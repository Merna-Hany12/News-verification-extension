from io import BytesIO
from PIL import Image
import numpy as np
import requests as http_requests
from fastapi import APIRouter, Request, HTTPException
import asyncio
from backend.api.schemas import TextRequest, ImageRequest, DetectMediaRequest, VerifyContentRequest
from backend.graph.builder import run_verify

router = APIRouter()

@router.post("/classify")
def classify_text(request: TextRequest, req: Request):
    classifier = getattr(req.app.state, "classifier", None)
    if not classifier:
        raise HTTPException(status_code=500, detail="Classifier model not loaded")

    text_truncated = request.text[:500]
    probs = classifier.predict_proba([text_truncated])[0]
    pred_idx = int(classifier.predict([text_truncated])[0])

    label_names = {0: "news", 1: "historical_scientific", 2: "medical", 3: "non_news"}
    label = label_names.get(pred_idx, "news")
    score = float(probs[pred_idx])

    news_score = float(probs[0] + probs[1] + probs[2])
    non_news_score = float(probs[3])

    print(f"[HAQQ] news_score    : {news_score:.3f}")
    print(f"[HAQQ] non_news_score: {non_news_score:.3f}")
    print(f"[HAQQ] text          : {request.text[:80]}")

    is_news = news_score > non_news_score and news_score >= 0.50

    return {
        "label":          label,
        "score":          score,
        "news_score":     news_score,
        "non_news_score": non_news_score,
        "is_news":        is_news,
    }


def _run_ocr_sync(image_url: str, ocr_reader) -> str:
    """
    Core OCR logic, extracted so both /ocr and /verify-content can call
    it. Stays synchronous (EasyOCR is CPU-bound) — callers that need it
    to run concurrently with other async work should wrap this in
    asyncio.to_thread(...).
    """
    try:
        resp = http_requests.get(image_url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[HAQQ] OCR download error: {e}")
        return ""

    try:
        img       = Image.open(BytesIO(resp.content)).convert("RGB")
        img_array = np.array(img)
        if not ocr_reader:
            print("[HAQQ] OCR reader not loaded")
            return ""

        results   = ocr_reader.readtext(img_array, detail=0, paragraph=True)
        extracted = " ".join(results).strip()
        print(f"[HAQQ] OCR extracted ({len(extracted)} chars): {extracted[:100]}")
        return extracted
    except Exception as e:
        print(f"[HAQQ] OCR error: {e}")
        return ""


@router.post("/ocr")
def ocr_image(request: ImageRequest, req: Request):
    print(f"[HAQQ] OCR request for: {request.image_url[:80]}")
    ocr_reader = getattr(req.app.state, "ocr_reader", None)
    extracted = _run_ocr_sync(request.image_url, ocr_reader)
    return {"text": extracted}
MIN_TEXT_LEN = 15


@router.post("/verify-content")
async def verify_content(request: VerifyContentRequest, req: Request):
    """
    Single-request replacement for the extension's old client-side
    orchestration (verify text -> maybe OCR -> maybe re-verify). Collapses
    what used to be up to 3 separate chrome.runtime round trips into one.
    """
    graph      = getattr(req.app.state, "haqq_graph", None)
    ocr_reader = getattr(req.app.state, "ocr_reader", None)
    if not graph:
        raise HTTPException(status_code=500, detail="LangGraph pipeline not compiled")

    direct_text = (request.text or "").strip()

    # OCR is CPU-bound/synchronous — run it in a thread so it executes
    # concurrently with run_verify's async I/O instead of blocking the
    # event loop. Fired immediately, same "don't wait to find out if we
    # need it" principle as the old client-side version.
    ocr_task = (
        asyncio.create_task(asyncio.to_thread(_run_ocr_sync, request.image_url, ocr_reader))
        if request.image_url else None
    )

    # Case 1: direct text too short to bother verifying — go straight to OCR.
    if len(direct_text) < MIN_TEXT_LEN:
        ocr_text = ((await ocr_task) if ocr_task else "").strip()
        if len(ocr_text) < MIN_TEXT_LEN:
            return {
                "verdict": "unverified",
                "confidence": 0,
                "explanation": "لا يوجد نص كافٍ في هذا المنشور للتحقق منه.",
                "sources": [],
                "text_source": "none",
            }
        result = await run_verify(graph, ocr_text, request.lang)
        return {**result, "text_source": "ocr"}

    # Case 2: verify the direct text first.
    first_result = await run_verify(graph, direct_text, request.lang)

    # Fall back to OCR on "unverified" (checkable claim, nothing
    # conclusive from the caption) or "non_news" (caption reads as
    # opinion, but the image itself might contain a real claim).
    should_try_ocr = first_result.get("verdict") in ("unverified", "non_news")

    if should_try_ocr and ocr_task:
        ocr_text = ((await ocr_task) or "").strip()
        if len(ocr_text) >= MIN_TEXT_LEN:
            ocr_result = await run_verify(graph, ocr_text, request.lang)
            return {**ocr_result, "text_source": "ocr_retry"}

    return {**first_result, "text_source": "direct"}

