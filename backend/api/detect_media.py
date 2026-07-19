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
from playwright.async_api import async_playwright

from backend.api.schemas import DetectMediaRequest

from io import BytesIO

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


class NotAVideoError(Exception):
    """
    Raised when a URL passed in as a video never produces any decodable
    video data at all (readyState stays 0) — a hard signal that it's
    actually a static image (e.g. Facebook's photo-CDN path being sent
    as videoUrl), not a slow-loading real video. Callers should catch
    this specifically and retry via the image-analysis path instead of
    treating it as a failed video extraction.
    """
    pass


async def _check_content_type(url: str, timeout: float = 6.0) -> str:
    """
    Cheap HEAD request to get the server's OWN reported Content-Type
    before deciding how to handle a URL.

    NOTE: this is informational only now, NOT a hard skip signal. In
    practice, Facebook's CDN sometimes serves a static thumbnail
    (image/jpeg) to an unauthenticated HEAD request even when the
    underlying post is a real video — the HEAD request doesn't carry
    the poster's session, so it gets the same fallback preview a
    logged-out visitor would see. Treating that as proof "this isn't a
    video" produced false negatives (real videos silently analyzed as
    a single thumbnail frame). The route handler now uses this only to
    decide whether to PREFER the permalink route when available; it no
    longer skips Playwright outright when a permalink is missing.
    Playwright's own NotAVideoError (readyState stuck at 0 inside a
    real browser) is the actual, trustworthy signal for "this really
    isn't a video."

    Returns "" if the HEAD request fails or the header is missing —
    callers should treat that as inconclusive, not as proof either way.
    """
    try:
        async with httpx.AsyncClient(headers=DOWNLOAD_HEADERS, timeout=timeout, follow_redirects=True) as client:
            resp = await client.head(url)
            return resp.headers.get("content-type", "").lower()
    except Exception as e:
        print(f"[HAQQ] Content-Type pre-check failed ({e}) — treating as inconclusive")
        return ""


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


