import base64
import traceback
from io import BytesIO

import cv2
import numpy as np
import torch
from PIL import Image
from fastapi import APIRouter, Request, HTTPException

from backend.api.schemas import DetectMediaRequest
from backend.api.playwright_utils import (
    NotAVideoError,
    _check_content_type,
    _get_frames,
    _download_single_image,
)
from backend.core.preprocessing import (
    preprocess_media,
    strip_black_bars,
    save_frames_to_disk,
    _format_timestamp,
    robust_xor_fusion,
)

router = APIRouter()
from backend.api.rate_limiter import limiter, global_key_func

# ─── Route Handler ──────────────────────────────────────────────────────────

from backend.observability.axiom_logger import axiom_logger, extract_platform
import time

@router.post("/detect-media")
@limiter.limit("10/day")
@limiter.limit("2/second", key_func=global_key_func)
async def detect_media(payload: DetectMediaRequest, request: Request):
    start_time = time.time()
    request_id = getattr(request.state, 'request_id', 'unknown')
    
    yunet = request.app.state.yunet
    gend_model = request.app.state.gend_model
    platform = payload.platform or "generic"

    frames = None
    timestamps: list = []
    extraction_method = None
    target_url = None
    is_permalink = False


    

    if frames:
        # Already have frames decoded from client-side capture
        pass
    elif payload.video_url:
        target_url, is_permalink = payload.video_url, False
        print(f"[HAQQ] Processing video URL: {target_url[:80]}...")

        # Content-Type is now used ONLY to decide whether to PREFER the
        # permalink route — it is no longer a hard "skip Playwright"
        # signal. Facebook's CDN can serve a plain thumbnail
        # (image/jpeg) to an unauthenticated HEAD request even when the
        # underlying post is a genuine video, since the HEAD request
        # doesn't carry the poster's session. Trusting that as proof
        # "not a video" produced false negatives — real videos silently
        # reduced to a single thumbnail frame. Playwright's own
        # NotAVideoError (readyState stuck at 0 in a REAL browser) is
        # the trustworthy signal now; it's checked further below no
        # matter which path we take here.
        content_type = await _check_content_type(target_url)
        if content_type.startswith("image/"):
            if payload.post_permalink:
                print(f"[HAQQ] Content-Type looks like an image ({content_type}) — "
                      f"preferring post_permalink with Playwright, since the permalink page "
                      f"is more likely to yield the real video than a bare CDN URL.")
                target_url, is_permalink = payload.post_permalink, True
            else:
                print(f"[HAQQ] Content-Type looks like an image ({content_type}) but no "
                      f"post_permalink is available — NOT skipping Playwright. Attempting "
                      f"real extraction on the raw video_url anyway; will only fall back to "
                      f"single-image analysis if Playwright itself confirms via NotAVideoError "
                      f"(readyState stuck at 0).")
                # target_url/is_permalink intentionally left as (video_url, False) —
                # fall through to the Playwright attempt below instead of
                # short-circuiting to single-image here.
        elif content_type and not content_type.startswith("video/"):
            print(f"[HAQQ] Content-Type is neither image/* nor video/* ({content_type}) — "
                  f"proceeding with Playwright anyway, but this is worth investigating "
                  f"if it keeps happening")

    elif payload.post_permalink:
        target_url, is_permalink = payload.post_permalink, True
        print(f"[HAQQ] Processing post permalink: {target_url[:80]}...")

    elif payload.image_url:
        print(f"[HAQQ] Processing image URL: {payload.image_url[:80]}...")
        try:
            img = await _download_single_image(payload.image_url)
            frames, extraction_method = [img], "single-image"
            timestamps = [None]
            saved_frames_dir = save_frames_to_disk(frames, timestamps, payload.image_url)
        except Exception as e:
            print(f"[HAQQ] Image download/decode failed: {e}")
            print(traceback.format_exc())
            raise HTTPException(status_code=400, detail=f"Could not download image: {e}")

    else:
        msg = "No media available to analyze." if payload.lang == "en" else "لا توجد وسائط لتحليلها."
        return {"verdict": "inconclusive", "confidence": 0.0, "explanation": msg, "sources": []}

    saved_frames_dir = None

    if target_url:
        try:
            frame_records, extraction_method = await _get_frames(target_url, is_permalink)
            frames = [fr["image"] for fr in frame_records]
            timestamps = [fr["timestamp"] for fr in frame_records]
            saved_frames_dir = save_frames_to_disk(frames, timestamps, target_url)
        except NotAVideoError as e:
            # The page didn't have a video, or it was a static image pretending to be one.
            print(f"[HAQQ] {e} — redirecting to single-image analysis instead")
            fallback_url = payload.image_url if is_permalink else target_url
            if not fallback_url:
                fallback_url = payload.image_url or payload.video_url

            try:
                print(f"[HAQQ] Using fallback URL for image analysis: {fallback_url[:80]}")
                img = await _download_single_image(fallback_url)
                frames, extraction_method = [img], "single-image"
                timestamps = [None]
                saved_frames_dir = save_frames_to_disk(frames, timestamps, fallback_url)
            except Exception as img_e:
                print(f"[HAQQ] Redirect-to-image also failed: {img_e}")
                raise HTTPException(
                    status_code=400,
                    detail=f"URL was not a real video and could not be analyzed as an image either: {img_e}"
                )
        except Exception as e:
            print(f"[HAQQ] Playwright failed: {e}. Trying to fallback to image_url...")
            fallback_url = payload.image_url or (payload.video_url if payload.video_url and payload.video_url != target_url else None)
            if fallback_url:
                try:
                    print(f"[HAQQ] Using fallback URL for image analysis: {fallback_url[:80]}")
                    img = await _download_single_image(fallback_url)
                    frames, extraction_method = [img], "single-image"
                    timestamps = [None]
                    saved_frames_dir = save_frames_to_disk(frames, timestamps, fallback_url)
                except Exception as img_e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Playwright failed ({e}), and fallback image download also failed: {img_e}"
                    )
            else:
                raise HTTPException(status_code=400, detail=f"Could not extract frames: {e}")

    # Preprocess frames (strip UI overlays, banners, and apply sharpening)
    frames = [preprocess_media(f, platform) for f in frames]

    # ── 2. YuNet Face Detection (Expanded Margin: 40) ─────────────────────
    processed_images = []
    faces_detected = []
    crop_sizes = []
    margin = 40

    for frame in frames:
        frame_cv = cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR)
        h, w, _ = frame_cv.shape
        yunet.setInputSize((w, h))

        _, faces = yunet.detect(frame_cv)

        if faces is not None and len(faces) == 1:
            box = faces[0][:4].astype(int)
            x, y, bw, bh = box
            x1 = max(0, x - margin)
            y1 = max(0, y - margin)
            x2 = min(w, x + bw + margin)
            y2 = min(h, y + bh + margin)

            face_crop = frame.crop((x1, y1, x2, y2))
            processed_images.append(face_crop)
            faces_detected.append(True)
            crop_sizes.append((x2 - x1, y2 - y1))
        elif faces is not None and len(faces) > 1:
            # If multiple faces are detected, infer the deepfake model with the full frame
            processed_images.append(frame)
            faces_detected.append(True)
            crop_sizes.append(None)
        else:
            processed_images.append(frame)
            faces_detected.append(False)
            crop_sizes.append(None)

    # ── 3. GenD Inference (Only on Face/Full Frames) ──────────────────────
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
            score = float(p[1])
            # Texture Guard for Instagram Beauty Filters
            if platform == "instagram":
                face_crop = processed_images[idx]
                gray_face = cv2.cvtColor(np.array(face_crop), cv2.COLOR_RGB2GRAY)
                face_var = cv2.Laplacian(gray_face, cv2.CV_64F).var()
                if face_var < 100.0:
                    penalty = max(0.3, face_var / 100.0)
                    score = score * penalty
            df_score_by_index[idx] = score

    deepfake_scores = [s for s in df_score_by_index if s is not None]
    avg_df_prob = float(np.mean(deepfake_scores)) if deepfake_scores else 0.0

    # ── 4. Non-Destructive Pre-processing: Photographic ROI for AIGC ──────
    roi_frames = [strip_black_bars(f) for f in frames]

    # ── 5. AIGC Inference (ConvNeXt only) ─────────────────────────────────
    convnext_model = request.app.state.convnext_model
    convnext_transform = request.app.state.convnext_transform

    # Ensure model is on the correct device (GPU if available)
    model_device = gend_model.device
    convnext_model.to(model_device)

    aigc_scores = []
    for frame in roi_frames:
        tensor = convnext_transform(frame.convert("RGB")).unsqueeze(0).to(model_device)
        with torch.no_grad():
            logits = convnext_model(tensor)
            probs = torch.softmax(logits, dim=-1).cpu().numpy()[0]
        aigc_scores.append(float(probs[1]))

    avg_ai_prob = float(np.mean(aigc_scores)) if aigc_scores else 0.0

    # ── 6. Dynamic Threshold Adjustment ──────────────────────────────────
    df_threshold = 0.90
    ai_threshold = 0.80

    first_frame = frames[0]
    fw, fh = first_frame.size
    is_vertical = fh > fw

    avg_crop_height = np.mean([cs[1] for cs in crop_sizes if cs is not None]) if any(crop_sizes) else 0
    if is_vertical and len(frames) > 1:
        effective_df_threshold = max(df_threshold, 0.98)
    elif 0 < avg_crop_height < 150:
        effective_df_threshold = max(df_threshold, 0.97)
    else:
        effective_df_threshold = df_threshold

    # ── 7. Robust XOR Fusion ──────────────────────────────────────────────
    pct_faces = (sum(faces_detected) / len(frames)) * 100.0
    result = robust_xor_fusion(
        frames=frames,
        faces_detected=faces_detected,
        df_score_by_index=df_score_by_index,
        aigc_scores=aigc_scores,
        df_threshold=effective_df_threshold,
        ai_threshold=ai_threshold
    )

    # Honest disclosure: a single poster-image analysis is a meaningfully
    # weaker signal than a full multi-frame video scan (can't catch
    # temporal artifacts like inconsistent blinking/lighting across
    # frames) — flag it in the explanation so the confidence number
    # isn't read the same way as a full video analysis.
    explanation = result["explanation"]
    if extraction_method == "single-image":
        if payload.lang == "en":
            explanation += " ⚠️ (Thumbnail analyzed only — full video extraction failed or skipped, accuracy is lower)"
        else:
            explanation += " ⚠️ (تحليل الصورة المصغّرة فقط — لم يتم تحميل الفيديو الفعلي بعد، الدقة أقل من تحليل كامل)"
    result["explanation"] = explanation

    # ── 8. Per-Frame Breakdown ────────────────────────────────────────────
    frame_results = []
    for i, frame in enumerate(frames):
        ts = timestamps[i] if i < len(timestamps) else None
        frame_results.append({
            "index": i,
            "timestamp": ts,                          # seconds into video, or None
            "timestamp_label": _format_timestamp(ts),
            "has_face": faces_detected[i],
            "deepfake_score": df_score_by_index[i],   # None if no face on this frame
            "ai_generated_score": aigc_scores[i] if i < len(aigc_scores) else None,
        })

    # ── 9. Console Summary ────────────────────────────────────────────────
    print("\n" + "=" * 50)
    print("[HAQQ MEDIA PIPELINE RESULTS]")
    print(f"Extraction Method:     {extraction_method}")
    print(f"Total Frames Analyzed: {len(frames)}")
    print(f"Frames with Faces:     {sum(faces_detected)} ({pct_faces:.1f}%)")
    print(f"Avg Deepfake Score:    {avg_df_prob:.3f} (GenD)")
    print(f"Avg AI-Gen Score:      {avg_ai_prob:.3f} (ConvNeXt)")
    print(f"Effective DF Thresh:   {effective_df_threshold:.2f}")
    print(f"FINAL VERDICT:         {result['verdict'].upper()} (Conf: {result['confidence']:.3f})")
    for fr in frame_results:
        df_str = f"{fr['deepfake_score']:.3f}" if fr['deepfake_score'] is not None else "  —  "
        ai_str = f"{fr['ai_generated_score']:.3f}" if fr['ai_generated_score'] is not None else "  —  "
        print(f"  Frame {fr['index']} [t={fr['timestamp_label']:>8}]: "
              f"face={fr['has_face']!s:<5} df={df_str} ai={ai_str}")
    print("=" * 50 + "\n")

    result["metadata"] = {
        "avg_df_prob": avg_df_prob,
        "avg_ai_prob": avg_ai_prob,
        "faces_detected": sum(faces_detected),
        "extraction_method": extraction_method,
        "frames": frame_results,
        "saved_frames_dir": saved_frames_dir,
        "use_convnext": True,
        "effective_df_threshold": effective_df_threshold,
        "avg_crop_height": float(avg_crop_height),
    }
    
    elapsed_ms = (time.time() - start_time) * 1000
    axiom_logger.log_media_detection_event({
        "request_id": request_id,
        "media_type": "video" if target_url and not extraction_method == "single-image" else "image",
        "detection_result": result["verdict"],
        "confidence": result["confidence"],
        "latency_ms": elapsed_ms,
        "model_used": "GenD" if faces_detected else "SigLIP",
        "platform": extract_platform(payload.post_permalink or payload.video_url or payload.image_url)
    })
    
    return result
