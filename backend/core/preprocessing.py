import os
from datetime import datetime

import cv2
import numpy as np
from PIL import Image, ImageFilter



# Frames get saved here for manual inspection — see save_frames_to_disk()
FRAME_DEBUG_DIR = os.path.join(os.getcwd(), "haqq_debug_frames")
os.makedirs(FRAME_DEBUG_DIR, exist_ok=True)


# ─── Subtitle Masking ───────────────────────────────────────────────────────

def _detect_content_bounds(img_array: np.ndarray, black_threshold: int = 15) -> tuple[int, int]:
    """
    Scans row-by-row from the top and bottom of a frame to find the first row
    that is NOT an almost-pure-black letterbox bar.

    Returns (content_top, content_bottom) pixel coordinates.
    Falls back to (0, total_height) when the entire frame is very dark
    (e.g. a night scene) to avoid over-cropping genuine content.

    Args:
        img_array:       HxWxC numpy array (uint8).
        black_threshold: A row whose per-channel mean is below this value is
                         considered a black bar (0-255 scale).
    """
    h = img_array.shape[0]
    row_means = img_array.mean(axis=(1, 2))  # shape (H,)

    # Scan from top
    content_top = 0
    for i in range(h):
        if row_means[i] > black_threshold:
            content_top = i
            break

    # Scan from bottom
    content_bottom = h
    for i in range(h - 1, -1, -1):
        if row_means[i] > black_threshold:
            content_bottom = i + 1
            break

    # Safety: if the detected content band is suspiciously thin (< 20% of total
    # height), the frame is probably a genuine dark scene — keep the full frame.
    if (content_bottom - content_top) < 0.20 * h:
        return 0, h

    return content_top, content_bottom


def crop_subtitle_banner(pil_image: Image.Image, bottom_crop_pct: float = 0.20) -> Image.Image:
    """
    Letterbox-aware subtitle/ticker removal.

    Phase 1 — Strip black bars:
        Detects the actual content region by scanning for non-black rows and
        removes both the top and bottom letterbox bars so the output frame
        contains only real video pixels.

    Phase 2 — Subtitle crop:
        Removes the bottom `bottom_crop_pct` of the *content* height (not the
        total frame height) where subtitles, news tickers, and lower-thirds
        typically sit.

    This correctly handles:
    - Native vertical videos (no bars — Phase 1 is a no-op, Phase 2 crops as before).
    - Horizontal videos letterboxed inside a portrait container (bars are stripped
      first, then the ticker inside the content area is removed).
    """
    img_array = np.array(pil_image.convert("RGB"))
    w, h = pil_image.size

    # Phase 1: Detect and strip letterbox bars
    content_top, content_bottom = _detect_content_bounds(img_array)

    # Phase 2: Crop subtitle band from the bottom of the content region
    content_h = content_bottom - content_top
    subtitle_band = int(content_h * bottom_crop_pct)
    final_bottom = content_bottom - subtitle_band

    return pil_image.crop((0, content_top, w, final_bottom))


def apply_unsharp_mask(image: Image.Image) -> Image.Image:
    return image.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

def strip_infographic_borders(image: Image.Image) -> Image.Image:
    w, h = image.size
    img_cv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    best_crop = None
    max_area = 0
    
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        area = cw * ch
        if 0.20 * w * h < area < 0.98 * w * h:
            if area > max_area:
                max_area = area
                best_crop = (x, y, x + cw, y + ch)
                
    if best_crop is not None:
        x1, y1, x2, y2 = best_crop
        crop_w = x2 - x1
        crop_h = y2 - y1
        if crop_w >= 150 and crop_h >= 150:
            return image.crop((x1, y1, x2, y2))
            
    # Fallback to subtitle-cropped full frame
    return image.crop((0, 0, w, int(h * 0.80)))

