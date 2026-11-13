"""声音体检: record the user reading a short text, then tune the settings to their
voice and rank every installed voice model by how well it works *for this voice*.

RVC models are trained on someone else's (the target) voice; how good they sound
depends a lot on the speaker feeding them. So we convert the user's own reading
with every model and score the result:

  * clarity  - HuBERT content similarity between the reading and the converted
               audio (how much of the pronunciation survives the conversion)
  * pitch    - how faithfully the converted pitch follows the user's intonation
  * clean    - how little noise/breath the model invents in the pauses

and derive per-user settings: noise gate from the measured noise floor, input
gain from the speaking level, pitch shift from the user's average pitch.
"""
import json
import logging
import math
import os
import threading
import time

import numpy as np
import torch
import torch.nn.functional as F

from .rvc import GPU_LOCK, VoiceModel, _f0_to_coarse

log = logging.getLogger(__name__)

READ_TEXT = (
    "大家好，我是你们的新队友。我们先去北边的仓库搜物资，"
    "注意左边二楼有人，小心手雷！好，我来掩护你，快冲！"
    "四是四，十是十，十四是十四，四十是四十。"
    "这一把我们一定能赢，加油！"
)

SILENCE_LEAD = 2.0  # seconds of silence at the start, to measure the room noise


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class VoiceCheck:
    def __init__(self, engine, library, folder):
        self.engine = engine
        self.library = library
        self.folder = folder
        self.lock = threading.Lock()
        self.state = {"phase": "idle"}
        self.audio = None
        self.profile = None
        self.thread = None
        self.live = None  # chunks list while recording, for the level meter
        self.finish = threading.Event()
        self._load()

    @property
    def result_path(self):
        return os.path.join(self.folder, "result.json")

    def _load(self):
        try:
            with open(self.result_path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            if saved.get("phase") == "done":
                self.state = saved
                self.profile = saved.get("profile")
        except (OSError, ValueError):
            pass

    def _save(self):
        try:
            with open(self.result_path, "w", encoding="utf-8") as handle:
                json.dump(self.status(), handle, ensure_ascii=False, indent=1)
        except OSError:
            log.warning("could not save voice check result", exc_info=True)

    @property
    def busy(self):
        return self.state.get("phase") in ("recording", "analyzing")

    # ------------------------------------------------------------ status
    def status(self):
        with self.lock:
            out = dict(self.state)
        out["text"] = READ_TEXT
        out["lead"] = SILENCE_LEAD
        out["now"] = time.time()
        live = self.live
        if out.get("phase") == "recording" and live:
            last = live[-8:]
            peak = max(float(np.max(np.abs(c))) for c in last) if last else 0.0
            out["level_db"] = round(20 * math.log10(peak + 1e-6), 1)
        return out

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    # ------------------------------------------------------------ step 1
    def finish_recording(self):
        self.finish.set()

    def record(self, seconds=25.0):
        if self.busy:
            raise RuntimeError("声音体检正在进行中")
        seconds = _clamp(float(seconds or 25), 8.0, 40.0)
        self.finish.clear()
        previous = dict(self.state)
        # hearing your own converted voice late while reading makes people stumble
        if self.engine.state == "running":
            self.engine.stop()
        self.state = {"phase": "recording", "seconds": seconds, "started": None}

        def started(chunks):
            self.live = chunks
            self._set(started=time.time())

        try:
            audio = self.engine.capture_input(seconds, on_start=started, finish=self.finish)
            sr = self.engine.sr
            profile = self._profile(audio, sr)
        except Exception:
            self.state = previous
            raise
        finally:
            self.live = None
        if profile["voiced_seconds"] < 3:
            self.state = previous
            raise RuntimeError("没怎么录到说话声。请离麦克风近一点，等提示「开始读」后把整段读出来。")
        os.makedirs(self.folder, exist_ok=True)
        import soundfile as sf

        sf.write(os.path.join(self.folder, "reading.wav"), audio, sr, subtype="PCM_16")
        self.audio = audio
        self.profile = profile
        self.state = {"phase": "recorded", "profile": profile}
        log.info("voice check profile: %s", profile)
        return profile

    def _profile(self, audio, sr):
        engine = self.engine
        zc = sr // 100
        n = len(audio) // zc
        frame_db = 10 * np.log10(np.mean(audio[: n * zc].reshape(n, zc) ** 2, axis=1) + 1e-12)
        lead = int(SILENCE_LEAD * 100)
        quiet = frame_db[: max(10, lead - 20)]  # the first ~1.8 s should be silence
        noise = float(np.percentile(quiet, 50)) if quiet.size else float(np.percentile(frame_db, 10))
        speech = float(np.percentile(frame_db[lead:] if n > lead + 50 else frame_db, 95))
        import torchaudio.functional as taf

        wav = taf.resample(torch.from_numpy(audio), sr, 16000)
        rmvpe = engine.shared.ensure_f0("rmvpe")
        with GPU_LOCK:
            f0 = rmvpe.infer_from_audio(wav.to(engine.device), thred=0.03, use_graph=False)
        voiced = f0[(f0 > 60) & (f0 < 800)]
        median = float(np.median(voiced)) if voiced.size > 30 else None
        peak = float(20 * np.log10(np.max(np.abs(audio)) + 1e-6))
        # gain: aim for speech around -24 dBFS (dynamic mics often sit at -40),
        # without pushing the loudest peak past -3 dBFS
        gain = 0.0
        if speech < -30:
            gain = float(_clamp(round((-24 - speech) / 3) * 3, 0, 18))
            gain = float(max(0.0, min(gain, (-3 - peak) // 3 * 3)))
        threshold = None
        if speech - noise > 15:
            threshold = int(round(_clamp(noise + gain + 7, -75, -40)))
        return {
            "noise_db": round(noise, 1),
            "speech_db": round(speech, 1),
            "snr_db": round(speech - noise, 1),
            "peak_db": round(peak, 1),
            "f0_median": round(median, 1) if median else None,
            "f0_low": round(float(np.percentile(voiced, 10)), 1) if voiced.size > 30 else None,
            "f0_high": round(float(np.percentile(voiced, 90)), 1) if voiced.size > 30 else None,
            "voiced_seconds": round(voiced.size / 100.0, 1),
            "suggested_gain_db": gain,
            "suggested_threshold": threshold,
        }

    # ------------------------------------------------------------ step 2
    def start_analysis(self, target_f0):
        reading = os.path.join(self.folder, "reading.wav")
        if self.audio is None and self.profile and os.path.isfile(reading):
            import soundfile as sf

            audio, sr = sf.read(reading, dtype="float32")
            if sr == self.engine.sr:
                self.audio = audio
        if self.audio is None:
            raise RuntimeError("先录一段朗读")
        if self.state.get("phase") == "analyzing":
            return self.status()
        if self.engine.state == "running":
            self.engine.stop()
        models = self.library.list_models()
        if not models:
            raise RuntimeError("还没有声音模型")
        for name in os.listdir(self.folder):
            if name.startswith("m") and name.endswith(".wav"):
                try:
                    os.remove(os.path.join(self.folder, name))
                except OSError:
                    pass
        self.state = {"phase": "analyzing", "done": 0, "total": len(models), "current": "",
                      "profile": self.profile, "results": [], "started": time.time()}
        self.thread = threading.Thread(target=self._analyze, args=(models, float(target_f0)), daemon=True,
                                       name="voicecheck")
        self.thread.start()
        return self.status()

    def _analyze(self, models, target_f0):
        import torchaudio.functional as taf

        engine = self.engine
        shared = engine.shared
        device = engine.device
        sr = engine.sr
        try:
            shared.ensure_hubert()
            rmvpe = shared.ensure_f0("rmvpe")
            # skip the silent lead-in, keep at most 20 s of the reading
            start = int(SILENCE_LEAD * sr * 0.8)
            audio = self.audio[start: start + 20 * sr]
            wav16 = taf.resample(torch.from_numpy(audio), sr, 16000).to(device)
            with GPU_LOCK, torch.no_grad():
                src_feats = F.normalize(shared.hubert.extract(wav16, "v2")[0].float(), dim=-1)
                src_f0 = rmvpe.infer_from_audio(wav16, thred=0.03, use_graph=False)
            median = self.profile.get("f0_median") or 120.0
            pitch = int(round(_clamp(12 * math.log2(target_f0 / median), -24, 24)))
            results = []
            for i, entry in enumerate(models):
                self._set(current=entry["name"], done=i)
                try:
                    result = self._try_model(entry, wav16, src_feats, src_f0, pitch, i)
                except Exception as error:
                    log.exception("voice check: %s failed", entry["key"])
                    result = {"key": entry["key"], "name": entry["name"], "error": str(error)[:120], "score": 0}
                results.append(result)
                results.sort(key=lambda r: r.get("score", 0), reverse=True)
                self._set(results=list(results))
            self._set(phase="done", done=len(models), current="", pitch=pitch, target_f0=target_f0,
                      finished=time.time())
            self._save()
            log.info("voice check done in %.1fs: %s", time.time() - self.state["started"],
                     [(r["name"], r.get("score")) for r in results[:5]])
        except Exception as error:
            log.exception("voice check failed")
            self._set(phase="error", error=str(error)[:200])

    def _convert(self, voice, wav16, f0_raw, pitch):
        shared = self.engine.shared
        device = self.engine.device
        with GPU_LOCK, torch.no_grad():
            torch.manual_seed(0)  # the synthesizer samples noise; keep the ranking repeatable
            feats = shared.hubert.extract(wav16, voice.version)
            feats = torch.cat((feats, feats[:, -1:, :]), 1)
            feats = F.interpolate(feats.permute(0, 2, 1), scale_factor=2).permute(0, 2, 1)
            p_len = min(feats.shape[1], wav16.shape[0] // 160)
            feats = feats[:, :p_len, :]
            lengths = torch.LongTensor([p_len]).to(device)
            sid = torch.LongTensor([0]).to(device)
            if voice.if_f0 == 1:
                f0 = np.asarray(f0_raw, dtype=np.float64).copy()
                uv = f0 == 0
                if np.any(~uv):
                    f0[uv] = np.interp(np.where(uv)[0], np.where(~uv)[0], f0[~uv])
                f0 = f0 * pow(2, pitch / 12)
                coarse, f0t = _f0_to_coarse(f0[:p_len], device)
                if coarse.shape[0] < p_len:
                    pad = p_len - coarse.shape[0]
                    coarse = F.pad(coarse, (0, pad), value=1)
                    f0t = F.pad(f0t, (0, pad))
                audio = voice.net_g.infer(feats, lengths, coarse[None], f0t[None], sid)[0]
            else:
                audio = voice.net_g.infer(feats, lengths, sid)[0]
            return audio.squeeze().float()

    def _try_model(self, entry, wav16, src_feats, src_f0, pitch, index):
        import soundfile as sf
        import torchaudio.functional as taf

        engine = self.engine
        device = engine.device
        own = engine.voice if engine.voice_key == entry["key"] and engine.voice is not None else None
        voice = own or VoiceModel(entry["pth"], device, engine.is_half, index_path="")
        try:
            out = self._convert(voice, wav16, src_f0, pitch)
            out16 = taf.resample(out, voice.tgt_sr, 16000)
            with GPU_LOCK, torch.no_grad():
                out_feats = F.normalize(engine.shared.hubert.extract(out16, "v2")[0].float(), dim=-1)
                out_f0 = engine.shared.ensure_f0("rmvpe").infer_from_audio(out16, thred=0.03, use_graph=False)
            n = min(src_feats.shape[0], out_feats.shape[0])
            clarity = float((src_feats[:n] * out_feats[:n]).sum(-1).mean().item())
            # pitch faithfulness on frames voiced in both
            m = min(len(src_f0), len(out_f0))
            a, b = np.asarray(src_f0[:m]), np.asarray(out_f0[:m])
            both = (a > 60) & (b > 60)
            pitch_ok = 0.0
            if both.sum() > 50:
                la, lb = np.log2(a[both]), np.log2(b[both])
                pitch_ok = float(np.clip(np.corrcoef(la, lb)[0, 1], 0, 1))
                # octave jumps / off-key: penalise median error beyond 1 semitone
                err = np.median(np.abs((lb - la) * 12 - pitch))
                pitch_ok *= float(np.clip(1.5 - err / 2, 0, 1))
            # invented noise in the user's pauses
            src_db = 20 * np.log10(np.abs(wav16.cpu().numpy()) + 1e-6)
            out_np = out16.cpu().numpy()
            k = min(len(src_db), len(out_np)) // 160
            sframes = src_db[: k * 160].reshape(k, 160).mean(1)
            oframes = (out_np[: k * 160].reshape(k, 160) ** 2).mean(1)
            pauses = sframes < np.percentile(sframes, 20)
            talk = sframes > np.percentile(sframes, 60)
            clean = 1.0
            if pauses.any() and talk.any():
                ratio = 10 * np.log10((oframes[pauses].mean() + 1e-10) / (oframes[talk].mean() + 1e-10))
                clean = float(np.clip((-ratio - 10) / 30, 0, 1))  # -40 dB -> 1, -10 dB -> 0
            clarity_n = float(np.clip((clarity - 0.70) / 0.27, 0, 1))  # 0.95 -> 93, 0.90 -> 74
            score = round(100 * (0.6 * clarity_n + 0.3 * pitch_ok + 0.1 * clean))
            sample = "m%02d.wav" % index
            sr_out = voice.tgt_sr
            sf.write(os.path.join(self.folder, sample), np.clip(out.cpu().numpy()[: 12 * sr_out], -1, 1), sr_out,
                     subtype="PCM_16")
            return {
                "key": entry["key"], "name": entry["name"], "cover": entry["cover"], "mtime": entry["mtime"],
                "score": score, "clarity": round(clarity_n * 100), "clarity_raw": round(clarity, 3), "pitch": round(pitch_ok * 100),
                "clean": round(clean * 100), "sample": sample, "version": voice.version, "sr": voice.tgt_sr,
            }
        finally:
            if voice is not own:
                voice.release()
                del voice
                if device.type == "cuda":
                    torch.cuda.empty_cache()
