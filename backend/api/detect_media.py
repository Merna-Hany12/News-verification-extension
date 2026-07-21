import sys
import os
import asyncio
import traceback
import concurrent.futures
from datetime import datetime
from io import BytesIO

import cv2
import numpy as np
import torch
import httpx
import requests as http_requests
from PIL import Image
from fastapi import APIRouter, Request, HTTPException

from backend.api.schemas import DetectMediaRequest

router = APIRouter()

DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "*/*",
    "Referer": "https://www.facebook.com/",
}

# Frames get saved here for manual inspection — see save_frames_to_disk()
FRAME_DEBUG_DIR = os.path.join(os.getcwd(), "haqq_debug_frames")
os.makedirs(FRAME_DEBUG_DIR, exist_ok=True)

N_FRAMES = 8


# ─── 1. Face-Aware Fusion Logic ───
def face_aware_fusion(avg_df_prob: float, avg_ai_prob: float, pct_faces_detected: float):
    """
    Face-detection-aware fusion.
    If faces are meaningfully present, trust the deepfake model first.
    Fall back to AIGC model only when no faces are detected.
    """
    FACE_THRESHOLD = 25.0  # If more than 25% of frames have faces

    if pct_faces_detected >= FACE_THRESHOLD:
        if avg_df_prob >= 0.50:
            return {
                "verdict": "manipulated",
                "confidence": float(avg_df_prob),
                "explanation": "تم اكتشاف تلاعب في الوجوه (Deepfake).",
                "sources": []
            }
        elif avg_ai_prob >= 0.50:
            return {
                "verdict": "ai_generated",
                "confidence": float(avg_ai_prob),
                "explanation": "المحتوى مولد بالذكاء الاصطناعي.",
                "sources": []
            }
    else:
        if avg_ai_prob >= 0.50:
            return {
                "verdict": "ai_generated",
                "confidence": float(avg_ai_prob),
                "explanation": "المحتوى مولد بالذكاء الاصطناعي.",
                "sources": []
            }

    p_real = (1.0 - avg_df_prob) * (1.0 - avg_ai_prob)
    return {
        "verdict": "real",
        "confidence": float(p_real),
        "explanation": "لم يتم اكتشاف تلاعب أو توليد بالذكاء الاصطناعي.",
        "sources": []
    }


def save_frames_to_disk(frames: list, timestamps: list, url: str) -> str:
    """
    Saves every extracted frame as a real PNG file on disk for debug/audit.
    """
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    url_tag = str(abs(hash(url)))[:8]
    run_dir = os.path.join(FRAME_DEBUG_DIR, f"{run_id}_{url_tag}")
    os.makedirs(run_dir, exist_ok=True)

    saved_paths = []
    for i, frame in enumerate(frames):
        ts = timestamps[i] if i < len(timestamps) else None
        ts_label = f"{ts:.2f}s".replace(".", "_") if ts is not None else "unknown"
        filename = f"frame_{i}_t{ts_label}.png"
        path = os.path.join(run_dir, filename)
        frame.save(path, format="PNG")
        saved_paths.append(path)

    print(f"[HAQQ] Saved {len(saved_paths)} frame(s) to: {run_dir}")
    return run_dir


