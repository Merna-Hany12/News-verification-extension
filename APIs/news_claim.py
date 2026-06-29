from contextlib import asynccontextmanager
from io import BytesIO

import easyocr
import numpy as np
import requests as http_requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from pydantic import BaseModel
from transformers import pipeline
from dotenv import load_dotenv

load_dotenv()   # ← reads .env before anything else

from haqq_graph import build_graph, run_verify   # noqa: E402 (must be after load_dotenv)

# ─── GLOBALS ──────────────────────────────────────────────
classifier  = None
ocr_reader  = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global classifier, ocr_reader

    print("LOADING CLASSIFIER...")
    classifier = pipeline(
        "zero-shot-classification",
        model="MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
    )
    print("CLASSIFIER LOADED ✅")

    print("LOADING EASYOCR (ar + en)...")
    ocr_reader = easyocr.Reader(["ar", "en"], gpu=False)
    print("EASYOCR LOADED ✅")

    print("BUILDING LANGGRAPH PIPELINE...")
    app.state.haqq_graph = build_graph()
    print("LANGGRAPH PIPELINE READY ✅")

    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── SCHEMAS ──────────────────────────────────────────────
class TextRequest(BaseModel):
    text: str

class ImageRequest(BaseModel):
    image_url: str

class VerifyRequest(BaseModel):
    text: str
    lang: str = "ar"   # "ar" or "en" — extension sends this


# ─── LABELS (used by /classify) ───────────────────────────
LABELS = [
    "news report breaking news journalism media coverage event announcement",
    "personal opinion social media post joke gossip casual conversation",
]


# ─── /classify ────────────────────────────────────────────
# Kept as-is — extension still calls this directly for the
# quick is-this-news check before showing the HAQQ badge.
@app.post("/classify")
def classify_text(request: TextRequest):
    result = classifier(request.text, LABELS)

    news_score     = 0.0
    non_news_score = 0.0

    for label, score in zip(result["labels"], result["scores"]):
        if "news" in label.lower() or "report" in label.lower():
            news_score = float(score)
        else:
            non_news_score = float(score)

    print(f"[HAQQ] news_score    : {news_score:.3f}")
    print(f"[HAQQ] non_news_score: {non_news_score:.3f}")
    print(f"[HAQQ] text          : {request.text[:80]}")

    is_news = news_score > non_news_score and news_score >= 0.50

    return {
        "label":          result["labels"][0],
        "score":          float(result["scores"][0]),
        "news_score":     news_score,
        "non_news_score": non_news_score,
        "is_news":        is_news,
    }


# ─── /verify ──────────────────────────────────────────────
# NEW endpoint — full LangGraph pipeline:
#   classify → extract keywords → search → LLM verify → score
# background.js calls this instead of doing the search/score in JS.
@app.post("/verify")
async def verify_text(request: VerifyRequest, req: Request):
    graph  = req.app.state.haqq_graph
    result = await run_verify(graph, request.text, request.lang)
    return result


# ─── /ocr ─────────────────────────────────────────────────
@app.post("/ocr")
def ocr_image(request: ImageRequest):
    print(f"[HAQQ] OCR request for: {request.image_url[:80]}")

    try:
        resp = http_requests.get(request.image_url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[HAQQ] OCR download error: {e}")
        return {"text": ""}

    try:
        img       = Image.open(BytesIO(resp.content)).convert("RGB")
        img_array = np.array(img)
        results   = ocr_reader.readtext(img_array, detail=0, paragraph=True)
        extracted = " ".join(results).strip()
        print(f"[HAQQ] OCR extracted ({len(extracted)} chars): {extracted[:100]}")
        return {"text": extracted}
    except Exception as e:
        print(f"[HAQQ] OCR error: {e}")
        return {"text": ""}


# ─── RUN ──────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)