from contextlib import asynccontextmanager
from io import BytesIO

import easyocr
import numpy as np
import requests as http_requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from pydantic import BaseModel
from transformers import pipeline

# ─── MODELS ───────────────────────────────────────────────
classifier  = None
ocr_reader  = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global classifier, ocr_reader

    print("LOADING CLASSIFIER...")
    classifier = pipeline(
        "zero-shot-classification",
        model="MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
    )
    print("CLASSIFIER LOADED ✅")

    print("LOADING EASYOCR (ar + en)...")
    ocr_reader = easyocr.Reader(["ar", "en"], gpu=False)
    print("EASYOCR LOADED ✅")

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

# ─── LABELS ───────────────────────────────────────────────
LABELS = [
    "news report breaking news journalism media coverage event announcement",
    "personal opinion social media post joke gossip casual conversation"
]

# ─── CLASSIFY ─────────────────────────────────────────────
@app.post("/classify")
def classify_text(request: TextRequest):   # ← was missing type hint
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
    print(f"[HAQQ] text          : {request.text}")

    is_news = news_score > non_news_score and news_score >= 0.50

    return {
        "label":          result["labels"][0],
        "score":          float(result["scores"][0]),
        "news_score":     news_score,
        "non_news_score": non_news_score,
        "is_news":        is_news
    }

# ─── OCR ──────────────────────────────────────────────────
@app.post("/ocr")
def ocr_image(request: ImageRequest):
    print(f"[HAQQ] OCR request for: {request.image_url[:80]}")

    # Download the image from the URL
    try:
        resp = http_requests.get(request.image_url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[HAQQ] OCR download error: {e}")
        return {"text": ""}

    # Run EasyOCR
    try:
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        img_array = np.array(img)
        results = ocr_reader.readtext(img_array, detail=0, paragraph=True)
        extracted = " ".join(results).strip()
        print(f"[HAQQ] OCR extracted ({len(extracted)} chars): {extracted}")
        return {"text": extracted}
    except Exception as e:
        print(f"[HAQQ] OCR error: {e}")
        return {"text": ""}

# ─── RUN ──────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)