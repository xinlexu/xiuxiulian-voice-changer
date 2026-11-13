"""Audio plumbing: ring buffers, adaptive output sinks and device backends."""
import logging
import re
import sys
import threading
import time
from collections import deque

import numpy as np

log = logging.getLogger(__name__)
IS_WINDOWS = sys.platform == "win32"

VIRTUAL_PATTERN = re.compile(
    r"vb-audio|vb-cable|voicemeeter|virtual audio cable|cable input|cable output|"
    r"cable [a-d]\b|伴侣扬声器|伴侣麦克风|steam streaming|网易|virtual",
    re.I,
)
BROADCAST_PATTERN = re.compile(r"nvidia broadcast|rtx voice|nvidia rtx", re.I)


def db(value):
    return float(20 * np.log10(max(float(value), 1e-6)))


class Resampler:
    """Streaming windowed-sinc resampler (state carried across calls).

    Used when a device only works at its own Windows sample rate: chunks of any
    size go in, the matching number of output samples come out, no clicks at
    chunk borders."""

    PHASES = 1024

    def __init__(self, src_sr, dst_sr, taps=16, beta=8.0):
        self.src_sr = int(src_sr)
        self.dst_sr = int(dst_sr)
        self.step = self.src_sr / float(self.dst_sr)
        self.taps = taps
        cut = min(1.0, self.dst_sr / float(self.src_sr)) * 0.94
        offs = np.arange(-taps + 1, taps + 1, dtype=np.float64)
        self.offs = offs.astype(np.int64)
        # filter taps for PHASES fractional positions (Kaiser-windowed sinc, unity DC gain)
        frac = np.arange(self.PHASES + 1, dtype=np.float64) / self.PHASES
        d = offs[None, :] - frac[:, None]
        w = np.sinc(cut * d) * np.i0(beta * np.sqrt(np.clip(1 - (d / taps) ** 2, 0, 1)))
        self.table = (w / w.sum(axis=1, keepdims=True)).astype(np.float32)
        self.hist = np.zeros(taps, dtype=np.float32)
        self.pos = float(taps)

    def process(self, x):
        x = np.asarray(x, dtype=np.float32)
        if x.size:
            self.hist = np.concatenate([self.hist, x])
        T = self.taps
        count = int(np.floor((len(self.hist) - 1 - T - self.pos) / self.step)) + 1
        if count <= 0:
            return np.zeros(0, dtype=np.float32)
        t = self.pos + np.arange(count) * self.step
        base = np.floor(t).astype(np.int64)
        w = self.table[np.rint((t - base) * self.PHASES).astype(np.int64)]
        y = np.einsum("ij,ij->i", self.hist[base[:, None] + self.offs[None, :]], w)
        self.pos = float(t[-1] + self.step)
        drop = max(0, int(np.floor(self.pos)) - T + 1)
        self.hist = self.hist[drop:]
        self.pos -= drop
        return y.astype(np.float32)


class PullResampler:
    """Output side: the device asks for N frames at its rate, we pull engine-rate audio."""

    def __init__(self, fill, src_sr, dst_sr):
        self.fill_src = fill
        self.r = Resampler(src_sr, dst_sr)
        self.out = np.zeros(0, dtype=np.float32)

    def fill(self, frames):
        while self.out.shape[0] < frames:
            need = int(np.ceil((frames - self.out.shape[0]) * self.r.step)) + 4
            self.out = np.concatenate([self.out, self.r.process(self.fill_src(need))])
        data, self.out = self.out[:frames], self.out[frames:]
        return data