def normalize_instagram_colors(image: Image.Image) -> Image.Image:
    img_arr = np.array(image).astype(np.float32) / 255.0
    mean = img_arr.mean(axis=(0, 1))
    std = img_arr.std(axis=(0, 1)) + 1e-6
    img_norm = (img_arr - mean) / std
    
    imagenet_mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    imagenet_std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    img_norm = img_norm * imagenet_std + imagenet_mean
    img_norm = np.clip(img_norm * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(img_norm)

def preprocess_media(image: Image.Image, platform: str = "generic") -> Image.Image:
    w, h = image.size
    original = image.copy()
    
    if platform == "generic":
        if h > w:
            platform = "tiktok"
        else:
            platform = "facebook"
            
    # 1. ROUTE BY PLATFORM
    if platform == "tiktok":
        image = image.crop((0, int(h * 0.15), int(w * 0.90), int(h * 0.65)))
        image = apply_unsharp_mask(image)
        
    elif platform == "facebook":
        image = strip_infographic_borders(image)
        
    elif platform == "instagram":
        if h > w:
            image = image.crop((0, int(h * 0.12), w, int(h * 0.70)))
        else:
            image = strip_infographic_borders(image)
        image = normalize_instagram_colors(image)
            
    # 2. RESOLUTION SAFEGUARD
    if image.size[0] < 150 or image.size[1] < 150:
        return original.crop((0, 0, w, int(h * 0.80)))
        
    return image

def strip_black_bars(pil_image: Image.Image, black_threshold: int = 15, min_content_ratio: float = 0.3) -> Image.Image:
    img_np = np.array(pil_image.convert("RGB"))
    gray = np.mean(img_np, axis=2)  # avg across channels

    h, w = gray.shape

    # Find rows and columns that have meaningful content (not black)
    row_brightness = gray.mean(axis=1)   # per row
    col_brightness = gray.mean(axis=0)   # per column

    content_rows = np.where(row_brightness > black_threshold)[0]
    content_cols = np.where(col_brightness > black_threshold)[0]

    if len(content_rows) == 0 or len(content_cols) == 0:
        return pil_image  # fully black frame, return as-is

    y1, y2 = int(content_rows[0]), int(content_rows[-1])
    x1, x2 = int(content_cols[0]), int(content_cols[-1])

    crop_h = y2 - y1
    crop_w = x2 - x1

    # Safety: don't return a tiny sliver
    if crop_h < h * min_content_ratio or crop_w < w * min_content_ratio:
        return pil_image

    return pil_image.crop((x1, y1, x2, y2))


# ─── Score Fusion ───────────────────────────────────────────────────────────

def robust_xor_fusion(
    frames: list,
    faces_detected: list[bool],
    df_score_by_index: list[float | None],
    aigc_scores: list[float],
    df_threshold: float = 0.90,
    ai_threshold: float = 0.80
) -> dict:
    """
    Robust XOR Fusion:
    - Uses mean averaging across all frames so that a high-confidence verdict
      requires consistently high scores throughout the video, not just peak frames.
    - Applies calibrated thresholds (df_threshold=0.90, ai_threshold=0.80).
    - Surfaced label is determined by the highest firing signal.
    """
    # 1. Deepfake Pooling: Mean average across all valid face frames
    valid_df_scores = [score for score in df_score_by_index if score is not None]

    if valid_df_scores:
        avg_df_prob = float(np.mean(valid_df_scores))
    else:
        avg_df_prob = 0.0

    # 2. AIGC Pooling: Mean average across all frames
    if aigc_scores:
        avg_ai_prob = float(np.mean(aigc_scores))
    else:
        avg_ai_prob = 0.0

    # 3. Threshold Check
    df_fires = avg_df_prob >= df_threshold
    ai_fires = avg_ai_prob >= ai_threshold

    # 4. Winning Signal Selection
    if not df_fires and not ai_fires:
        verdict = "real"
        confidence = 1.0 - max(avg_df_prob, avg_ai_prob)
        explanation = "لم يتم اكتشاف تلاعب في الوجوه أو توليد بالذكاء الاصطناعي."
    elif df_fires and (not ai_fires or avg_df_prob >= avg_ai_prob):
        verdict = "manipulated"
        confidence = avg_df_prob
        explanation = "تم اكتشاف تلاعب في الوجوه (Deepfake)."
    else:
        verdict = "ai_generated"
        confidence = avg_ai_prob
        explanation = "المحتوى مولد بالذكاء الاصطناعي."

    return {
        "verdict": verdict,
        "confidence": confidence,
        "explanation": explanation,
        "sources": []
    }


# ─── Debug / Disk Utilities ─────────────────────────────────────────────────

def save_frames_to_disk(frames: list, timestamps: list, url: str) -> str | None:
    """
    Saves every extracted frame as a real PNG file on disk, so you can
    open them directly and see exactly what the pipeline captured —
    useful for confirming whether frames are genuinely different
    (spread across the video) or suspiciously identical (a sign of the
    misclassification/black-box bugs we've been chasing).

    Returns the folder path where this run's frames were saved, or None if disabled.
    """
    if os.environ.get("ENABLE_DEBUG_FRAMES", "false").lower() != "true":
        return None

    # One subfolder per request, named by timestamp + a short hash of
    # the URL so repeated runs don't overwrite each other.
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
