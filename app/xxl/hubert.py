"""HuBERT content encoder for RVC, built on torchaudio instead of fairseq.

RVC ships the original fairseq ``hubert_base.pt`` checkpoint. torchaudio has a
bit-exact port of the same network, so we map the fairseq weights onto it and
keep ``final_proj`` for v1 models. This removes the fairseq dependency (and
its torch>=2.6 ``weights_only`` loading problems) from the realtime path.
"""
import logging
import pickle
import types

import torch
from torch import nn

from .vendor.rvc.cuda_graph import run_cuda_graph

log = logging.getLogger(__name__)


class _Stub:
    def __init__(self, *args, **kwargs):
        pass

    def __setstate__(self, state):
        self.state = state


class _LenientUnpickler(pickle.Unpickler):
    """fairseq pickles a few of its own classes (e.g. Dictionary) next to the
    weights. We only need the tensors, so unknown classes become stubs."""

    def find_class(self, module, name):
        try:
            return super().find_class(module, name)
        except Exception:
            return type(name, (_Stub,), {})


_pickle_module = types.ModuleType("xxl_lenient_pickle")
_pickle_module.Unpickler = _LenientUnpickler
_pickle_module.load = pickle.load
_pickle_module.loads = pickle.loads


def _load_fairseq_state(path):
    with open(path, "rb") as handle:
        ckpt = torch.load(
            handle, map_location="cpu", weights_only=False, pickle_module=_pickle_module
        )
    state = ckpt["model"] if isinstance(ckpt, dict) and "model" in ckpt else ckpt
    return state


class HubertContent(nn.Module):
    def __init__(self, path, device, is_half):
        super().__init__()
        import torchaudio
        from torchaudio.models.wav2vec2.utils.import_fairseq import _map_key

        state = _load_fairseq_state(path)
        model = torchaudio.models.hubert_base()
        converted = {}
        for key, value in state.items():
            try:
                mapped = _map_key(key)
            except ValueError:
                mapped = None
            if mapped is not None and not mapped.startswith("aux") and mapped != "label_embs_concat":
                converted[mapped] = value
        missing, unexpected = model.load_state_dict(converted, strict=False)
        if missing:
            raise RuntimeError("HuBERT weights incomplete: %s" % ", ".join(missing[:8]))
        if unexpected:
            log.warning("HuBERT unexpected keys ignored: %s", unexpected[:8])
        self.model = model
        self.final_proj = nn.Linear(768, 256)
        self.final_proj.weight.data.copy_(state["final_proj.weight"])
        self.final_proj.bias.data.copy_(state["final_proj.bias"])
        self.eval()
        self.is_half = bool(is_half)
        dtype = torch.float16 if self.is_half else torch.float32
        self.to(device=device, dtype=dtype)
        self.device = torch.device(device)

    @torch.no_grad()
    def extract(self, wav16k, version):
        """wav16k: 1-D float tensor on ``self.device``. Returns (1, frames, C)."""
        x = wav16k.view(1, -1)
        x = x.half() if self.is_half else x.float()
        if version == "v1":
            def forward(audio):
                feats, _ = self.model.extract_features(audio, num_layers=9)
                return self.final_proj(feats[-1])

            return run_cuda_graph(self, "hubert-v1", forward, x)

        def forward(audio):
            feats, _ = self.model.extract_features(audio, num_layers=12)
            return feats[-1]

        return run_cuda_graph(self, "hubert-v2", forward, x)