class Ring:
    """Single-producer/single-consumer float32 ring with a lock (tiny critical sections)."""

    def __init__(self, capacity):
        self.buf = np.zeros(int(capacity), dtype=np.float32)
        self.capacity = int(capacity)
        self.start = 0
        self.size = 0
        self.lock = threading.Lock()
        self.overflows = 0

    def level(self):
        return self.size

    def clear(self):
        with self.lock:
            self.start = 0
            self.size = 0

    def write(self, data):
        data = np.asarray(data, dtype=np.float32).reshape(-1)
        n = data.shape[0]
        if n == 0:
            return
        with self.lock:
            if n >= self.capacity:
                data = data[-self.capacity:]
                n = self.capacity
                self.start = 0
                self.size = 0
            overflow = self.size + n - self.capacity
            if overflow > 0:  # drop the oldest audio rather than block a realtime thread
                self.start = (self.start + overflow) % self.capacity
                self.size -= overflow
                self.overflows += 1
            end = (self.start + self.size) % self.capacity
            first = min(n, self.capacity - end)
            self.buf[end:end + first] = data[:first]
            if first < n:
                self.buf[: n - first] = data[first:]
            self.size += n

    def read(self, n, out=None):
        """Read up to n samples; returns the number actually copied into out."""
        with self.lock:
            take = min(int(n), self.size)
            if out is None:
                out = np.zeros(int(n), dtype=np.float32)
            first = min(take, self.capacity - self.start)
            out[:first] = self.buf[self.start:self.start + first]
            if first < take:
                out[first:take] = self.buf[: take - first]
            self.start = (self.start + take) % self.capacity
            self.size -= take
            return out, take

    def peek(self, n):
        """Copy of the oldest n samples without consuming them."""
        with self.lock:
            take = min(int(n), self.size)
            out = np.empty(take, dtype=np.float32)
            first = min(take, self.capacity - self.start)
            out[:first] = self.buf[self.start:self.start + first]
            if first < take:
                out[first:] = self.buf[: take - first]
            return out

    def discard(self, n):
        with self.lock:
            n = min(int(n), self.size)
            self.start = (self.start + n) % self.capacity
            self.size -= n
            return n