def _format_timestamp(t: float | None) -> str:
    if t is None:
        return "—"
    minutes = int(t // 60)
    seconds = t - minutes * 60
    return f"{minutes}:{seconds:05.2f}" if minutes else f"{seconds:.2f}s"


async def _download_single_image(url: str) -> Image.Image:
    resp = await asyncio.to_thread(
        http_requests.get, url, headers=DOWNLOAD_HEADERS, timeout=10
    )
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert("RGB")


from backend.observability.axiom_logger import axiom_logger, extract_platform
import time

@router.post("/detect-media")
async def detect_media(request: DetectMediaRequest, req: Request):
    start_time = time.time()
    request_id = getattr(req.state, 'request_id', 'unknown')
    
    yunet = req.app.state.yunet
    gend_model = req.app.state.gend_model
    aigc_pipeline = req.app.state.aigc_pipeline

    frames = None
    timestamps: list = []
    extraction_method = None
    saved_frames_dir = None

    if request.extracted_frames:
        import base64
        print(f"[HAQQ] Received {len(request.extracted_frames)} client-side extracted frames!")
        try:
            frames = []
            for b64 in request.extracted_frames:
                img_data = base64.b64decode(b64)
                img = Image.open(BytesIO(img_data)).convert("RGB")
                frames.append(img)

            if frames:
                extraction_method = "client-side-frames"
                timestamps = [None] * len(frames)
                saved_frames_dir = save_frames_to_disk(frames, timestamps, "client-side-capture")
                print(f"[HAQQ] Successfully decoded {len(frames)} client-side frames, saved to {saved_frames_dir}")
        except Exception as e:
            print(f"[HAQQ] Failed to decode client frames: {e}")
            frames = None

    if not frames:
        fallback_url = request.image_url or request.video_url
        if fallback_url:
            print(f"[HAQQ] No client frames provided — falling back to single-image download: {fallback_url[:80]}...")
            try:
                img = await _download_single_image(fallback_url)
                frames, extraction_method = [img], "single-image"
                timestamps = [None]
                saved_frames_dir = save_frames_to_disk(frames, timestamps, fallback_url)
            except Exception as e:
                print(f"[HAQQ] Fallback image download failed: {e}")
                raise HTTPException(status_code=400, detail=f"Could not download fallback image/poster: {e}")
        else:
            return {"verdict": "inconclusive", "confidence": 0.0, "explanation": "لا توجد وسائط لتحليلها.", "sources": []}


    # ── Process Frames with YuNet ──
    processed_images = []
    faces_detected = []
    margin = 20

    for frame in frames:
        frame_cv = cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR)
        h, w, _ = frame_cv.shape
        yunet.setInputSize((w, h))

        _, faces = yunet.detect(frame_cv)

        if faces is not None and len(faces) > 0:
            box = faces[0][:4].astype(int)
            x, y, bw, bh = box
            x1 = max(0, x - margin)
            y1 = max(0, y - margin)
            x2 = min(w, x + bw + margin)
            y2 = min(h, y + bh + margin)

            face_crop = frame.crop((x1, y1, x2, y2))
            processed_images.append(face_crop)
            faces_detected.append(True)
        else:
            processed_images.append(frame)
            faces_detected.append(False)

    # ── GenD Inference (Only on Face Frames) ──
    # Kept index-aligned to the original frame list (via face_indices) so
    # per-frame timestamps/results can be reported correctly, not just a
    # flat list that's lost its correspondence to frame position.
    face_indices = [i for i, has_face in enumerate(faces_detected) if has_face]
    face_images = [processed_images[i] for i in face_indices]

    df_score_by_index: list = [None] * len(frames)

    if face_images:
        tensors = [gend_model.feature_extractor.preprocess(img) for img in face_images]
        batch_tensor = torch.stack(tensors).to(gend_model.device)
        with torch.no_grad():
            logits = gend_model(batch_tensor)
            probs = torch.softmax(logits, dim=-1).cpu().numpy()
        for idx, p in zip(face_indices, probs):
            df_score_by_index[idx] = float(p[1])

    deepfake_scores = [s for s in df_score_by_index if s is not None]
    avg_df_prob = float(np.mean(deepfake_scores)) if deepfake_scores else 0.0

    # ── SigLIP Inference (On All Original Frames) ──
    aigc_scores = []
    for frame in frames:
        res = aigc_pipeline(frame)
        aigc_dict = {item['label'].lower(): item['score'] for item in res}
        aigc_scores.append(aigc_dict.get('ai', 0.0))

    avg_ai_prob = float(np.mean(aigc_scores)) if aigc_scores else 0.0

    # ── Face-Aware Fusion ──
    pct_faces = (sum(faces_detected) / len(frames)) * 100.0
    result = face_aware_fusion(avg_df_prob, avg_ai_prob, pct_faces)

    # Honest disclosure: a single poster-image analysis is a meaningfully
    # weaker signal than a full multi-frame video scan (can't catch
    # temporal artifacts like inconsistent blinking/lighting across
    # frames) — flag it in the explanation so the confidence number
    # isn't read the same way as a full video analysis.
    explanation = result["explanation"]
    if extraction_method == "single-image":
        explanation += " ⚠️ (تحليل الصورة المصغّرة فقط — لم يتم تحميل الفيديو الفعلي بعد، الدقة أقل من تحليل كامل)"
    result["explanation"] = explanation

    # ── Per-Frame Breakdown (timestamp + individual scores) ──
    frame_results = []
    for i, frame in enumerate(frames):
        ts = timestamps[i] if i < len(timestamps) else None
        frame_results.append({
            "index": i,
            "timestamp": ts,                       # seconds into video, or None
            "timestamp_label": _format_timestamp(ts),
            "has_face": faces_detected[i],
            "deepfake_score": df_score_by_index[i],  # None if no face on this frame
            "ai_generated_score": aigc_scores[i] if i < len(aigc_scores) else None,
        })

    # ---- print statements ----
    print("\n" + "=" * 50)
    print("[HAQQ MEDIA PIPELINE RESULTS]")
    print(f"Extraction Method:     {extraction_method}")
    print(f"Total Frames Analyzed: {len(frames)}")
    print(f"Frames with Faces:     {sum(faces_detected)} ({pct_faces:.1f}%)")
    print(f"Avg Deepfake Score:    {avg_df_prob:.3f} (GenD)")
    print(f"Avg AI-Gen Score:      {avg_ai_prob:.3f} (SigLIP)")
    print(f"FINAL VERDICT:         {result['verdict'].upper()} (Conf: {result['confidence']:.3f})")
    for fr in frame_results:
        df_str = f"{fr['deepfake_score']:.3f}" if fr['deepfake_score'] is not None else "  —  "
        ai_str = f"{fr['ai_generated_score']:.3f}" if fr['ai_generated_score'] is not None else "  —  "
        print(f"  Frame {fr['index']} [t={fr['timestamp_label']:>8}]: "
              f"face={fr['has_face']!s:<5} df={df_str} ai={ai_str}")
    print("=" * 50 + "\n")
    # -----------------------------------------

    result["metadata"] = {
        "avg_df_prob": avg_df_prob,
        "avg_ai_prob": avg_ai_prob,
        "faces_detected": sum(faces_detected),
        "extraction_method": extraction_method,
        "frames": frame_results,
        "saved_frames_dir": saved_frames_dir,
    }
    
    elapsed_ms = (time.time() - start_time) * 1000
    axiom_logger.log_media_detection_event({
        "request_id": request_id,
        "media_type": "video" if extraction_method == "client-side-frames" else "image",
        "detection_result": result["verdict"],
        "confidence": result["confidence"],
        "latency_ms": elapsed_ms,
        "model_used": "GenD" if faces_detected else "SigLIP",
        "platform": extract_platform(request.post_permalink or request.video_url or request.image_url)
    })
    
    return result