# ─── 2. Playwright frame + timestamp extraction (ONLY extraction method) ───
async def _extract_frames_playwright(url: str, is_permalink: bool = False) -> list[dict]:
    """
    Uses a real headless Chromium browser (via Playwright) to load and
    play the video, capturing frames via SCREENSHOT of the <video>
    element. This is the ONLY extraction path — no httpx download, no
    temp file, no cv2.VideoCapture on a local file. The full-download
    fast path was removed: CDNs (Facebook in particular) routinely serve
    a truncated/single-segment file to a bare GET, which decoded to a
    single unusable frame far more often than it produced a real result.
    Playwright's real browser TLS/HTTP fingerprint and correct
    Range-request handling for fMP4/DASH segments avoids that failure
    mode entirely.

    Returns a list of dicts: {"image": PIL.Image, "timestamp": float | None}
    "timestamp" is the video's ACTUAL currentTime (seconds) at capture —
    read back AFTER seeking completes, not the requested value, since
    browsers snap seeks to the nearest keyframe and the true position
    can differ slightly from what was requested. None only in the
    fallback branch where duration never became available (in which
    case the number is an estimate of elapsed playback time, not a real
    seek position — see the `else` branch below).

    FRAME DISTRIBUTION: once the video's metadata loads (duration
    known), playback is paused and we SEEK to N_FRAMES evenly-spaced
    timestamps across the whole clip (inset slightly from both ends to
    avoid black intro/outro frames), waiting for each browser-native
    `seeked` event before screenshotting. This guarantees coverage of
    the entire video regardless of length or network speed.

    is_permalink=True: url is a post/reel PAGE url — navigate there and
    find the <video> element already on the page.
    is_permalink=False: url is a direct CDN video file link — wrapped in
    a minimal HTML page with an explicit <video> tag.

    NOTE: this coroutine must be run inside an event loop that supports
    subprocess creation (Playwright launches Chromium as a subprocess).
    On Windows, uvicorn forces WindowsSelectorEventLoopPolicy on its own
    main loop regardless of what's set at module import time — so this
    function is never called directly from a route handler. See
    _extract_frames_playwright_isolated() below, which runs this inside
    a dedicated thread with its own Proactor-policy loop instead.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--autoplay-policy=no-user-gesture-required",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
            viewport={"width": 390, "height": 844},
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
        )
        page = await context.new_page()

        try:
            if is_permalink:
                if "facebook.com" in url and "m.facebook.com" not in url:
                    url = url.replace("www.facebook.com", "m.facebook.com").replace("web.facebook.com", "m.facebook.com")
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3000)
                await page.evaluate("window.scrollBy(0, 500)")

                # Try to click the center to dismiss overlays or hit play
                try:
                    await page.mouse.click(200, 300)
                    await page.wait_for_timeout(1000)
                except:
                    pass

                video = page.locator("video").first
                await video.wait_for(state="attached", timeout=30000)
            else:
                html = (
                    f'<video id="v" autoplay muted playsinline src="{url}" '
                    f'style="width:800px;height:800px;object-fit:contain;background:#000"></video>'
                )
                await page.set_content(html, wait_until="domcontentloaded")
                video = page.locator("#v")
                await video.wait_for(state="attached", timeout=15000)

            await page.evaluate(
                "() => { const v = document.querySelector('video'); "
                "if (v) { v.muted = true; v.play().catch(() => {}); } }"
            )

            # Wait for metadata. Facebook-style CDNs frequently serve
            # video via MSE (Media Source Extensions), where
            # video.duration starts as Infinity/NaN even after
            # loadedmetadata fires — isFinite() correctly rejects that,
            # but the FIX for MSE streams is well-established: seeking
            # near the end of the (currently-infinite) timeline forces
            # MSE to finalize the real duration, after which a
            # 'durationchange' event reports the true finite value. This
            # is why earlier attempts fell through to the elapsed-time
            # fallback (fixed 0.4s steps, all clustered near the start)
            # instead of real full-duration distribution — duration was
            # never finite, so has_duration was always False even though
            # metadata itself had loaded fine.
            has_duration = await page.evaluate(
                """() => new Promise((resolve) => {
                    const v = document.querySelector('video');
                    if (!v) return resolve(false);

                    const isReal = () => isFinite(v.duration) && v.duration > 0;

                    if (v.readyState >= 1 && isReal()) return resolve(true);

                    const tryForceFinalize = () => {
                        // MSE-specific trick: seeking near the end of an
                        // Infinity-duration stream forces the browser to
                        // finalize the real duration.
                        try { v.currentTime = 1e9; } catch (e) {}
                    };

                    const onDurationChange = () => {
                        if (isReal()) {
                            v.removeEventListener('durationchange', onDurationChange);
                            v.removeEventListener('loadedmetadata', onLoadedMeta);
                            resolve(true);
                        }
                    };
                    const onLoadedMeta = () => {
                        if (isReal()) {
                            v.removeEventListener('durationchange', onDurationChange);
                            v.removeEventListener('loadedmetadata', onLoadedMeta);
                            resolve(true);
                        } else {
                            // Metadata loaded but duration is still
                            // Infinity/NaN (the MSE case) — nudge it.
                            tryForceFinalize();
                        }
                    };

                    v.addEventListener('durationchange', onDurationChange);
                    v.addEventListener('loadedmetadata', onLoadedMeta);

                    // Also attempt the nudge proactively in case
                    // loadedmetadata already fired before these
                    // listeners were attached.
                    tryForceFinalize();

                    setTimeout(() => {
                        v.removeEventListener('durationchange', onDurationChange);
                        v.removeEventListener('loadedmetadata', onLoadedMeta);
                        resolve(isReal());
                    }, 15000);
                })"""
            )

            raw_duration_debug = await page.evaluate(
                "() => { const v = document.querySelector('video'); "
                "return v ? {duration: v.duration, readyState: v.readyState, isFinite: isFinite(v.duration)} : null; }"
            )
            print(f"[HAQQ] has_duration={has_duration} | raw video state: {raw_duration_debug}")

            # readyState=0 (HAVE_NOTHING) means the browser has decoded
            # ZERO frames of actual video data — not "still loading
            # slowly", but "this element never received anything
            # resembling a video stream at all". This is a hard,
            # unambiguous signal (unlike the has_duration/MSE case,
            # which can legitimately take time to resolve) — it's the
            # exact symptom of a static image URL (e.g. Facebook's
            # t51.* photo CDN path) being passed in as a video URL.
            # Continuing past this point would just screenshot the
            # browser's empty video placeholder box repeatedly — which
            # is IDENTICAL every time regardless of the real post
            # content, producing a constant, meaningless "REAL, no
            # face, ai=0.013" result no matter what was actually posted.
            # Raise a specific, distinctly-named error so the caller can
            # redirect this to the working single-image analysis path
            # instead of silently analyzing a black box.
            if raw_duration_debug and raw_duration_debug.get("readyState", 0) == 0:
                raise NotAVideoError(
                    f"Video element never received any decodable data "
                    f"(readyState stayed 0) — this URL is very likely a "
                    f"static image, not a real video: {url[:100]}"
                )

            if has_duration:
                # Reset back to the start — the MSE finalize step above
                # may have left currentTime near the (huge) seek target.
                await page.evaluate(
                    "() => { const v = document.querySelector('video'); if (v) v.currentTime = 0; }"
                )
                await page.wait_for_timeout(150)

            frames: list[dict] = []

            if has_duration:
                duration = await page.evaluate(
                    "() => document.querySelector('video').duration"
                )
                print(f"[HAQQ] Using real seeking — duration={duration:.2f}s")

                # Inset slightly from both ends to dodge black leading/
                # trailing frames (intro fades, end-cards, etc).
                inset = min(0.15 * duration, 0.5)
                start = inset
                end = max(duration - inset, start + 0.01)
                timestamps = [
                    start + i * (end - start) / (N_FRAMES - 1)
                    for i in range(N_FRAMES)
                ]

                await page.evaluate(
                    "() => { const v = document.querySelector('video'); if (v) v.pause(); }"
                )

                for t in timestamps:
                    seeked = await page.evaluate(
                        """(t) => new Promise((resolve) => {
                            const v = document.querySelector('video');
                            if (!v) return resolve(false);
                            const onSeeked = () => {
                                v.removeEventListener('seeked', onSeeked);
                                resolve(true);
                            };
                            v.addEventListener('seeked', onSeeked);
                            v.currentTime = t;
                            setTimeout(() => resolve(false), 3000);
                        })""",
                        t,
                    )
                    if seeked:
                        # Small extra wait so the decoded frame actually
                        # paints to the compositor before we screenshot.
                        await page.wait_for_timeout(120)

                    # Read back the ACTUAL currentTime after seeking —
                    # browsers snap to the nearest keyframe, so the true
                    # timestamp can differ slightly from the requested `t`.
                    actual_t = await page.evaluate(
                        "() => document.querySelector('video').currentTime"
                    )
                    screenshot_bytes = await video.screenshot(type="png")
                    frames.append({
                        "image": Image.open(BytesIO(screenshot_bytes)).convert("RGB"),
                        "timestamp": float(actual_t),
                    })

            else:
                # Duration never became available — fall back to
                # playback-timed sampling. Timestamps here are only an
                # ESTIMATE of elapsed wall-clock time since playback
                # started (not a true seek position). readyState=0 at
                # this point means NO data had loaded yet at all, so
                # this branch is also giving the video more real wall-
                # clock time to actually start buffering before we
                # begin capturing — not just spacing captures further
                # apart once they start.
                print("[HAQQ] ⚠️ FALLBACK BRANCH — duration never resolved, "
                      "using elapsed-time estimate (NOT real full-video seeking)")

                FALLBACK_INITIAL_WAIT_MS = 2000  # was 800 — more time for
                                                  # the video to actually
                                                  # start loading real
                                                  # data before capturing
                FALLBACK_FRAME_GAP_MS = 1500     # was 400 — spreads the 8
                                                  # captures across ~12s of
                                                  # elapsed playback instead
                                                  # of ~3.2s

                await page.wait_for_timeout(FALLBACK_INITIAL_WAIT_MS)
                elapsed = FALLBACK_INITIAL_WAIT_MS / 1000
                for _ in range(N_FRAMES):
                    screenshot_bytes = await video.screenshot(type="png")
                    frames.append({
                        "image": Image.open(BytesIO(screenshot_bytes)).convert("RGB"),
                        "timestamp": elapsed,
                    })
                    await page.wait_for_timeout(FALLBACK_FRAME_GAP_MS)
                    elapsed += FALLBACK_FRAME_GAP_MS / 1000

            if not frames:
                raise RuntimeError("Playwright captured no frames — <video> never rendered visible content")

            return frames

        finally:
            await context.close()
            await browser.close()


def _run_playwright_in_own_loop(url: str, is_permalink: bool) -> list[dict]:
    """
    Creates a BRAND NEW event loop with WindowsProactorEventLoopPolicy,
    inside its own dedicated thread, fully isolated from uvicorn's main
    event loop. Necessary because uvicorn itself forces
    WindowsSelectorEventLoopPolicy on Windows internally (for --reload/
    signal-handling compatibility) — overriding whatever policy was set
    at module import time in main.py, which is why setting the policy
    there alone doesn't fix Playwright's subprocess launch. Since we
    can't safely change uvicorn's main loop policy without risking
    --reload breakage, Playwright instead gets a private loop that
    actually supports subprocess creation.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_extract_frames_playwright(url, is_permalink))
    finally:
        loop.close()


