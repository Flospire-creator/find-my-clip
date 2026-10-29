"""The two local models: CLIP for pictures, Whisper for speech.

Both load lazily and run on this Mac. If either is missing, the app keeps
working and says what is switched off (see status()).
"""

import os
import threading

CLIP_ARCH = "ViT-B-32"
CLIP_WEIGHTS = "laion2b_s34b_b79k"
SPEECH_MODEL = os.environ.get("FMC_SPEECH_MODEL", "medium")

_lock = threading.Lock()
_clip = None
_speech = None
_errors = {}


def _hub(fn):
    """Use the local model cache first, then allow a one-off download."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        return fn()
    except Exception:
        os.environ["HF_HUB_OFFLINE"] = "0"
        try:
            return fn()
        finally:
            os.environ["HF_HUB_OFFLINE"] = "1"


def clip():
    global _clip
    with _lock:
        if _clip is None and "clip" not in _errors:
            try:
                import torch
                import open_clip
                device = "mps" if torch.backends.mps.is_available() else "cpu"
                model, _, preprocess = _hub(lambda: open_clip.create_model_and_transforms(
                    CLIP_ARCH, pretrained=CLIP_WEIGHTS, device=device))
                model.eval()
                tok = open_clip.get_tokenizer(CLIP_ARCH)
                _clip = Clip(torch, model, preprocess, tok, device)
            except Exception as e:
                _errors["clip"] = str(e)
        return _clip


def speech():
    global _speech
    with _lock:
        if _speech is None and "speech" not in _errors:
            try:
                from faster_whisper import BatchedInferencePipeline, WhisperModel
                # Batched decoding: a 4-minute film took 94 s against 166 s unbatched, same words.
                _speech = BatchedInferencePipeline(model=_hub(lambda: WhisperModel(SPEECH_MODEL, device="cpu", compute_type="int8")))
            except Exception as e:
                _errors["speech"] = str(e)
        return _speech


def status():
    return {
        "pictures": "clip" not in _errors,
        "speech": "speech" not in _errors,
        "errors": dict(_errors),
    }


class Clip:
    def __init__(self, torch, model, preprocess, tok, device):
        self.torch, self.model, self.preprocess, self.tok, self.device = torch, model, preprocess, tok, device

    def images(self, paths):
        from PIL import Image
        t = self.torch
        batch = []
        for p in paths:
            with Image.open(p) as im:
                batch.append(self.preprocess(im.convert("RGB")))
        with t.no_grad():
            f = self.model.encode_image(t.stack(batch).to(self.device))
            f = f / f.norm(dim=-1, keepdim=True)
        return f.float().cpu().numpy()

    def texts(self, texts):
        t = self.torch
        with t.no_grad():
            f = self.model.encode_text(self.tok(texts).to(self.device))
            f = f / f.norm(dim=-1, keepdim=True)
        return f.float().cpu().numpy()
