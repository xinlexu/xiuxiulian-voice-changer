"""Realtime voice engine.

Pipeline per block (adapted from RVC-Project realtime_gui.py, MIT License):
  mic -> input ring -> [worker] gate -> 16 kHz -> HuBERT + pitch -> RVC synth
      -> loudness follow -> SOLA crossfade -> output sinks (game cable, monitor)

Differences from the reference GUI that matter for games:
  * Audio devices and inference are decoupled through rings. Inference spikes
    are absorbed by a small adaptive buffer instead of cracking the audio.
  * Every device runs at 48 kHz through WASAPI auto-conversion, so any mic and
    any output can be combined (no MME/DirectSound fallback, no rate errors).
  * The game cable and the monitor headphones play at the same time.
  * Auto-tune: when the GPU is busy (a game at 100%) the engine trades a bit of
    quality/latency for stability instead of stuttering.
  * Pitch, formant, gate, gains and the voice model change live.
"""
import contextlib
import logging
import math
import os
import threading
import time
from collections import deque

import numpy as np
import torch
import torch.nn.functional as F

from .audio import OutputSink, Ring, db
from .rvc import GPU_LOCK, RealtimeRVC, SharedModels, VoiceModel
from .vendor.rvc import cuda_graph
from . import winutil

log = logging.getLogger(__name__)

# CUDA Graphs stay off: a capture that fails (e.g. because another thread touched
# the GPU at that moment) leaves PyTorch's allocator broken for the whole process.
# Eager mode is fast enough on any recent NVIDIA card.
os.environ[cuda_graph.ENV_NAME] = "0"

ENGINE_SR = 48000

# "extra_time" is how much past audio the model sees on every block. It does not
# add latency, but too little of it makes consonants mushy, so the presets give the
# model plenty on a GPU (a 5080 handles 1.5 s of context in ~30 ms).
PRESETS = {
    # 低延迟：给游戏用。
    "game": {"block_time": 0.12, "crossfade_time": 0.03, "extra_time": 1.00, "f0_method": "rmvpe"},
    # 均衡：音质和延迟折中。
    "balanced": {"block_time": 0.17, "crossfade_time": 0.04, "extra_time": 1.50, "f0_method": "rmvpe"},
    # 高音质：录音、唱歌。
    "quality": {"block_time": 0.26, "crossfade_time": 0.05, "extra_time": 2.50, "f0_method": "rmvpe"},
}

DEFAULTS = {
    "model": "",
    "pitch": 10,
    "formant": 0.0,
    "index_rate": 0.0,
    "preset": "game",
    "f0_method": "rmvpe",
    "block_time": 0.12,
    "crossfade_time": 0.03,
    "extra_time": 1.00,
    "threshold": -60.0,
    "rms_mix_rate": 0.0,
    "input_device": "",
    "output_device": "",
    "monitor_enabled": False,
    "monitor_device": "",
    "monitor_volume": 0.8,
    "input_gain_db": 0.0,
    "output_gain_db": 0.0,
    "auto_tune": True,
    "min_buffer_ms": 15.0,
    "target_f0": 220.0,
    "gpu_priority": True,
    "v": 2,
}

LIVE_KEYS = {
    "pitch", "formant", "index_rate", "threshold", "rms_mix_rate", "input_gain_db",
    "output_gain_db", "monitor_volume", "auto_tune", "target_f0", "min_buffer_ms",
}
REBUILD_KEYS = {"block_time", "crossfade_time", "extra_time", "f0_method", "preset"}
STREAM_KEYS = {"input_device", "output_device"}


def clamp(value, low, high):
    return max(low, min(high, value))


def sanitize(cfg):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULTS})
    for key in ("pitch", "formant", "index_rate", "block_time", "crossfade_time", "extra_time",
                "threshold", "rms_mix_rate", "monitor_volume", "input_gain_db", "output_gain_db",
                "min_buffer_ms", "target_f0"):
        try:
            out[key] = float(out[key])
        except (TypeError, ValueError):
            out[key] = DEFAULTS[key]
    out["pitch"] = clamp(out["pitch"], -24, 24)
    out["formant"] = clamp(out["formant"], -6, 6)
    out["index_rate"] = clamp(out["index_rate"], 0, 1)
    out["block_time"] = clamp(out["block_time"], 0.06, 0.6)
    out["crossfade_time"] = clamp(out["crossfade_time"], 0.01, 0.08)
    out["extra_time"] = clamp(out["extra_time"], 0.2, 3.0)
    out["threshold"] = clamp(out["threshold"], -90, -20)
    out["rms_mix_rate"] = clamp(out["rms_mix_rate"], 0, 1)
    out["monitor_volume"] = clamp(out["monitor_volume"], 0, 1.5)
    out["input_gain_db"] = clamp(out["input_gain_db"], -20, 20)
    out["output_gain_db"] = clamp(out["output_gain_db"], -20, 20)
    out["min_buffer_ms"] = clamp(out["min_buffer_ms"], 5, 120)
    out["target_f0"] = clamp(out["target_f0"], 80, 400)
    if out["f0_method"] not in ("rmvpe", "fcpe"):
        out["f0_method"] = "rmvpe"
    if out["preset"] not in PRESETS and out["preset"] != "custom":
        out["preset"] = "custom"
    for key in ("monitor_enabled", "auto_tune", "gpu_priority"):
        out[key] = bool(out[key])
    for key in ("model", "input_device", "output_device", "monitor_device"):
        out[key] = str(out[key] or "")
    return out


def is_gpu_fault(error):
    text = str(error)
    return "CUDA error" in text or "INTERNAL ASSERT" in text or "CUBLAS_STATUS" in text or "cuDNN error" in text


def friendly_error(error, prefix=""):
    text = str(error)
    if is_gpu_fault(error):
        return "显卡运行出错了，请点右上角退出，再重新打开羞羞脸变声。（%s）" % text.splitlines()[0][:160]
    return ("%s：%s" % (prefix, text)) if prefix else text


