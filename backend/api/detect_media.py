from fastapi import APIRouter, Request

from backend.api.schemas import DetectMediaRequest

router = APIRouter()


@router.post("/detect-media")
async def detect_media(request: DetectMediaRequest, req: Request):
    """
    AI-generated / manipulated media detection for images and video.

    ─── TODO: replace the stub body below with the real GPU-accelerated
    detection ensemble. Rough shape to build toward:
      1. Download the image (and/or sample frames from the video) —
         same pattern as ocr_image() in routes.py, via http_requests.get(...).
      2. Preprocess: resize, normalize, format-check.
      3. Run through the detection ensemble on GPU, with early-exit logic
         (cheap/fast signals first, escalate to heavier detectors only
         if inconclusive).
      4. Fuse per-method scores into one overall verdict + confidence.
      5. For video: aggregate per-frame verdicts into one clip verdict.

    Once ready, load whatever models you need onto app.state at startup
    inside main.py's lifespan() — same pattern as classifier / ocr_reader —
    and pull them here with getattr(req.app.state, "your_model_name", None).
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