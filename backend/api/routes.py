from io import BytesIO
from PIL import Image
import numpy as np
import requests as http_requests
from fastapi import APIRouter, Request, HTTPException

from backend.api.schemas import TextRequest, VerifyRequest, ImageRequest, DetectMediaRequest
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


@router.post("/detect-media")
async def detect_media(request: DetectMediaRequest, req: Request):
    """
    AI-generated / manipulated media detection for images and video.

    ─── TODO: replace the stub body below with the real GPU-accelerated
    detection ensemble. Rough shape to build toward:
      1. Download the image (and/or sample frames from the video) —
         same pattern as ocr_image() above, via http_requests.get(...).
      2. Preprocess: resize, normalize, format-check.
      3. Run through the detection ensemble on GPU, with early-exit logic
         (cheap/fast signals first, escalate to heavier detectors only
         if inconclusive).
      4. Fuse per-method scores into one overall verdict + confidence.
      5. For video: aggregate per-frame verdicts into one clip verdict.

    Once ready, load whatever models you need onto req.app.state at
    startup (same pattern as classifier / ocr_reader above) and pull
    them here with getattr(req.app.state, "your_model_name", None).
    """
    if not request.image_url and not request.video_url:
        return {
            "verdict": "inconclusive",
            "confidence": 0.0,
            "explanation": "لا توجد وسائط لتحليلها.",
            "sources": [],
        }

    target      = request.video_url or request.image_url
    media_kind  = "فيديو" if request.video_url else "صورة"
    print(f"[HAQQ] /detect-media stub called for {media_kind}: {target[:80]}")

    # ─── STUB LOGIC — delete once the real models are wired in ───
    return {
        "verdict": "inconclusive",
        "confidence": 0.0,
        "explanation": f"(محاكاة مؤقتة) لم يتم بعد ربط نموذج الكشف الفعلي — {media_kind} قيد الانتظار.",
        "sources": [],
    }
    # ─── END STUB LOGIC ───