def pick_device():
    if torch.cuda.is_available():
        name = torch.cuda.get_device_name(0)
        upper = name.upper()
        slow_half = any(tag in upper for tag in ("GTX 16", "P40", "P10", "1060", "1070", "1080", "T4 "))
        return torch.device("cuda:0"), not slow_half, name
    return torch.device("cpu"), False, "CPU"


class Engine:
    def __init__(self, root, backend_factory, resolve_model, sr=ENGINE_SR):
        self.root = root
        self.backend_factory = backend_factory
        self.backend = None
        self.resolve_model = resolve_model
        self.sr = sr
        self.lock = threading.RLock()
        self.cfg = dict(DEFAULTS)
        self.state = "idle"  # idle | loading | ready | starting | running | error
        self.error = ""
        self.device, self.is_half, self.gpu_name = pick_device()
        if self.device.type == "cuda":
            torch.backends.cudnn.benchmark = True
            torch.set_num_threads(2)
        else:
            torch.set_num_threads(max(1, min(4, (os.cpu_count() or 4) // 2)))
        self.shared = SharedModels(root, self.device, self.is_half)
        self.voice = None
        self.voice_key = ""
        self.voice_loading = ""
        self.pending_voice = None
        self.rt = None
        self.sinks = {}
        self.in_stream = None
        self.in_name = ""
        self.in_latency = 0.0
        self.worker = None
        self.stop_event = threading.Event()
        self.cond = threading.Condition()
        self.input_ring = Ring(sr * 4)
        self.need_rebuild = False
        self.calib_chunks = None
        self.events = deque(maxlen=30)
        self.event_seq = 0
        self.adapt = {}
        self.adapt_note = ""
        self.gpu_priority = None
        self.boost = {}
        self.cuda_stream = None
        self.preload_thread = None
        self.time_scale = 1.0  # tests drive audio slower than real time
        self.gpu_broken = ""
        self.calib_lock = threading.Lock()
        self.selftest_report = None
        self.rec = None  # a pitch test with its own mic stream vs. start()
        self._reset_metrics()

    # ------------------------------------------------------------------ utils
    def _event(self, level, text):
        self.event_seq += 1
        self.events.append({"id": self.event_seq, "level": level, "text": text, "t": time.time()})
        (log.warning if level == "warn" else log.info)("event: %s", text)

    def _reset_metrics(self):
        self.proc_times = deque(maxlen=60)
        self.stage_times = {}
        self.in_db = -120.0
        self.out_db = -120.0
        self.gate_open = False
        self.overload_drops = 0
        self.blocks = 0
        self.load_low_since = None
        self.last_adjust = 0.0
        self.recent_underruns = deque()
        self.errors_in_row = 0
        self.sola_offset_ms = 0.0
        self.health = {"since": time.monotonic(), "under": {}, "xruns": {}, "late": 0, "cut": 0, "cut_ms": 0.0,
                       "skipped_ms": 0.0}
        self.health_log = deque()

    def get_backend(self):
        if self.backend is None:
            self.backend = self.backend_factory()
        return self.backend

    def active(self):
        cfg = self.cfg
        return {
            "block_time": self.adapt.get("block_time", cfg["block_time"]),
            "extra_time": self.adapt.get("extra_time", cfg["extra_time"]),
            "crossfade_time": cfg["crossfade_time"],
            "f0_method": self.adapt.get("f0_method", cfg["f0_method"]),
        }

    # ---------------------------------------------------------- configuration
    def configure(self, cfg):
        """Replace config while idle (used at startup)."""
        with self.lock:
            self.cfg = sanitize(cfg)
            if self.cfg["preset"] in PRESETS:
                self.cfg.update(PRESETS[self.cfg["preset"]])

    def update(self, patch):
        """Apply a partial config. Returns the sanitized config."""
        with self.lock:
            patch = dict(patch or {})
            if patch.get("preset") in PRESETS:
                patch.update(PRESETS[patch["preset"]])
            elif any(k in patch for k in ("block_time", "extra_time", "crossfade_time", "f0_method")) and "preset" not in patch:
                patch["preset"] = "custom"
            before = dict(self.cfg)
            self.cfg = sanitize(dict(self.cfg, **patch))
            changed = {k for k in self.cfg if self.cfg[k] != before.get(k)}
            running = self.state == "running"
            if changed & REBUILD_KEYS:
                self.adapt = {}
                self.adapt_note = ""
                if running:
                    self.need_rebuild = True
            if "output_gain_db" in changed or "monitor_volume" in changed:
                self._apply_gains()
            if "min_buffer_ms" in changed:
                for sink in self.sinks.values():
                    sink.min_target = int(self.cfg["min_buffer_ms"] * self.sr / 1000)
            if "model" in changed and self.cfg["model"]:
                self.load_voice_async(self.cfg["model"])
            if "gpu_priority" in changed and self.cfg["gpu_priority"]:
                self.gpu_priority = winutil.set_gpu_priority()
            restart_streams = running and bool(changed & STREAM_KEYS)
            monitor_change = running and bool(changed & {"monitor_enabled", "monitor_device"})
        if restart_streams:
            self.restart()
        elif monitor_change:
            self._sync_monitor()
        return dict(self.cfg)

    def _apply_gains(self):
        main_gain = 10 ** (self.cfg["output_gain_db"] / 20)
        if "main" in self.sinks:
            self.sinks["main"].gain = main_gain
        if "monitor" in self.sinks:
            self.sinks["monitor"].gain = main_gain * self.cfg["monitor_volume"]

    # ---------------------------------------------------------------- models
    def preload(self):
        """Warm the heavy models in the background so 'start' is instant."""
        def run():
            try:
                with self.lock:
                    if self.state == "idle":
                        self.state = "loading"
                self.shared.ensure_hubert()
                self.shared.ensure_f0(self.cfg["f0_method"])
                if self.cfg["model"]:
                    self._ensure_voice(self.cfg["model"])
                self.selftest()
            except Exception as error:
                log.exception("preload failed")
                self._event("warn", "模型预加载失败：%s" % friendly_error(error))
            finally:
                with self.lock:
                    if self.state == "loading":
                        self.state = "ready"

        self.preload_thread = threading.Thread(target=run, name="preload", daemon=True)
        self.preload_thread.start()

    def _load_voice(self, key):
        pth, index = self.resolve_model(key)
        return VoiceModel(pth, self.device, self.is_half, index_path=index)

    def _ensure_voice(self, key):
        with self.lock:
            if self.voice is not None and self.voice_key == key:
                return self.voice
        voice = self._load_voice(key)
        with self.lock:
            old = self.voice
            self.voice, self.voice_key = voice, key
        if old is not None and old is not voice:
            old.release()
        return voice

    def load_voice_async(self, key):
        if self.voice_key == key and self.voice is not None:
            return
        self.voice_loading = key

        def run():
            try:
                voice = self._load_voice(key)
            except Exception as error:
                log.exception("voice load failed")
                self._event("warn", "模型加载失败：%s" % error)
                self.voice_loading = ""
                return
            with self.lock:
                if self.cfg["model"] != key:
                    voice.release()
                    return
                if self.state in ("running", "starting"):
                    self.pending_voice = (key, voice)  # the worker swaps it between blocks
                else:
                    old = self.voice
                    self.voice, self.voice_key = voice, key
                    if old is not None:
                        old.release()
                    self.voice_loading = ""

        threading.Thread(target=run, name="voice-load", daemon=True).start()

    # ------------------------------------------------------------- lifecycle
    def start(self, patch=None):
        if patch:
            self.update(patch)
        with self.lock:
            if self.state == "running":
                return
            if self.gpu_broken:
                raise RuntimeError(self.gpu_broken)
            if not self.cfg["model"]:
                raise RuntimeError("请先选择一个声音模型")
            if not self.cfg["output_device"]:
                raise RuntimeError("请先选择输出设备（给游戏用一般选 VB-Audio 的「伴侣扬声器」/ CABLE Input）")
            self.state = "starting"
            self.error = ""
        try:
            if self.preload_thread is not None:
                self.preload_thread.join()
            if self.cfg["gpu_priority"] and self.gpu_priority is None:
                self.gpu_priority = winutil.set_gpu_priority()
            self.shared.ensure_hubert()
            self.shared.ensure_f0(self.active()["f0_method"])
            self._ensure_voice(self.cfg["model"])
            self.voice_loading = ""
            if self.device.type == "cuda" and self.cuda_stream is None:
                self.cuda_stream = torch.cuda.Stream(device=self.device, priority=-1)
            self._reset_metrics()
            with GPU_LOCK, self._stream_ctx():
                self._alloc()
                self._warmup()
            with self.calib_lock:  # let a running pitch test finish with its own stream first
                self._open_streams()
            self.stop_event.clear()
            self.worker = threading.Thread(target=self._worker, name="voice-worker", daemon=True)
            self.worker.start()
            with self.lock:
                self.state = "running"
            act = self.active()
            self._event("info", "变声已开启（%d ms 分块，%s）" % (act["block_time"] * 1000, act["f0_method"].upper()))
        except Exception as error:
            log.exception("start failed")
            self._close_streams()
            with self.lock:
                self.state = "error"
                self.error = friendly_error(error)
                if is_gpu_fault(error):
                    self.gpu_broken = self.error
            raise RuntimeError(self.error) from error

    def stop(self):
        with self.lock:
            was = self.state
        self.stop_event.set()
        with self.cond:
            self.cond.notify_all()
        if self.worker is not None:
            self.worker.join(timeout=3)
            self.worker = None
        self._close_streams()
        with self.lock:
            self.state = "ready"
            self.need_rebuild = False
        if was == "running":
            self._event("info", "变声已停止")

    def restart(self):
        self.stop()
        self.start()

    def _stream_ctx(self):
        if self.cuda_stream is not None:
            return torch.cuda.stream(self.cuda_stream)
        return contextlib.nullcontext()

    def _open_streams(self):
        backend = self.get_backend()
        backend.refresh()
        self.input_ring.clear()
        min_ms = self.cfg["min_buffer_ms"]
        main = OutputSink("main", self.sr, target_ms=max(min_ms, 20), min_ms=min_ms)
        _, out_name = backend.open_output(self.cfg["output_device"], self.sr, main)
        self.sinks = {"main": main}
        self._sync_monitor()
        self._apply_gains()
        self.in_stream, self.in_name, self.in_latency = backend.open_input(
            self.cfg["input_device"], self.sr, self._on_input
        )
        log.info("streams: in=%s out=%s", self.in_name, out_name)

    def _sync_monitor(self):
        want = self.cfg["monitor_enabled"] and bool(self.cfg["monitor_device"])
        current = self.sinks.get("monitor")
        if current is not None and (not want or current.name != self.cfg["monitor_device"]):
            self.sinks.pop("monitor", None)
            try:
                current.stream.stop()
                current.stream.close()
            except Exception:
                pass
            current = None
        if want and current is None:
            if self.cfg["monitor_device"] == self.cfg["output_device"]:
                self._event("warn", "监听设备和输出设备相同，已忽略监听")
                return
            sink = OutputSink(self.cfg["monitor_device"], self.sr, target_ms=30, min_ms=self.cfg["min_buffer_ms"])
            try:
                self.get_backend().open_output(self.cfg["monitor_device"], self.sr, sink)
                self.sinks["monitor"] = sink
            except Exception as error:
                self._event("warn", "监听设备打不开：%s" % error)
        self._apply_gains()

    def _close_streams(self):
        streams = [self.in_stream] + [s.stream for s in self.sinks.values()]
        self.in_stream = None
        self.sinks = {}
        for stream in streams:
            if stream is None:
                continue
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _on_input(self, chunk):
        self.input_ring.write(chunk)
        if self.calib_chunks is not None:
            self.calib_chunks.append(np.array(chunk, dtype=np.float32))
        if self.input_ring.level() >= self.block_frame:
            with self.cond:
                self.cond.notify()

    # ----------------------------------------------------------- DSP state
    def _alloc(self):
        act = self.active()
        sr = self.sr
        dev = self.device
        self.zc = zc = sr // 100
        self.block_frame = int(round(act["block_time"] * sr / zc)) * zc
        self.block_frame_16k = 160 * self.block_frame // zc
        self.sola_buffer_frame = int(round(min(act["crossfade_time"], 0.04) * sr / zc)) * zc
        self.crossfade_frame = self.sola_buffer_frame
        self.sola_search_frame = zc
        self.extra_frame = int(round(act["extra_time"] * sr / zc)) * zc
        total = self.extra_frame + self.crossfade_frame + self.sola_search_frame + self.block_frame
        self.input_wav = torch.zeros(total, device=dev, dtype=torch.float32)
        self.input_wav_res = torch.zeros(160 * total // zc, device=dev, dtype=torch.float32)
        self.sola_buffer = torch.zeros(self.sola_buffer_frame, device=dev, dtype=torch.float32)
        self.sola_den_kernel = torch.ones(1, 1, self.sola_buffer_frame, device=dev, dtype=torch.float32)
        self.skip_head = self.extra_frame // zc
        self.return_length = (self.block_frame + self.sola_buffer_frame + self.sola_search_frame) // zc
        t = torch.linspace(0.0, 1.0, steps=self.sola_buffer_frame, device=dev, dtype=torch.float32)
        self.fade_in_window = torch.sin(0.5 * np.pi * t) ** 2
        self.fade_out_window = 1 - self.fade_in_window
        import torchaudio.transforms as tat

        self.resampler = tat.Resample(orig_freq=sr, new_freq=16000, dtype=torch.float32).to(dev)
        self._make_output_resampler()
        self.rt = RealtimeRVC(self.shared, self.voice)
        self.f0_active = act["f0_method"]
        self.shared.ensure_f0(self.f0_active)
        # noise gate state (10 ms frames, 40 ms rms window, 200 ms hold)
        self.gate_hist = np.zeros(3 * zc, dtype=np.float32)
        self.gate_gain = 0.0
        self.gate_hold = 0
        self.gate_frames_total = 0
        self.gate_frames_open = 0
        log.info(
            "alloc: block=%d extra=%d sola=%d f0=%s voice=%s/%d",
            self.block_frame, self.extra_frame, self.sola_buffer_frame, self.f0_active,
            self.voice.version, self.voice.tgt_sr,
        )

    def _make_output_resampler(self):
        import torchaudio.transforms as tat

        if self.voice.tgt_sr != self.sr:
            self.resampler2 = tat.Resample(
                orig_freq=self.voice.tgt_sr, new_freq=self.sr, dtype=torch.float32
            ).to(self.device)
        else:
            self.resampler2 = None

    def _warmup(self):
        started = time.perf_counter()
        n = self.block_frame
        phase = np.arange(n * 4) / self.sr
        probe = (0.05 * np.sin(2 * np.pi * 140 * phase)).astype(np.float32)
        for i in range(4):
            self._process(probe[i * n:(i + 1) * n], warm=True)
        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)
        self.input_wav.zero_()
        self.input_wav_res.zero_()
        self.sola_buffer.zero_()
        self.rt.reset()
        self.gate_hist[:] = 0
        log.info("warmup %.0f ms", (time.perf_counter() - started) * 1000)

    # ---------------------------------------------------------------- DSP
    GATE_LOOKAHEAD = 3   # frames (30 ms): open before a quiet consonant, not after it
    GATE_HOLD = 25       # frames (250 ms): don't close inside a word
    GATE_RELEASE = 4     # frames (40 ms) fade-out

    def _gate(self, block):
        threshold = self.cfg["threshold"]
        zc = self.zc
        if threshold <= -89:
            self.gate_open = True
            return block
        data = np.concatenate([self.gate_hist, block])
        frames = block.shape[0] // zc
        windows = data[: (frames + 3) * zc].reshape(frames + 3, zc)
        energy = np.mean(windows * windows, axis=1)
        # rms over a 40 ms window ending at each frame of this block
        rms_db = 10 * np.log10(np.convolve(energy, np.ones(4) / 4, mode="valid")[:frames] + 1e-12)
        loud = rms_db >= threshold
        ahead = loud.copy()
        for k in range(1, self.GATE_LOOKAHEAD + 1):
            ahead[:-k] |= loud[k:]
        self.gate_frames_total += frames
        self.gate_frames_open += int(np.count_nonzero(ahead))
        out = block.copy()
        gain = self.gate_gain
        for i in range(frames):
            if ahead[i]:
                self.gate_hold = self.GATE_HOLD
            elif self.gate_hold > 0:
                self.gate_hold -= 1
            target = 1.0 if self.gate_hold > 0 else max(0.0, gain - 1.0 / self.GATE_RELEASE)
            if target != gain:
                out[i * zc:(i + 1) * zc] *= np.linspace(gain, target, zc, endpoint=False, dtype=np.float32)
            elif gain == 0.0:
                out[i * zc:(i + 1) * zc] = 0.0
            gain = target
        self.gate_gain = gain
        self.gate_hist = data[-3 * zc:].copy()
        self.gate_open = gain > 0.5
        return out

    def _frame_rms(self, x, length):
        zc = self.zc
        padded = F.pad(x.view(1, 1, -1) ** 2, (2 * zc, 2 * zc))
        rms = torch.sqrt(F.avg_pool1d(padded, kernel_size=4 * zc, stride=zc) + 1e-12)
        return F.interpolate(rms, size=length + 1, mode="linear", align_corners=True)[0, 0, :-1]

    def _process(self, block, warm=False):
        cfg = self.cfg
        if cfg["input_gain_db"] != 0:
            block = block * (10 ** (cfg["input_gain_db"] / 20))
        if not warm:
            self.in_db = db(np.max(np.abs(block)) if block.size else 0)
            block = self._gate(block)
        bf = self.block_frame
        zc = self.zc
        dev = self.device
        self.input_wav[:-bf] = self.input_wav[bf:].clone()
        self.input_wav[-bf:] = torch.from_numpy(np.ascontiguousarray(block, dtype=np.float32)).to(dev)
        self.input_wav_res[:-self.block_frame_16k] = self.input_wav_res[self.block_frame_16k:].clone()
        resample_input = self.input_wav[-bf - 2 * zc:]
        self.input_wav_res[-160 * (bf // zc + 1):] = cuda_graph.run_cuda_graph(
            self.resampler, "in-resample", lambda audio: self.resampler(audio), resample_input
        )[160:]
        infer_wav = self.rt.infer(
            self.input_wav_res, self.block_frame_16k, self.skip_head, self.return_length,
            self.f0_active, cfg["pitch"], cfg["formant"], cfg["index_rate"],
        )
        if self.resampler2 is not None:
            infer_wav = cuda_graph.run_cuda_graph(
                self.resampler2, "out-resample", lambda audio: self.resampler2(audio), infer_wav
            )
        infer_wav = infer_wav[: bf + self.sola_buffer_frame + self.sola_search_frame].clone()
        if cfg["rms_mix_rate"] < 1:
            source = self.input_wav[self.extra_frame:][: infer_wav.shape[0]]
            rms1 = self._frame_rms(source, infer_wav.shape[0])
            rms2 = torch.clamp(self._frame_rms(infer_wav, infer_wav.shape[0]), min=1e-3)
            # cap the boost so a quiet onset of the model output can't be blown up 100x
            ratio = torch.clamp(rms1 / rms2, max=4.0)
            infer_wav = infer_wav * torch.pow(ratio, 1.0 - cfg["rms_mix_rate"])
        # SOLA (from DDSP-SVC via RVC)
        conv_input = infer_wav[None, None, : self.sola_buffer_frame + self.sola_search_frame]
        cor_nom = F.conv1d(conv_input, self.sola_buffer[None, None, :])
        cor_den = torch.sqrt(F.conv1d(conv_input ** 2, self.sola_den_kernel) + 1e-8)
        sola_offset = int(torch.argmax(cor_nom[0, 0] / cor_den[0, 0]).item())
        infer_wav = infer_wav[sola_offset:]
        head = infer_wav[: self.sola_buffer_frame] * self.fade_in_window + self.sola_buffer * self.fade_out_window
        self.sola_buffer[:] = infer_wav[bf: bf + self.sola_buffer_frame]
        out = torch.cat([head, infer_wav[self.sola_buffer_frame: bf]]).float().cpu().numpy()
        if not warm:
            self.sola_offset_ms = 1000.0 * sola_offset / self.sr
            peak = float(np.max(np.abs(out))) if out.size else 0.0
            self.out_db = db(peak)
            if peak > 0.9:  # soft limiter instead of hard clipping
                mag = np.abs(out)
                over = mag > 0.9
                out[over] = np.sign(out[over]) * (0.9 + 0.1 * np.tanh((mag[over] - 0.9) / 0.1))
        return out

    # --------------------------------------------------------------- worker
    def _worker(self):
        winutil.boost_current_thread()
        with self._stream_ctx():
            while not self.stop_event.is_set():
                with self.cond:
                    while self.input_ring.level() < self.block_frame and not self.stop_event.is_set():
                        self.cond.wait(0.05)
                if self.stop_event.is_set():
                    break
                self._apply_pending()
                block, _ = self.input_ring.read(self.block_frame)
                self._catch_up()
                t0 = time.perf_counter()
                try:
                    with GPU_LOCK:
                        out = self._process(block)
                    self.errors_in_row = 0
                except Exception as error:
                    log.exception("process failed")
                    self.errors_in_row += 1
                    out = np.zeros(self.block_frame, dtype=np.float32)
                    if self.errors_in_row >= 20 or is_gpu_fault(error):
                        with self.lock:
                            self.state = "error"
                            self.error = friendly_error(error, "推理连续出错")
                        self._event("warn", self.error)
                        self.stop_event.set()
                        break
                elapsed = time.perf_counter() - t0
                rec = self.rec
                if rec is not None and not rec["done"].is_set():
                    rec["in"].append(block.copy())
                    rec["out"].append(np.array(out, dtype=np.float32))
                    if sum(len(c) for c in rec["in"]) >= rec["need"]:
                        rec["done"].set()
                self.proc_times.append(elapsed)
                self.stage_times = dict(self.rt.timings)
                self.blocks += 1
                if elapsed > self.block_frame / self.sr / self.time_scale:
                    self.health["late"] += 1
                self._health_tick()
                quiet = self.out_db < -48
                for sink in list(self.sinks.values()):
                    sink.push(out, quiet)
                self._auto_tune()
        if self.state == "error":
            threading.Thread(target=self._close_streams, daemon=True).start()

    # ------------------------------------------------------------ catch up
    def _catch_up(self):
        """Keep latency bounded when the GPU fell behind (a game frame spike).

        Throwing away queued input skips audio, so do it inside a pause: only
        the quiet leading part of the backlog goes. Only a big backlog (the GPU
        stalled for ~half a second) is cut regardless."""
        bf = self.block_frame
        backlog = self.input_ring.level()
        if backlog <= bf * 1.5:
            return
        excess = backlog - bf // 2
        if backlog > max(bf * 4, int(self.sr * 0.6)):
            self.input_ring.discard(excess)
            self.overload_drops += 1
            self.health["cut"] += 1
            self.health["cut_ms"] += excess * 1000.0 / self.sr
            return
        zc = self.zc
        data = self.input_ring.peek(excess - excess % zc)
        if data.size < zc:
            return
        gain = 10 ** (self.cfg["input_gain_db"] / 20)
        frames = data.reshape(-1, zc)
        rms_db = 10 * np.log10(np.mean(frames * frames, axis=1) * gain * gain + 1e-12)
        quiet_db = max(self.cfg["threshold"], -70.0) + 3
        loud = np.nonzero(rms_db >= quiet_db)[0]
        quiet_frames = int(loud[0]) if loud.size else frames.shape[0]
        quiet_frames -= 2  # keep a little room before the next word
        if quiet_frames > 0:
            self.input_ring.discard(quiet_frames * zc)
            self.health["skipped_ms"] += quiet_frames * zc * 1000.0 / self.sr

    def _health_tick(self):
        now = time.monotonic()
        h = self.health
        if now - h["since"] < 15:
            return
        backend = self.get_backend()
        under = {k: s.underruns for k, s in self.sinks.items()}
        xruns = dict(getattr(backend, "xruns", {}) or {})
        gaps = dict(getattr(backend, "gaps", {}) or {})
        d_under = {k: v - h["under"].get(k, 0) for k, v in under.items()}
        d_xruns = {k: v - h["xruns"].get(k, 0) for k, v in xruns.items()}
        times = sorted(self.proc_times) or [0]
        block_ms = self.block_frame * 1000.0 / self.sr
        p90 = times[int(len(times) * 0.9) - 1 if len(times) > 1 else 0] * 1000 * self.time_scale
        glitches = sum(d_under.values()) + sum(d_xruns.values()) + h["cut"]
        log.info(
            "health %ds: underruns=%s driver_xruns=%s max_callback_gap_ms=%s late_blocks=%d cut=%d(%.0fms) "
            "skipped_quiet_ms=%.0f infer_p90=%.0f/%.0fms",
            now - h["since"], d_under, {"%s:%s" % k: v for k, v in d_xruns.items() if v},
            {k[0]: round(v * 1000) for k, v in gaps.items()}, h["late"], h["cut"], h["cut_ms"], h["skipped_ms"],
            p90, block_ms,
        )
        cause = ""
        if glitches:
            if h["late"] or h["cut"] or p90 > block_ms * 0.85:
                cause = "gpu"
            elif sum(d_xruns.values()) or any(v > 0.08 for v in gaps.values()):
                cause = "device"
            else:
                cause = "buffer"
        self.health_log.append((now, glitches, cause))
        while self.health_log and now - self.health_log[0][0] > 65:
            self.health_log.popleft()
        if hasattr(backend, "reset_stats"):
            backend.reset_stats()
        self.health.update(since=now, under=under, xruns=xruns, late=0, cut=0, cut_ms=0.0, skipped_ms=0.0)

    def health_summary(self):
        total = sum(g for _, g, _ in self.health_log)
        causes = [c for _, g, c in self.health_log if g and c]
        cause = max(set(causes), key=causes.count) if causes else ""
        return {"glitches_60s": total, "cause": cause}

    def _apply_pending(self):
        pending = None
        with self.lock:
            if self.pending_voice is not None:
                pending, self.pending_voice = self.pending_voice, None
            rebuild = self.need_rebuild
            self.need_rebuild = False
        if pending is not None:
            key, voice = pending
            old = self.voice
            self.voice, self.voice_key = voice, key
            self.voice_loading = ""
            rebuild = True
            if old is not None and old is not voice:
                old.release()
            self._event("info", "已切换到模型：%s" % key)
        if rebuild:
            for sink in list(self.sinks.values()):
                sink.reprime()  # the pause while we rebuild is not an underrun
            try:
                with GPU_LOCK:
                    self._alloc()
                    self._warmup()
            except Exception as error:
                log.exception("rebuild failed")
                self._event("warn", "参数应用失败：%s" % error)
            # judge the new settings on fresh measurements only
            self.proc_times.clear()
            self.recent_underruns.clear()
            self.input_ring.discard(max(0, self.input_ring.level() - self.block_frame))
            self.last_adjust = time.monotonic()

    # ------------------------------------------------------------ auto tune
    def _auto_tune(self):
        if self.blocks % 4 != 0 or len(self.proc_times) < 8:
            return
        now = time.monotonic()
        block_s = self.block_frame / self.sr / self.time_scale
        times = sorted(self.proc_times)
        p90 = times[int(len(times) * 0.9) - 1]
        load = p90 / block_s
        under = sum(s.underruns for s in self.sinks.values())
        self.recent_underruns.append((now, under))
        while self.recent_underruns and now - self.recent_underruns[0][0] > 4:
            self.recent_underruns.popleft()
        new_underruns = under - self.recent_underruns[0][1] if self.recent_underruns else 0
        if not self.cfg["auto_tune"]:
            return
        overloaded = load > 0.9 or (new_underruns >= 3 and load > 0.6) or self.overload_drops > 0
        if overloaded and now - self.last_adjust > 2:
            self.overload_drops = 0
            act = self.active()
            note = None
            if act["f0_method"] == "rmvpe":
                self.adapt["f0_method"] = "fcpe"
                note = "显卡繁忙，音高算法临时换成更轻的 FCPE"
            elif act["extra_time"] > 0.31:
                self.adapt["extra_time"] = round(max(0.3, act["extra_time"] - 0.15), 2)
                note = "显卡繁忙，上下文缩短到 %d ms" % (self.adapt["extra_time"] * 1000)
            elif act["block_time"] < 0.30:
                self.adapt["block_time"] = round(min(0.30, act["block_time"] + 0.04), 2)
                note = "显卡繁忙，分块加大到 %d ms 防止断音" % (self.adapt["block_time"] * 1000)
            elif not self.adapt.get("maxed"):
                self.adapt["maxed"] = True
                self.adapt_note = "算力不够：已经是最省的设置。可以在游戏里限帧，给显卡留 10~20% 余量"
                self._event("warn", self.adapt_note)
                self.last_adjust = now
            if note:
                self.last_adjust = now
                self.load_low_since = None
                self.adapt_note = note
                self._event("warn", note)
                with self.lock:
                    self.need_rebuild = True
            return
        if not self.adapt:
            return
        if load < 0.45 and new_underruns == 0:
            if self.load_low_since is None:
                self.load_low_since = now
            elif now - self.load_low_since > 25 and now - self.last_adjust > 25:
                self.adapt.pop("maxed", None)
                for key in ("block_time", "extra_time", "f0_method"):
                    if key in self.adapt:
                        del self.adapt[key]
                        break
                self.last_adjust = now
                self.load_low_since = None
                self.adapt_note = "显卡空闲了，已恢复设置" if not self.adapt else self.adapt_note
                self._event("info", "负载降低，恢复一步设置")
                with self.lock:
                    self.need_rebuild = True
        else:
            self.load_low_since = None

    # ------------------------------------------------------------- self test
    def selftest(self):
        """Check, on this computer, that the GPU pipeline runs and the chosen devices open.

        Runs once after the models are loaded (and on demand from the UI) while
        the voice is off. The result is shown in the UI and written to
        data/logs/selftest.json so problems can be diagnosed from the log alone.
        """
        if self.state in ("running", "starting"):
            return self.selftest_report
        report = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "devices": {}, "gpu": None}
        with self.calib_lock:
            backend = self.get_backend()
            for key, kind in (("input_device", "input"), ("output_device", "output"), ("monitor_device", "output")):
                name = self.cfg[key]
                if not name:
                    continue
                try:
                    result = backend.probe(name, kind, self.sr)
                    if not result.get("ok"):
                        result["error"] = "设备打开了，但没有声音数据流过"
                except Exception as error:
                    result = {"ok": False, "error": str(error)}
                report["devices"][key] = dict(result, name=name)
        if self.cfg["model"] and self.voice is not None:
            started = time.perf_counter()
            try:
                with GPU_LOCK, self._stream_ctx():
                    self._alloc()
                    self._warmup()
                    noise = (np.random.randn(self.block_frame) * 0.05).astype(np.float32)
                    times = []
                    for _ in range(8):
                        t0 = time.perf_counter()
                        self._process(noise, warm=True)
                        times.append((time.perf_counter() - t0) * 1000)
                    self.rt.reset()
                times.sort()
                report["gpu"] = {
                    "ok": True,
                    "device": self.gpu_name,
                    "infer_ms": round(times[len(times) // 2], 1),
                    "block_ms": round(self.block_frame * 1000.0 / self.sr),
                    "total_s": round(time.perf_counter() - started, 1),
                }
            except Exception as error:
                log.exception("self test: inference failed")
                report["gpu"] = {"ok": False, "error": friendly_error(error)}
                if is_gpu_fault(error):
                    self.gpu_broken = friendly_error(error)
        self.selftest_report = report
        log.info("self test: %s", report)
        return report

    # ------------------------------------------------------------- record
    def record(self, seconds, folder):
        """Record N seconds of mic input and converted output (while running) so
        the user can listen back without the delay of live monitoring, plus a
        few numbers that explain unclear speech (level, noise floor, gate)."""
        if self.state != "running":
            raise RuntimeError("先点「开始变声」，再录一段试听")
        seconds = clamp(float(seconds or 5), 2.0, 15.0)
        rec = {"in": [], "out": [], "need": int(seconds * self.sr), "done": threading.Event()}
        gate_before = (self.gate_frames_total, self.gate_frames_open)
        self.rec = rec
        try:
            rec["done"].wait(seconds + 3)
        finally:
            self.rec = None
        if not rec["in"]:
            raise RuntimeError("没有录到声音")
        audio_in = np.concatenate(rec["in"])
        audio_out = np.concatenate(rec["out"])
        zc = self.sr // 100
        n = len(audio_in) // zc
        gain_db = float(self.cfg["input_gain_db"])
        # levels as the gate and the model see them (after the input gain)
        frame_db = 10 * np.log10(np.mean(audio_in[: n * zc].reshape(n, zc) ** 2, axis=1) + 1e-12) + gain_db
        floor = float(np.percentile(frame_db, 10))
        speech = float(np.percentile(frame_db, 95))
        peak = float(20 * np.log10(np.max(np.abs(audio_in)) + 1e-9)) + gain_db
        thr = self.cfg["threshold"]
        voiced = frame_db > speech - 40
        cut = float(np.mean(frame_db[voiced] < thr)) if np.any(voiced) else 0.0
        gate_total = self.gate_frames_total - gate_before[0]
        gate_open = self.gate_frames_open - gate_before[1]
        suggestion = None
        if speech - floor > 18:
            suggestion = int(round(clamp(floor + 8, -75, -40)))
        f0_in = f0_out = None
        try:
            import torchaudio.functional as taf

            rmvpe = self.shared.ensure_f0("rmvpe")
            with GPU_LOCK:
                for name, audio in (("in", audio_in), ("out", audio_out)):
                    wav = taf.resample(torch.from_numpy(audio), self.sr, 16000).to(self.device)
                    f0 = rmvpe.infer_from_audio(wav, thred=0.03, use_graph=False)
                    f0 = f0[(f0 > 60) & (f0 < 1000)]
                    value = round(float(np.median(f0)), 1) if f0.size > 20 else None
                    if name == "in":
                        f0_in = value
                    else:
                        f0_out = value
        except Exception:
            log.exception("record: pitch analysis failed")
        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        import soundfile as sf

        sf.write(os.path.join(folder, stamp + "-in.wav"), audio_in, self.sr, subtype="PCM_16")
        sf.write(os.path.join(folder, stamp + "-out.wav"), np.clip(audio_out, -1, 1), self.sr, subtype="PCM_16")
        for old in sorted(os.listdir(folder))[:-10]:  # keep the last 5 recordings
            try:
                os.remove(os.path.join(folder, old))
            except OSError:
                pass
        result = {
            "id": stamp,
            "seconds": round(len(audio_in) / self.sr, 1),
            "in_peak_db": round(peak, 1),
            "speech_db": round(speech, 1),
            "noise_db": round(floor, 1),
            "threshold": thr,
            "gain_db": gain_db,
            "cut_ratio": round(cut, 2),
            "gate_open_ratio": round(gate_open / gate_total, 2) if gate_total else None,
            "suggested_threshold": suggestion,
            "f0_in": f0_in,
            "f0_out": f0_out,
            "pitch": self.cfg["pitch"],
            "model": self.voice_key,
            "active": self.active(),
        }
        log.info("recording %s: %s", stamp, result)
        return result

    # ------------------------------------------------------------ calibrate
    def capture_input(self, seconds, on_start=None, finish=None):
        """Raw mic audio for N seconds (engine rate), whether or not the voice is on.

        ``on_start(chunks)`` is called once audio starts flowing into ``chunks``;
        setting ``finish`` ends the capture early."""
        chunks = []
        finish = finish or threading.Event()
        deadline = time.monotonic() + 30
        while self.state in ("starting", "loading") and time.monotonic() < deadline:
            time.sleep(0.2)  # wait for the engine instead of fighting over the mic
        if self.state == "running":
            self.calib_chunks = chunks
            try:
                if on_start:
                    on_start(chunks)
                finish.wait(seconds)
            finally:
                self.calib_chunks = None
        else:
            with self.calib_lock:
                temp_stream, _, _ = self.get_backend().open_input(
                    self.cfg["input_device"], self.sr,
                    lambda c: chunks.append(np.array(c, dtype=np.float32)))  # driver buffers are reused
                try:
                    if on_start:
                        on_start(chunks)
                    finish.wait(seconds)
                finally:
                    temp_stream.stop()
                    temp_stream.close()
        if not chunks:
            raise RuntimeError("没有收到麦克风声音")
        return np.concatenate([np.asarray(c, dtype=np.float32) for c in chunks])

    def calibrate(self, seconds=4.0):
        seconds = clamp(float(seconds or 4.0), 2.0, 10.0)
        audio = self.capture_input(seconds)
        import torchaudio.functional as taf

        wav = taf.resample(torch.from_numpy(audio), self.sr, 16000)
        # RMVPE is the most reliable estimator; graph capture is skipped because the
        # realtime worker may be using the GPU on another thread right now.
        rmvpe = self.shared.ensure_f0("rmvpe")
        with GPU_LOCK:  # never overlap with the realtime worker or a start-up warmup
            f0 = rmvpe.infer_from_audio(wav.to(self.device), thred=0.03, use_graph=False)
        voiced = f0[(f0 > 60) & (f0 < 700)]
        ratio = float(voiced.size) / max(1, f0.size)
        if voiced.size < 30:
            raise RuntimeError("没听清，请对着麦克风正常说几句话再试")
        median = float(np.median(voiced))
        target = self.cfg["target_f0"]
        suggested = int(round(clamp(12 * math.log2(target / median), -24, 24)))
        return {"median_hz": round(median, 1), "voiced_ratio": round(ratio, 2),
                "target_hz": target, "suggested_pitch": suggested}

    # --------------------------------------------------------------- status
    def status(self):
        with self.lock:
            state = self.state
            cfg = dict(self.cfg)
        act = self.active()
        info = {
            "state": state,
            "error": self.error,
            "device": self.gpu_name,
            "device_type": self.device.type,
            "half": self.is_half,
            "gpu_priority": self.gpu_priority,
            "boost": self.boost,
            "model": self.voice_key,
            "model_loading": self.voice_loading,
            "model_info": {"version": self.voice.version, "sr": self.voice.tgt_sr, "f0": self.voice.if_f0,
                           "index": bool(self.voice.index is not None)} if self.voice else None,
            "active": {k: (round(v * 1000) if isinstance(v, float) else v) for k, v in act.items()},
            "adapt_note": self.adapt_note if self.adapt else "",
            "events": list(self.events)[-8:],
            "selftest": self.selftest_report,
            "health": self.health_summary() if state == "running" else None,
            "apis": dict(("%s:%s" % k, v) for k, v in (getattr(self.backend, "last_api", None) or {}).items()),
            "config": cfg,
        }
        if state == "running" and self.proc_times:
            times = sorted(self.proc_times)
            p50 = times[len(times) // 2] * 1000
            p90 = times[int(len(times) * 0.9) - 1] * 1000
            block_ms = self.block_frame * 1000.0 / self.sr
            p50, p90 = p50 * self.time_scale, p90 * self.time_scale
            main = self.sinks.get("main")
            buffer_ms = main.queue_ms() if main else 0
            device_ms = (self.in_latency + (main.device_latency if main else 0)) * 1000
            algo_ms = block_ms + (self.sola_buffer_frame + self.sola_search_frame) * 1000.0 / self.sr - self.sola_offset_ms
            info["latency"] = {
                "total_ms": round(algo_ms + p50 + buffer_ms + device_ms),
                "algorithm_ms": round(algo_ms),
                "infer_ms": round(p50, 1),
                "infer_p90_ms": round(p90, 1),
                "buffer_ms": round(buffer_ms, 1),
                "device_ms": round(device_ms, 1),
                "load": round(p90 / block_ms, 2),
            }
            info["stages"] = {k: round(v, 1) for k, v in self.stage_times.items()}
            info["levels"] = {"in_db": round(self.in_db, 1), "out_db": round(self.out_db, 1), "gate": self.gate_open}
            info["sinks"] = {
                name: {"underruns": s.underruns, "target_ms": round(s.target_ms(), 1), "trimmed_ms": round(1000.0 * s.trimmed / self.sr)}
                for name, s in self.sinks.items()
            }
            info["input"] = self.in_name
        return info