async def _extract_frames_playwright_isolated(url: str, is_permalink: bool = False) -> list[dict]:
    """
    Runs the actual Playwright extraction in a separate thread with its
    own Proactor-policy event loop, instead of directly on uvicorn's
    main loop. This is the function _get_frames() actually calls.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run_playwright_in_own_loop, url, is_permalink)
        return await asyncio.wrap_future(future)


async def _get_frames(target_url: str, is_permalink: bool = False) -> tuple[list, str]:
    """
    Returns (frame_records, method_used). frame_records is a list of
    {"image": PIL.Image, "timestamp": float | None} dicts, in playback
    order. Playwright is the ONLY extraction method — no download
    fast-path, no fallback chain. If it fails, the request fails.
    """
    try:
        frame_records = await _extract_frames_playwright_isolated(target_url, is_permalink=is_permalink)
        print(f"[HAQQ] Playwright extraction succeeded — {len(frame_records)} frames")
        return frame_records, "playwright"
    except NotAVideoError:
        # Don't wrap this one — the route handler needs to catch this
        # SPECIFIC exception type to redirect to image analysis instead
        # of failing the whole request.
        raise
    except Exception as e:
        print(f"[HAQQ] Playwright extraction failed: {e}")
        print(traceback.format_exc())
        raise RuntimeError(f"Frame extraction failed: {e}")


def save_frames_to_disk(frames: list, timestamps: list, url: str) -> str:
    """
    Saves every extracted frame as a real PNG file on disk, so you can
    open them directly and see exactly what the pipeline captured —
    useful for confirming whether frames are genuinely different
    (spread across the video) or suspiciously identical (a sign of the
    misclassification/black-box bugs we've been chasing).

    Returns the folder path where this run's frames were saved.
    """
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


# ─── 3. Route Handler ───
async def _download_single_image(url: str) -> Image.Image:
    """Shared by the explicit image_url path and the NotAVideoError
    redirect (when a video_url turns out to actually be a static image,
    e.g. Facebook's t51.* photo CDN path sent in by mistake)."""
    resp = await asyncio.to_thread(
        http_requests.get, url, headers=DOWNLOAD_HEADERS, timeout=10
    )
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert("RGB")


@router.post("/detect-media")
async def detect_media(request: DetectMediaRequest, req: Request):
    yunet = req.app.state.yunet
    gend_model = req.app.state.gend_model
    aigc_pipeline = req.app.state.aigc_pipeline

    frames = None
    timestamps: list = []
    extraction_method = None
    target_url = None
    is_permalink = False

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
                extraction_method = "captureVisibleTab"
                timestamps = [None] * len(frames)
                target_url = None # Skip Playwright branch entirely
                saved_frames_dir = save_frames_to_disk(frames, timestamps, "client-side-capture")
                print(f"[HAQQ] Successfully decoded {len(frames)} client-side frames, saved to {saved_frames_dir}")
        except Exception as e:
            print(f"[HAQQ] Failed to decode client frames: {e}")
            frames = None  # Fall through to other paths

    if request.video_url and not frames:
        target_url, is_permalink = request.video_url, False
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
            if request.post_permalink:
                print(f"[HAQQ] Content-Type looks like an image ({content_type}) — "
                      f"preferring post_permalink with Playwright, since the permalink page "
                      f"is more likely to yield the real video than a bare CDN URL.")
                target_url, is_permalink = request.post_permalink, True
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

    elif request.post_permalink:
        target_url, is_permalink = request.post_permalink, True
        print(f"[HAQQ] Processing post permalink: {target_url[:80]}...")

    elif request.image_url:
        print(f"[HAQQ] Processing image URL: {request.image_url[:80]}...")
        try:
            img = await _download_single_image(request.image_url)
            frames, extraction_method = [img], "single-image"
            timestamps = [None]
        except Exception as e:
            print(f"[HAQQ] Image download/decode failed: {e}")
            print(traceback.format_exc())
            raise HTTPException(status_code=400, detail=f"Could not download image: {e}")

    else:
        return {"verdict": "inconclusive", "confidence": 0.0, "explanation": "لا توجد وسائط لتحليلها.", "sources": []}

    saved_frames_dir = None

    if target_url:
        try:
            frame_records, extraction_method = await _get_frames(target_url, is_permalink)
            frames = [fr["image"] for fr in frame_records]
            timestamps = [fr["timestamp"] for fr in frame_records]
            saved_frames_dir = save_frames_to_disk(frames, timestamps, target_url)
        except NotAVideoError as e:
            # This is now the ONLY trusted signal that a video_url is
            # actually a static image — readyState stayed 0 inside a
            # real browser, not just a HEAD-request guess. Re-route to
            # the SAME image path used above, instead of repeatedly
            # screenshotting an empty video box and returning an
            # identical, meaningless result every time.
            print(f"[HAQQ] {e} — redirecting to single-image analysis instead")
            try:
                img = await _download_single_image(target_url)
                frames, extraction_method = [img], "single-image"
                timestamps = [None]
                saved_frames_dir = save_frames_to_disk(frames, timestamps, target_url)
            except Exception as img_e:
                print(f"[HAQQ] Redirect-to-image also failed: {img_e}")
                raise HTTPException(
                    status_code=400,
                    detail=f"URL was not a real video and could not be analyzed as an image either: {img_e}"
                )
        except Exception as e:
            print(f"[HAQQ] Playwright failed: {e}. Trying to fallback to image_url...")
            if request.video_url and request.video_url != target_url:
                try:
                    img = await _download_single_image(request.video_url)
                    frames, extraction_method = [img], "single-image"
                    timestamps = [None]
                    saved_frames_dir = save_frames_to_disk(frames, timestamps, request.video_url)
                except Exception as img_e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Playwright failed ({e}), and fallback image download also failed: {img_e}"
                    )
            else:
                raise HTTPException(status_code=400, detail=f"Could not extract frames: {e}")


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
    return result