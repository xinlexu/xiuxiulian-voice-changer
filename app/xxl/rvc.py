"""RVC realtime inference.

Adapted from RVC-Project/Retrieval-based-Voice-Conversion-WebUI ``infer/rtrvc.py``
(MIT License). Changes for Xiuxiulian:
  * HuBERT runs through torchaudio (see hubert.py), no fairseq.
  * Pitch / formant / index rate are read on every call, so they change live.
  * Shared models (HuBERT, RMVPE, FCPE) are loaded once and reused when the
    voice model is swapped.
  * Index files are read through Python so non-ASCII paths work with faiss.
"""
import logging
import os
import threading
import time

import numpy as np
import torch
import torch.nn.functional as F

from .vendor.rvc.cuda_graph import run_cuda_graph, clear_cuda_graph_cache

log = logging.getLogger(__name__)

# All GPU work goes through this lock: the realtime worker, warm-up, pitch
# calibration and moving freshly loaded models onto the card never overlap.
GPU_LOCK = threading.RLock()

F0_MIN = 50.0
F0_MAX = 1100.0
F0_MEL_MIN = 1127 * np.log(1 + F0_MIN / 700)
F0_MEL_MAX = 1127 * np.log(1 + F0_MAX / 700)


class VoiceModel:
    """One RVC ``.pth`` voice (synthesizer + optional faiss index)."""

    def __init__(self, pth_path, device, is_half, index_path=""):
        from .vendor.rvc.models import (
            SynthesizerTrnMs256NSFsid,
            SynthesizerTrnMs256NSFsid_nono,
            SynthesizerTrnMs768NSFsid,
            SynthesizerTrnMs768NSFsid_nono,
        )

        started = time.perf_counter()
        with open(pth_path, "rb") as handle:
            cpt = torch.load(handle, map_location="cpu", weights_only=False)
        if not isinstance(cpt, dict) or "weight" not in cpt or "config" not in cpt:
            raise ValueError("不是 RVC 模型（缺少 weight/config）")
        cpt["config"][-3] = cpt["weight"]["emb_g.weight"].shape[0]
        self.if_f0 = int(cpt.get("f0", 1))
        self.version = cpt.get("version", "v1")
        self.tgt_sr = int(cpt["config"][-1])
        if self.version == "v1":
            cls = SynthesizerTrnMs256NSFsid if self.if_f0 == 1 else SynthesizerTrnMs256NSFsid_nono
        else:
            cls = SynthesizerTrnMs768NSFsid if self.if_f0 == 1 else SynthesizerTrnMs768NSFsid_nono
        if self.if_f0 == 1:
            net_g = cls(*cpt["config"], is_half=False)
        else:
            net_g = cls(*cpt["config"])
        if hasattr(net_g, "enc_q"):
            del net_g.enc_q
        net_g.load_state_dict(cpt["weight"], strict=False)
        net_g.eval()
        net_g.remove_weight_norm()
        net_g = net_g.half() if is_half else net_g.float()
        with GPU_LOCK:
            self.net_g = net_g.to(device)
        self.pth_path = pth_path
        self.device = device
        self.is_half = is_half
        self.index = None
        self.big_npy = None
        self.index_path = ""
        if index_path:
            self.load_index(index_path)
        log.info(
            "Loaded voice %s (%s, %d Hz, f0=%d) in %.2fs",
            os.path.basename(pth_path),
            self.version,
            self.tgt_sr,
            self.if_f0,
            time.perf_counter() - started,
        )

    def load_index(self, index_path):
        try:
            import faiss

            data = np.fromfile(index_path, dtype=np.uint8)
            index = faiss.deserialize_index(data)
            dim = 256 if self.version == "v1" else 768
            if index.d != dim:
                log.warning("Index %s has dim %d, model needs %d; ignored", index_path, index.d, dim)
                return
            self.big_npy = index.reconstruct_n(0, index.ntotal)
            self.index = index
            self.index_path = index_path
            log.info("Loaded index %s (%d vectors)", os.path.basename(index_path), index.ntotal)
        except Exception:
            log.exception("Failed to load index %s", index_path)

    def release(self):
        clear_cuda_graph_cache(self.net_g)
        self.net_g = None