class OutputSink:
    """One playback device fed block-wise by the worker.

    Keeps a small adaptive safety margin ("jitter buffer"): an underrun inserts
    ``target`` of silence and raises the target a little; a long quiet period
    without underruns lowers it again; when the queue holds more than needed
    (clock drift, a late burst) the excess is trimmed from quiet blocks so the
    latency never creeps up.
    """

    def __init__(self, name, sr, target_ms=20.0, min_ms=10.0, max_ms=160.0, gain=1.0, adaptive=True):
        self.name = name
        self.sr = sr
        self.ring = Ring(sr * 4)
        self.lock = threading.Lock()
        self.gain = float(gain)
        self.min_target = int(min_ms * sr / 1000)
        self.max_target = int(max_ms * sr / 1000)
        self.target = int(max(self.min_target, target_ms * sr / 1000))
        self.adaptive = adaptive
        self.pad = 0
        self.primed = False
        self.underruns = 0
        self.last_underrun = 0.0
        self.last_relax = time.monotonic()
        self.levels = deque()
        self.level_avg = 0.0
        self.trimmed = 0
        self.stream = None
        self.channels = 2
        self.device_latency = 0.0
        self._fade = None

    # ---- worker side -------------------------------------------------
    def push(self, block, quiet):
        now = time.monotonic()
        level = self.ring.level()
        if self.primed:
            self.levels.append((now, level))
            while self.levels and now - self.levels[0][0] > 1.5:
                self.levels.popleft()
            self.level_avg = 0.9 * self.level_avg + 0.1 * level
        with self.lock:
            if not self.primed:
                self.primed = True
                self.pad = self.target
            target = self.target
            if self.adaptive and now - self.last_underrun > 8.0 and now - self.last_relax > 5.0:
                if self.target > self.min_target:
                    self.target = max(self.min_target, int(self.target * 0.85))
                self.last_relax = now
        if quiet and len(self.levels) >= 3:
            floor = min(lv for _, lv in self.levels)
            excess = floor - target
            if excess > int(0.015 * self.sr):
                cut = min(excess, block.shape[0] // 2)
                block = block[: block.shape[0] - cut]
                self.trimmed += cut
                self.levels.clear()
        self.ring.write(block)

    def reprime(self):
        """After a deliberate pause (settings rebuild) start again without
        counting it as an underrun or growing the safety margin."""
        with self.lock:
            self.primed = False
            self.pad = 0
            self.ring.clear()
        self.levels.clear()
        self.level_avg = 0.0

    # ---- device side -------------------------------------------------
    def fill(self, frames):
        out = np.zeros(frames, dtype=np.float32)
        with self.lock:
            if not self.primed:
                return out
            pos = 0
            if self.pad > 0:
                pos = min(self.pad, frames)
                self.pad -= pos
            if pos < frames:
                _, got = self.ring.read(frames - pos, out[pos:])
                if got < frames - pos:
                    # Underrun: fade the tail we did get, then re-prime.
                    tail = min(got, 64)
                    if tail > 0:
                        out[pos + got - tail: pos + got] *= np.linspace(1, 0, tail, dtype=np.float32)
                    self.underruns += 1
                    self.last_underrun = time.monotonic()
                    if self.adaptive:
                        self.target = min(self.max_target, self.target + int(0.010 * self.sr))
                    self.pad = self.target
        if self.gain != 1.0:
            out *= self.gain
        return out

    def queue_ms(self):
        return 1000.0 * self.level_avg / self.sr

    def target_ms(self):
        return 1000.0 * self.target / self.sr


# ---------------------------------------------------------------------------
# Device backends
# ---------------------------------------------------------------------------

_SC = {"module": None, "error": None, "lock": threading.Lock()}


def _soundcard():
    """Import the bundled SoundCard library (direct WASAPI via cffi) once.

    It initialises COM for the importing thread, so import it on a fresh
    thread that has no COM apartment yet."""
    with _SC["lock"]:
        if _SC["module"] is None and _SC["error"] is None:
            def load():
                try:
                    import soundcard.mediafoundation as mf

                    _SC["module"] = mf
                except Exception as error:  # not on Windows / not bundled
                    _SC["error"] = "%s: %s" % (type(error).__name__, error)

            t = threading.Thread(target=load, name="soundcard-import", daemon=True)
            t.start()
            t.join(10)
            if _SC["module"] is None and _SC["error"] is None:
                _SC["error"] = "import timed out"
        if _SC["module"] is None:
            raise RuntimeError("直连 WASAPI 不可用（%s）" % _SC["error"])
        return _SC["module"]


class _Flags:
    def __init__(self, input_overflow=False, output_underflow=False):
        self.input_overflow = input_overflow
        self.output_underflow = output_underflow

    def __bool__(self):
        return self.input_overflow or self.output_underflow


_UNDERFLOW = _Flags(output_underflow=True)


class DirectWasapiStream:
    """A WASAPI shared-mode stream that talks to Windows directly (SoundCard
    library) instead of through PortAudio, for drivers where PortAudio's WASAPI
    stream refuses to start. Device lookup, the audio client, start/stop and all
    I/O live on one thread, so there is no cross-thread COM marshaling at all."""

    def __init__(self, kind, name, sr, channels, on_audio=None, source=None, note=None):
        self.kind = kind
        self.name = name
        self.sr = int(sr)
        self.channels = int(channels)
        self.on_audio = on_audio
        self.source = source
        self.note = note or (lambda status: None)
        self.latency = 0.01
        self.underflows = 0
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error = None
        self._thread = None

    def start(self):
        self._stop.clear()
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="wasapi-%s" % self.kind, daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            self._stop.set()
            raise RuntimeError("直连 WASAPI 启动超时")
        if self._error:
            raise RuntimeError(self._error)

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(2)

    def close(self):
        self.stop()

    def _device(self, mf):
        devices = mf.all_microphones() if self.kind == "input" else mf.all_speakers()
        for dev in devices:
            if dev.name == self.name:
                return dev
        for dev in devices:  # MME-style truncated names, renamed endpoints
            if self.name and (dev.name.startswith(self.name) or self.name.startswith(dev.name)):
                return dev
        raise LookupError("直连 WASAPI 找不到设备 %s" % self.name)

    def _run(self):
        import ctypes

        com = None
        try:
            mf = _soundcard()
            ole32 = ctypes.windll.ole32
            com = ole32.CoInitializeEx(None, 0)  # MTA, same as SoundCard itself
            from . import winutil

            winutil.mmcss_pro_audio()
            dev = self._device(mf)
            if self.kind == "input":
                self._capture(dev)
            else:
                self._render(mf, dev)
        except Exception as error:
            self._error = "%s: %s" % (type(error).__name__, error)
            if self._ready.is_set():
                log.error("direct WASAPI %s %s stopped: %s", self.kind, self.name, self._error)
            self._ready.set()
        finally:
            if com in (0, 1):
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass

    def _capture(self, dev):
        with dev.recorder(samplerate=self.sr, channels=self.channels, blocksize=int(self.sr * 0.05)) as rec:
            period = rec.deviceperiod[0]
            self.latency = period
            self._ready.set()
            while not self._stop.is_set():
                data = rec.record(None)
                self.note(None)
                if data.shape[0]:
                    mono = data[:, 0] if data.shape[1] == 1 else data.mean(axis=1)
                    self.on_audio(np.ascontiguousarray(mono, dtype=np.float32))

    def _render(self, mf, dev):
        ffi = mf._ffi
        ch = self.channels
        with dev.player(samplerate=self.sr, channels=ch, blocksize=int(self.sr * 0.06)) as player:
            size = player.buffersize
            period = player.deviceperiod[0]
            # keep ~2 engine periods queued in Windows: low latency, survives a late wake-up
            target = min(size, max(int(self.sr * 0.02), int(self.sr * period * 2)))
            step = max(1, int(self.sr * 0.002))
            self.latency = target / float(self.sr)
            self._ready.set()
            started = False
            while not self._stop.is_set():
                padding = player.currentpadding
                if started and padding == 0:
                    self.underflows += 1
                    self.note(_UNDERFLOW)
                free = min(size - padding, target - padding)
                if free < step:
                    time.sleep(0.0015)
                    continue
                self.note(None)
                data = np.asarray(self.source.fill(free), dtype=np.float32)
                if ch > 1:
                    data = np.repeat(data[:, None], ch, axis=1)
                raw = np.ascontiguousarray(data).tobytes()
                buf = player._render_buffer(free)
                ffi.memmove(buf[0], raw, len(raw))
                player._render_release(free)
                started = True


class SoundDeviceBackend:
    """Opens devices with a fallback chain so one picky driver can't block us:

      1. WASAPI shared, Windows-side sample-rate conversion (auto_convert), low latency
      2. WASAPI shared at the device's own Windows rate, we resample ("native")
      3. WASAPI through our own client instead of PortAudio ("direct") - on some
         PCs PortAudio's WASAPI streams open but refuse to start for one device
      4./5. PortAudio WASAPI with its default (safer) latency
      6. DirectSound            7. MME
    DirectSound/MME work everywhere but deliver audio in uneven bursts, which is
    what makes the voice stutter, so they are only the last resort.
    A stream that opened but failed to start is closed before the next attempt
    (otherwise the half-open stream keeps the device busy).
    The device list shown to the user always comes from WASAPI (full names).
    """

    PREFERRED_HOSTAPI = "Windows WASAPI"
    CHAIN = (
        ("Windows WASAPI", "low", "auto"),
        ("Windows WASAPI", "low", "native"),
        ("Windows WASAPI", "low", "direct"),  # our own WASAPI client (SoundCard), no PortAudio
        ("Windows WASAPI", "high", "auto"),
        ("Windows WASAPI", "high", "native"),
        ("Windows DirectSound", "low", None),
        ("MME", "low", None),
        ("*", "low", None),  # whatever host API the device list came from (non-Windows/dev)
    )
    API_LABEL = {"Windows WASAPI": "WASAPI", "Windows DirectSound": "DirectSound（兼容模式）", "MME": "MME（兼容模式）"}

    def __init__(self):
        import sounddevice as sd

        self.sd = sd
        self.preferred = {}  # (kind, name) -> index into CHAIN that worked last time
        self.last_api = {}   # (kind, name) -> label of the host API in use
        self.xruns = {}      # (kind, name) -> driver-reported overflows/underflows
        self.gaps = {}       # (kind, name) -> longest silence between callbacks (s)

    def refresh(self):
        try:
            self.sd._terminate()
            self.sd._initialize()
        except Exception:
            log.exception("PortAudio re-init failed")

    def _api(self, api_name):
        if api_name == "*":
            index, _ = self._hostapi_index()
            return index, self.sd.query_hostapis()[index]
        for i, api in enumerate(self.sd.query_hostapis()):
            if api["name"] == api_name:
                return i, api
        return None, None

    def _hostapi_index(self):
        index, api = self._api(self.PREFERRED_HOSTAPI)
        if api is not None:
            return index, api["name"]
        apis = self.sd.query_hostapis()
        default = self.sd.default.hostapi if self.sd.default.hostapi >= 0 else 0
        return default, apis[default]["name"]

    def list_devices(self):
        api_index, api_name = self._hostapi_index()
        api = self.sd.query_hostapis()[api_index]
        inputs, outputs = [], []
        for idx in api["devices"]:
            dev = self.sd.query_devices(idx)
            item = {
                "name": dev["name"],
                "samplerate": int(dev["default_samplerate"]),
                "virtual": bool(VIRTUAL_PATTERN.search(dev["name"])),
                "broadcast": bool(BROADCAST_PATTERN.search(dev["name"])),
            }
            if dev["max_input_channels"] > 0:
                inputs.append(dict(item, channels=dev["max_input_channels"], default=idx == api.get("default_input_device")))
            if dev["max_output_channels"] > 0:
                outputs.append(dict(item, channels=dev["max_output_channels"], default=idx == api.get("default_output_device")))
        return {"hostapi": api_name, "inputs": inputs, "outputs": outputs, "in_use": dict(
            ("%s:%s" % k, v) for k, v in self.last_api.items())}

    def _find(self, api_name, name, kind):
        """Device index for ``name`` inside one host API (MME truncates names to 31 chars)."""
        _, api = self._api(api_name)
        if api is None:
            return None, None
        key = "max_input_channels" if kind == "input" else "max_output_channels"
        candidates = [(i, self.sd.query_devices(i)) for i in api["devices"]]
        candidates = [(i, d) for i, d in candidates if d[key] > 0]
        if not name:
            default = api.get("default_input_device" if kind == "input" else "default_output_device", -1)
            for i, d in candidates:
                if i == default:
                    return i, d
            return candidates[0] if candidates else (None, None)
        for i, d in candidates:
            if d["name"] == name:
                return i, d
        for i, d in candidates:
            if len(d["name"]) >= 20 and (name.startswith(d["name"]) or d["name"] == name[: len(d["name"])]):
                return i, d
        return None, None

    def resolve(self, name, kind):
        for api_name, _, _ in self.CHAIN:
            index, dev = self._find(api_name, name, kind)
            if index is not None:
                return index, dev
        if name:
            raise LookupError("找不到设备：%s（设备被拔掉或改名了？点「刷新设备」再选一次）" % name)
        raise LookupError("没有可用的%s设备" % ("输入" if kind == "input" else "输出"))

    def _attempts(self, kind, name):
        order = list(range(len(self.CHAIN)))
        first = self.preferred.get((kind, name))
        if first is not None:
            order.remove(first)
            order.insert(0, first)
        return order

    def _open(self, kind, name, sr, make_stream):
        errors = []
        found_any = False
        for step in self._attempts(kind, name):
            api_name, latency, mode = self.CHAIN[step]
            index, dev = self._find(api_name, name, kind)
            if index is None:
                continue
            found_any = True
            extra = None
            rate = sr
            if api_name == "Windows WASAPI" and mode == "auto":
                try:
                    extra = self.sd.WasapiSettings(auto_convert=True)
                except TypeError:
                    extra = None
            elif mode == "native":
                rate = int(dev["default_samplerate"]) or sr
            elif mode == "direct" and not IS_WINDOWS:
                continue
            max_ch = int(dev["max_input_channels" if kind == "input" else "max_output_channels"])
            layouts = (1, min(2, max_ch)) if kind == "input" else (min(2, max_ch), 1)
            for channels in dict.fromkeys(c for c in layouts if c >= 1):
                stream = None
                try:
                    stream = make_stream(index, channels, latency, extra, rate, mode, dev["name"])
                    stream.start()
                    self.preferred[(kind, name)] = step
                    label = self.API_LABEL.get(api_name, api_name)
                    if mode == "direct":
                        label += "（直连）"
                    if rate != sr:
                        label += "（%d Hz）" % rate
                    self.last_api[(kind, name or dev["name"])] = label
                    if errors:
                        log.warning("%s %s: opened via %s/%s/%s %d Hz after: %s", kind, dev["name"], api_name, latency,
                                    mode, rate, " | ".join(errors))
                    else:
                        log.info("%s %s: %s latency=%s ch=%d rate=%d", kind, dev["name"], api_name, latency, channels, rate)
                    return stream, dev
                except Exception as error:
                    errors.append("%s/%s/%s/%dch/%dHz: %s" % (api_name, latency, mode, channels, rate,
                                                             str(error).split("[Windows")[0].strip()))
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass
        if not found_any:
            raise LookupError("找不到设备：%s（设备被拔掉或改名了？点「刷新设备」再选一次）" % name)
        log.error("%s %s: every attempt failed: %s", kind, name, " | ".join(errors))
        what = "麦克风" if kind == "input" else "输出设备"
        raise RuntimeError(
            "%s「%s」打不开。可能被别的程序独占了：Windows 声音设置 → 该设备 → 属性 → 高级，"
            "取消勾选「允许应用程序独占控制此设备」，或者先关掉正在用它的软件，再试一次。" % (what, name)
        )

    def _watch(self, kind, name):
        """Per-stream glitch bookkeeping shared by the callbacks."""
        key = (kind, name)
        counts = {"xruns": 0, "last": None}
        self.xruns[key] = 0
        self.gaps[key] = 0.0

        def note(status):
            now = time.perf_counter()
            last = counts["last"]
            if last is not None and now - last > self.gaps[key]:
                self.gaps[key] = now - last
            counts["last"] = now
            if status:
                bad = status.input_overflow if kind == "input" else status.output_underflow
                if bad:
                    self.xruns[key] += 1

        return note

    def reset_stats(self):
        for key in list(self.gaps):
            self.gaps[key] = 0.0

    def open_input(self, name, sr, on_audio):
        note = self._watch("input", name)

        def make(index, channels, latency, extra, rate, mode, dev_name):
            if mode == "direct":
                return DirectWasapiStream("input", dev_name, sr, channels, on_audio=on_audio, note=note)
            resampler = Resampler(rate, sr) if rate != sr else None

            def callback(indata, frames, time_info, status):
                note(status)
                mono = indata[:, 0] if indata.shape[1] == 1 else indata.mean(axis=1)
                on_audio(resampler.process(mono) if resampler else mono)

            return self.sd.InputStream(
                device=index, samplerate=rate, channels=channels, dtype="float32",
                blocksize=0, latency=latency, extra_settings=extra, callback=callback,
            )

        stream, dev = self._open("input", name, sr, make)
        return stream, dev["name"], float(stream.latency)

    def open_output(self, name, sr, sink):
        note = self._watch("output", name)

        def make(index, channels, latency, extra, rate, mode, dev_name):
            if mode == "direct":
                return DirectWasapiStream("output", dev_name, sr, channels, source=sink, note=note)
            source = PullResampler(sink.fill, sr, rate) if rate != sr else sink

            def callback(outdata, frames, time_info, status):
                note(status)
                data = source.fill(frames)
                if channels == 1:
                    outdata[:, 0] = data
                else:
                    outdata[:] = data[:, None]

            return self.sd.OutputStream(
                device=index, samplerate=rate, channels=channels, dtype="float32",
                blocksize=0, latency=latency, extra_settings=extra, callback=callback,
            )

        stream, dev = self._open("output", name, sr, make)
        sink.stream = stream
        sink.channels = stream.channels
        sink.device_latency = float(stream.latency)
        return stream, dev["name"]

    def probe(self, name, kind, sr, seconds=0.4):
        """Open a device briefly and check that audio actually flows (self-test)."""
        import time as _time

        count = {"n": 0}
        if kind == "input":
            stream, _, _ = self.open_input(name, sr, lambda chunk: count.__setitem__("n", count["n"] + 1))
        else:
            class _Silence:
                def fill(self, frames):
                    count["n"] += 1
                    return np.zeros(frames, dtype=np.float32)

            sink = _Silence()
            stream, _ = self.open_output(name, sr, sink)
        try:
            _time.sleep(seconds)
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        label = self.last_api.get((kind, name), "")
        return {"ok": count["n"] > 0, "api": label, "callbacks": count["n"]}


class FakeStream:
    def __init__(self, stopper):
        self._stop = stopper
        self.latency = 0.005

    def stop(self):
        self._stop()

    def close(self):
        self._stop()


class FileBackend:
    """Test backend: plays a wav into the engine at (optionally accelerated)
    real time and records every output device."""

    def __init__(self, wav, sr, speed=1.0, period=480, jitter=None, loop=False):
        self.wav = np.asarray(wav, dtype=np.float32)
        self.loop = loop
        self.sr = sr
        self.speed = speed
        self.period = period
        self.outputs = {}
        self.jitter = jitter
        self.done = threading.Event()

    def refresh(self):
        pass

    def list_devices(self):
        dev = {"samplerate": self.sr, "virtual": False, "broadcast": False, "channels": 1, "default": True}
        return {
            "hostapi": "Fake",
            "inputs": [dict(dev, name="Test Mic")],
            "outputs": [dict(dev, name="Test Cable", virtual=True), dict(dev, name="Test Headphones")],
        }

    def _clock(self, tick, stop_event):
        interval = self.period / self.sr / self.speed
        next_t = time.perf_counter()
        while not stop_event.is_set():
            tick()
            next_t += interval
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)

    def open_input(self, name, sr, on_audio):
        stop = threading.Event()
        pos = {"i": 0}

        def tick():
            i = pos["i"]
            if self.loop and i >= self.wav.shape[0]:
                i = 0
            if i >= self.wav.shape[0]:
                chunk = np.zeros(self.period, dtype=np.float32)
                if i >= self.wav.shape[0] + sr:  # one second of trailing silence
                    self.done.set()
            else:
                chunk = self.wav[i:i + self.period]
                if chunk.shape[0] < self.period:
                    chunk = np.pad(chunk, (0, self.period - chunk.shape[0]))
            pos["i"] = i + self.period
            on_audio(chunk)

        threading.Thread(target=self._clock, args=(tick, stop), daemon=True).start()
        return FakeStream(stop.set), name or "Test Mic", 0.005

    def probe(self, name, kind, sr, seconds=0.2):
        return {"ok": True, "api": "Fake", "callbacks": 1}

    def open_output(self, name, sr, sink):
        stop = threading.Event()
        record = self.outputs.setdefault(name or "Test Cable", [])

        def tick():
            record.append(sink.fill(self.period))

        threading.Thread(target=self._clock, args=(tick, stop), daemon=True).start()
        stream = FakeStream(stop.set)
        sink.stream = stream
        sink.device_latency = 0.005
        return stream, name or "Test Cable"
