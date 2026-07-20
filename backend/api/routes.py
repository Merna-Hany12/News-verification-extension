from io import BytesIO
from PIL import Image
import numpy as np
import requests as http_requests
from fastapi import APIRouter, Request, HTTPException
import asyncio
from backend.api.schemas import TextRequest, ImageRequest, DetectMediaRequest, VerifyContentRequest
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


from backend.observability.axiom_logger import axiom_logger, extract_platform
import time

@router.post("/verify-content")
async def verify_content(request: VerifyContentRequest, req: Request):
    """
    Single-request replacement for the extension's old client-side
    orchestration (verify text -> maybe OCR -> maybe re-verify).
    """
    start_time = time.time()
    request_id = getattr(req.state, 'request_id', 'unknown')
    
    graph      = getattr(req.app.state, "haqq_graph", None)
    ocr_reader = getattr(req.app.state, "ocr_reader", None)
    if not graph:
        raise HTTPException(status_code=500, detail="LangGraph pipeline not compiled")

    direct_text = (request.text or "").strip()
    ocr_task = (
        asyncio.create_task(asyncio.to_thread(_run_ocr_sync, request.image_url, ocr_reader))
        if request.image_url else None
    )

    def _log_and_return(result: dict, text_source: str):
        elapsed_ms = (time.time() - start_time) * 1000
        axiom_logger.log_verification_event({
            "request_id": request_id,
            "pipeline": "traditional",
            "text_source": text_source,
            "verdict": result.get("verdict"),
            "content_type": result.get("content_type"),
            "confidence": result.get("confidence"),
            "total_tokens": result.get("total_tokens", 0),
            "total_cost_usd": result.get("total_cost_usd", 0.0),
            "latency_ms": elapsed_ms,
            "trusted_sources_count": sum(1 for s in result.get("sources", []) if s.get("trusted")),
            "lang": request.lang,
            "text_length": len(direct_text),
            "platform": extract_platform(request.image_url)
        })
        return {**result, "text_source": text_source}

    if len(direct_text) < MIN_TEXT_LEN:
        ocr_text = ((await ocr_task) if ocr_task else "").strip()
        if len(ocr_text) < MIN_TEXT_LEN:
            return _log_and_return({
                "verdict": "unverified",
                "confidence": 0,
                "explanation": "لا يوجد نص كافٍ في هذا المنشور للتحقق منه.",
                "sources": [],
            }, "none")
        result = await run_verify(graph, ocr_text, request.lang, request_id)
        return _log_and_return(result, "ocr")

    first_result = await run_verify(graph, direct_text, request.lang, request_id)

    should_try_ocr = first_result.get("verdict") in ("unverified", "non_news")
    if should_try_ocr and ocr_task:
        ocr_text = ((await ocr_task) or "").strip()
        if len(ocr_text) >= MIN_TEXT_LEN:
            ocr_result = await run_verify(graph, ocr_text, request.lang, request_id)
            return _log_and_return(ocr_result, "ocr_retry")

    return _log_and_return(first_result, "direct")