class SharedModels:
    """HuBERT + pitch estimators, loaded once per process."""

    def __init__(self, root, device, is_half):
        self.root = root
        self.device = torch.device(device)
        self.is_half = is_half
        self.hubert = None
        self.rmvpe = None
        self.fcpe = None
        self.lock = GPU_LOCK

    def basemodel(self, name):
        for candidate in (
            os.path.join(self.root, "dependencies", "basemodels", name),
            os.path.join(self.root, "assets", name),
            os.path.join(self.root, "models", name),
        ):
            if os.path.isfile(candidate):
                return candidate
        raise FileNotFoundError("找不到基础模型 %s（应在 dependencies\\basemodels 里）" % name)

    def ensure_hubert(self):
        with self.lock:
            if self.hubert is None:
                from .hubert import HubertContent

                started = time.perf_counter()
                self.hubert = HubertContent(self.basemodel("hubert_base.pt"), self.device, self.is_half)
                log.info("HuBERT ready in %.2fs", time.perf_counter() - started)
            return self.hubert

    def ensure_f0(self, method):
        with self.lock:
            if method == "rmvpe":
                if self.rmvpe is None:
                    from .vendor.rvc.rmvpe import RMVPE

                    started = time.perf_counter()
                    self.rmvpe = RMVPE(self.basemodel("rmvpe.pt"), self.is_half, self.device)
                    log.info("RMVPE ready in %.2fs", time.perf_counter() - started)
                return self.rmvpe
            if method == "fcpe":
                if self.fcpe is None:
                    from torchfcpe import spawn_bundled_infer_model

                    started = time.perf_counter()
                    self.fcpe = spawn_bundled_infer_model(device=str(self.device))
                    log.info("FCPE ready in %.2fs", time.perf_counter() - started)
                return self.fcpe
            raise ValueError("Unsupported f0 method %s" % method)

    def estimate_f0(self, wav16k, method):
        """Raw f0 (Hz, 0 = unvoiced) for a 1-D 16 kHz tensor, 100 frames/s."""
        if method == "rmvpe":
            return self.ensure_f0("rmvpe").infer_from_audio(wav16k, thred=0.03)
        model = self.ensure_f0("fcpe")
        with torch.no_grad():
            f0 = model.infer(
                wav16k.float().to(self.device)[None, :, None],
                sr=16000,
                decoder_mode="local_argmax",
                threshold=0.006,
            )
        return f0.squeeze().float().cpu().numpy()


def _f0_to_coarse(f0, device):
    f0 = torch.from_numpy(np.ascontiguousarray(f0)).float().to(device)
    f0_mel = 1127 * torch.log(1 + f0 / 700)
    voiced = f0_mel > 0
    f0_mel[voiced] = (f0_mel[voiced] - F0_MEL_MIN) * 254 / (F0_MEL_MAX - F0_MEL_MIN) + 1
    f0_mel[f0_mel <= 1] = 1
    f0_mel[f0_mel > 255] = 255
    return torch.round(f0_mel).long(), f0


