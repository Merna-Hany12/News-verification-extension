from io import BytesIO
from PIL import Image
import numpy as np
import requests as http_requests
from fastapi import APIRouter, Request, HTTPException

from backend.api.schemas import TextRequest, VerifyRequest, ImageRequest
from backend.graph.builder import run_verify

router = APIRouter()

# ─── LABELS (used by /classify) ───────────────────────────
LABELS = [
    "news report breaking news journalism media coverage event announcement",
    "personal opinion social media post joke gossip casual conversation",
]


@router.post("/classify")
def classify_text(request: TextRequest, req: Request):
    classifier = getattr(req.app.state, "classifier", None)
    if not classifier:
        raise HTTPException(status_code=500, detail="Classifier model not loaded")

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


@router.post("/verify")
async def verify_text(request: VerifyRequest, req: Request):
    graph = getattr(req.app.state, "haqq_graph", None)
    if not graph:
        raise HTTPException(status_code=500, detail="LangGraph pipeline not compiled")
    result = await run_verify(graph, request.text, request.lang)
    return result


@router.post("/ocr")
def ocr_image(request: ImageRequest, req: Request):
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
        ocr_reader = getattr(req.app.state, "ocr_reader", None)
        if not ocr_reader:
            print("[HAQQ] OCR reader not loaded")
            return {"text": ""}

        results   = ocr_reader.readtext(img_array, detail=0, paragraph=True)
        extracted = " ".join(results).strip()
        print(f"[HAQQ] OCR extracted ({len(extracted)} chars): {extracted[:100]}")
        return {"text": extracted}
    except Exception as e:
        print(f"[HAQQ] OCR error: {e}")
        return {"text": ""}
