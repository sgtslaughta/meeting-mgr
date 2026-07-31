import os, tempfile
from fastapi import FastAPI, File, UploadFile
from pyannote.audio import Pipeline, Inference
from pyannote.core import Segment
import torch

app = FastAPI(title="diarizer")

_HF_TOKEN = os.environ.get("HF_TOKEN")
if not _HF_TOKEN:
    raise RuntimeError(
        "HF_TOKEN is required: pyannote's models are gated. Create a token at "
        "https://huggingface.co/settings/tokens and accept the model terms for "
        "pyannote/speaker-diarization-3.1 and pyannote/embedding."
    )

_pipeline = Pipeline.from_pretrained(
    "pyannote/speaker-diarization-3.1", use_auth_token=_HF_TOKEN
)
if torch.cuda.is_available():
    _pipeline.to(torch.device("cuda"))
_embedder = Inference("pyannote/embedding", window="whole",
                      use_auth_token=_HF_TOKEN)

@app.post("/diarize")
async def diarize(file: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        tmp.write(await file.read()); tmp.flush()
        annotation = _pipeline(tmp.name)
        clusters = {}
        for turn, _, label in annotation.itertracks(yield_label=True):
            clusters.setdefault(label, []).append(
                {"start": float(turn.start), "end": float(turn.end)})
        out = []
        for label, spans in clusters.items():
            longest = max(spans, key=lambda s: s["end"] - s["start"])
            emb = _embedder.crop(tmp.name, Segment(longest["start"], longest["end"]))
            out.append({"label": label, "spans": spans,
                        "embedding": emb.flatten().tolist()})
    return {"clusters": out}