class RealtimeRVC:
    """Stateful block inference. One instance per (voice, buffer layout)."""

    def __init__(self, shared, voice):
        self.shared = shared
        self.voice = voice
        self.device = shared.device
        self.cache_pitch = torch.zeros(1024, device=self.device, dtype=torch.long)
        self.cache_pitchf = torch.zeros(1024, device=self.device, dtype=torch.float32)
        self.resample_kernel = {}
        self.timings = {}

    def reset(self):
        self.cache_pitch.zero_()
        self.cache_pitchf.zero_()

    def _f0(self, x, key, method):
        f0 = self.shared.estimate_f0(x, method)
        f0 = np.asarray(f0, dtype=np.float64)
        uv = f0 == 0
        if np.any(~uv):
            f0[uv] = np.interp(np.where(uv)[0], np.where(~uv)[0], f0[~uv])
        f0 = f0 * pow(2, key / 12)
        return _f0_to_coarse(f0, self.device)

    @torch.no_grad()
    def infer(self, input_wav, block_frame_16k, skip_head, return_length, f0method,
              pitch, formant, index_rate):
        voice = self.voice
        t1 = time.perf_counter()
        feats = self.shared.hubert.extract(input_wav, voice.version)
        feats = torch.cat((feats, feats[:, -1:, :]), 1)
        t2 = time.perf_counter()
        if voice.index is not None and index_rate > 0:
            try:
                npy = feats[0][skip_head // 2:].float().cpu().numpy()
                score, ix = voice.index.search(npy, k=8)
                if (ix >= 0).all():
                    weight = np.square(1 / np.maximum(score, 1e-9))
                    weight /= weight.sum(axis=1, keepdims=True)
                    npy = np.sum(voice.big_npy[ix] * np.expand_dims(weight, axis=2), axis=1)
                    mixed = torch.from_numpy(npy).to(self.device, dtype=feats.dtype)
                    feats[0][skip_head // 2:] = (
                        mixed * index_rate + (1 - index_rate) * feats[0][skip_head // 2:]
                    )
            except Exception:
                log.exception("index search failed")
        t3 = time.perf_counter()
        p_len = input_wav.shape[0] // 160
        factor = pow(2, formant / 12)
        return_length2 = int(np.ceil(return_length * factor))
        if voice.if_f0 == 1:
            f0_extractor_frame = block_frame_16k + 800
            if f0method == "rmvpe":
                f0_extractor_frame = 5120 * ((f0_extractor_frame - 1) // 5120 + 1) - 160
            coarse, f0 = self._f0(input_wav[-f0_extractor_frame:], pitch - formant, f0method)
            shift = block_frame_16k // 160
            self.cache_pitch[:-shift] = self.cache_pitch[shift:].clone()
            self.cache_pitchf[:-shift] = self.cache_pitchf[shift:].clone()
            self.cache_pitch[4 - coarse.shape[0]:] = coarse[3:-1]
            self.cache_pitchf[4 - f0.shape[0]:] = f0[3:-1]
            cache_pitch = self.cache_pitch[None, -p_len:]
            cache_pitchf = self.cache_pitchf[None, -p_len:] * return_length2 / return_length
        t4 = time.perf_counter()
        feats = F.interpolate(feats.permute(0, 2, 1), scale_factor=2).permute(0, 2, 1)
        feats = feats[:, :p_len, :]
        p_len_tensor = torch.LongTensor([p_len]).to(self.device)
        sid = torch.LongTensor([0]).to(self.device)
        net_g = voice.net_g
        sk, rl, rl2 = int(skip_head), int(return_length), int(return_length2)
        if voice.if_f0 == 1:
            audio = run_cuda_graph(
                net_g,
                "rt-f0-%d-%d-%d" % (sk, rl, rl2),
                lambda phone, lengths, coarse_, cont, speaker: net_g.infer(
                    phone, lengths, coarse_, cont, speaker, sk, rl, rl2
                )[0],
                feats,
                p_len_tensor,
                cache_pitch,
                cache_pitchf,
                sid,
            )
        else:
            audio = run_cuda_graph(
                net_g,
                "rt-nof0-%d-%d-%d" % (sk, rl, rl2),
                lambda phone, lengths, speaker: net_g.infer(phone, lengths, speaker, sk, rl, rl2)[0],
                feats,
                p_len_tensor,
                sid,
            )
        audio = audio.squeeze(1).float()
        upp_res = int(np.floor(factor * voice.tgt_sr // 100))
        if upp_res != voice.tgt_sr // 100:
            if upp_res not in self.resample_kernel:
                from torchaudio.transforms import Resample

                self.resample_kernel[upp_res] = Resample(
                    orig_freq=upp_res, new_freq=voice.tgt_sr // 100, dtype=torch.float32
                ).to(self.device)
            audio = self.resample_kernel[upp_res](audio[:, : return_length * upp_res])
        t5 = time.perf_counter()
        self.timings = {
            "hubert": (t2 - t1) * 1000,
            "index": (t3 - t2) * 1000,
            "f0": (t4 - t3) * 1000,
            "synth": (t5 - t4) * 1000,
        }
        return audio.squeeze()
