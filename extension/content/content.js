// ─── HAQQ Content Script v18 ─────────────────────────────────
// v18 change — DOUBLE-INJECTION GUARD:
//   "Uncaught SyntaxError: Identifier 'PROCESSED_ATTR' has already been
//   declared" means this file's top-level const/function declarations
//   ran twice in the same page (e.g. the manifest injecting it plus a
//   chrome.scripting.executeScript call, or the extension reloading
//   while the tab stayed open). Every top-level declaration is now
//   inside a single IIFE gated on a window-scoped flag, so a second
//   injection becomes a harmless no-op instead of a crash. No other
//   behavior changed — same functions, same logic, just wrapped.
//
// v17 changes:
//   1. DEDUP FIX — v16 accidentally defined makeBtn() and verifyMedia()
//      TWICE in the same file (once in the "SECTION 2/3" blocks with
//      the retry/permalink logic, then again unchanged further down
//      under the old TOOLBAR / AI-MEDIA sections). In JS, the second
//      `function name() {}` declaration silently wins — so the
//      retry/permalink logic was dead code that never actually ran.
//      This file keeps exactly ONE definition of each.
//   2. CLIENT-SIDE FRAME CAPTURE — for "aimedia", we now try to grab
//      frames directly off the live, already-authenticated <video>
//      element in the page (canvas capture) BEFORE falling back to
//      sending a CDN URL/permalink to the backend for server-side
//      extraction. This avoids re-fetching the video without the
//      user's session/cookies entirely for the common case. Falls
//      back to the old getVideoUrlWithRetry() path automatically if
//      capture isn't possible yet (video not mounted) or is blocked
//      (tainted canvas — cross-origin video without permissive CORS).
//
// v16 fix (from screenshot showing Like/Comment/Share crushed by the
// verdict badge): toolbar (buttons) and any resulting badge now live
// inside a single dedicated `.haqq-panel` div, inserted as a SIBLING
// right after the native action row — never merged into it.
//
// v14 fix (carried over): only mark PROCESSED_ATTR once there's
// confirmed text/media to show, so early-in-DOM posts aren't
// permanently skipped before their media mounts.
//
// v13 fix (carried over): Facebook action-row detection primarily via
// `data-ad-rendering-role`, aria-label strings as fallback.
//
// v7 change (carried over): extension-context-invalidation guard.
//
// v6 change (carried over): Facebook gets "content" + "aimedia".
// Instagram/TikTok get "aimedia" ONLY — deliberate product decision.
//
// NOTE: Instagram/TikTok POST_SELECTORs and Facebook's action-row
// markers are best-effort based on commonly-seen attributes as of this
// writing. All three platforms change markup often — verify against
// live pages via devtools before relying on this in production.
(function () {
  if (window.__HAQQ_INJECTED__) {
    console.log("[HAQQ] Already injected on this page — skipping re-init.");
    return;
  }
  window.__HAQQ_INJECTED__ = true;

const PROCESSED_ATTR = "data-haqq-processed";
const DEBUG = true;
function log(...args) { if (DEBUG) console.log("[HAQQ]", ...args); }

// ─── EXTENSION CONTEXT GUARD ───────────────────────────────
function isContextValid() {
  return typeof chrome !== "undefined" && !!chrome.runtime?.id;
}

let contextDead = false;

function killIfContextInvalid() {
  if (contextDead || isContextValid()) return;
  contextDead = true;
  log("Extension context invalidated — stopping observer/interval. Reload the page to restore HAQQ.");
  observer.disconnect();
  clearInterval(scanInterval);
  clearTimeout(scanTimer);
}

const CONTEXT_DEAD_MSG = "تم تحديث الإضافة — أعد تحميل الصفحة";

// ─── PLATFORM DETECTION ────────────────────────────────────
function detectPlatform() {
  const host = location.hostname;
  if (host.includes("instagram.com")) return "instagram";
  if (host.includes("tiktok.com"))    return "tiktok";
  return "facebook";
}
const PLATFORM = detectPlatform();
document.documentElement.setAttribute("data-haqq-platform", PLATFORM);

// Per-platform config. toolbarAnchorSelectors are used only to locate a
// REFERENCE point (the native action row) — the panel is placed right
// after whatever row/element that reference resolves to. Nothing is
// ever inserted INTO these elements anymore (v16).
const PLATFORM_CONFIG = {
  facebook: {
    mediaOnly: false,
    postSelector: [
      'div[aria-posinset]',
      'div[data-pagelet^="TimelineFeedUnit"]',
      'div[data-pagelet^="FeedUnit"]',
      'div[data-pagelet^="PermalinkPost"]',
      'div[data-pagelet^="GroupsFeed"]',
      'div[role="article"]',
      'div[data-testid="fbfeed_story"]',
    ].join(", "),
    seeMoreLabels: ["See more", "اقرأ المزيد", "See More"],
    toolbarAnchorSelectors: [
      '[data-ad-rendering-role="share_button"]',
      '[data-ad-rendering-role="comment_button"]',
      '[data-ad-rendering-role="like_button"]',
      '[aria-label="Send this to friends or post it on your profile."]',
      '[aria-label^="Comment on"]',
      '[aria-label^="React with Like to"]',
    ],
  },
  instagram: {
    mediaOnly: true,
    postSelector: 'article',
    seeMoreLabels: ["more", "... more", "المزيد"],
    toolbarAnchorSelectors: [
      'section svg[aria-label="Send Post"]',
      'section svg[aria-label="Share"]',
      'section svg[aria-label="Comment"]',
      'section svg[aria-label="Like"]',
    ],
    // Where the button icon should sit — right before the bookmark
    // icon. VERIFY LIVE — IG's bookmark aria-label has historically
    // been "Save" but changes.
    buttonInsertBeforeSelectors: [
      'section svg[aria-label="Save"]',
    ],
  },
  tiktok: {
    mediaOnly: true,
    postSelector: [
      '[data-e2e="recommend-list-item-container"]',
      '[data-e2e="feed-video"]',
      '[data-e2e="browse-video"]',
    ].join(", "),
    seeMoreLabels: ["more"],
    toolbarAnchorSelectors: [
      '[data-e2e="share-icon"]',
      '[data-e2e="comment-icon"]',
      '[data-e2e="like-icon"]',
    ],
    // Insert right before the like (heart) icon.
    buttonInsertBeforeSelectors: [
      '[data-e2e="like-icon"]',
    ],
  },
};

const CFG = PLATFORM_CONFIG[PLATFORM];
const POST_SELECTOR = CFG.postSelector;

// ─── VALIDATE ─────────────────────────────────────────────
function isValidPost(el) {
  if (el.hasAttribute(PROCESSED_ATTR)) {
    // Buttons may live either directly in the native icon row/column
    // (Instagram/TikTok) or inside .haqq-panel (Facebook, or wherever
    // insertButtonsIntoActionColumn couldn't find its anchor). Check
    // for the button group ANYWHERE under this post, not just inside
    // a panel — a panel may legitimately not exist yet if no badge has
    // been shown for this post.
    const btnGroup = el.querySelector('.haqq-btn-group[data-haqq-owned]');
    if (!btnGroup || !btnGroup.isConnected) {
      el.removeAttribute(PROCESSED_ATTR);
    } else {
      return false;
    }
  }

  if (PLATFORM === "facebook" && el.getAttribute("aria-posinset")) {
    if (el.parentElement?.closest('[aria-posinset]')) return false;
  }
  if (el.querySelector('[data-visualcompletion="loading-state"]')) return false;
  if (el.querySelector('[aria-label="Loading..."]')) return false;
  if (!CFG.mediaOnly && (!el.innerText || el.innerText.trim().length < 10)) return false;
  return true;
}

// ─── EXTRACT ──────────────────────────────────────────────
function extractAll(postEl) {
  const out = { text: null, imageUrl: null, videoUrl: null, videoPoster: null };

  for (const btn of postEl.querySelectorAll([
    '[role="button"][tabindex="0"]',
    'div[role="button"]',
    'span[role="button"]',
    'button',
  ].join(","))) {
    const t = btn.innerText?.trim();
    if (t && CFG.seeMoreLabels.includes(t)) btn.click();
  }

  if (!CFG.mediaOnly) {
    const msgEl =
      postEl.querySelector('[data-ad-comet-preview="message"]') ||
      postEl.querySelector('[data-ad-preview="message"]')       ||
      postEl.querySelector('[data-ad-rendering-role="story_message"]');

    if (msgEl) {
      const t = msgEl.textContent?.trim().replace(/\n+/g, " ");
      if (t && t.length > 10) out.text = t.slice(0, 3000);
    }

    if (!out.text) {
      for (const b of postEl.querySelectorAll('[dir="auto"]')) {
        const t = b.textContent?.trim().replace(/\n+/g, " ");
        if (t && t.length > 10) { out.text = t.slice(0, 3000); break; }
      }
    }
  }

  const vid = postEl.querySelector("video");
  if (vid) {
    const src    = vid.src || vid.currentSrc || "";
    const poster = vid.getAttribute("poster") || "";
    if (src && src.length > 10) out.videoUrl = src;
    if (poster) out.videoPoster = poster;
    if (!out.videoUrl && poster) out.videoUrl = poster;
    if (!out.videoUrl) out.videoUrl = "video";
    log("Video — src:", src.slice(0, 60), "| poster:", poster.slice(0, 60));
  }

  for (const img of postEl.querySelectorAll("img[src]")) {
    const src = img.src || "";
    if (!src || src.startsWith("data:")) continue;
    if (src.includes("emoji") || src.includes("rsrc.php") || src.includes("static.xx.fbcdn")) continue;
    if (src.includes("_s40x40") || src.includes("_s32x32") || src.includes("_s50x50")) continue;
    if (out.videoPoster && src === out.videoPoster) continue;
    const w = img.naturalWidth  || img.width  || img.offsetWidth  || 0;
    const h = img.naturalHeight || img.height || img.offsetHeight || 0;
    const isPendingLoad = (!img.complete && img.complete !== undefined) || (img.naturalWidth === 0 && img.naturalHeight === 0);
    if (!isPendingLoad && (w < 100 || h < 80)) continue;
    out.imageUrl = src;
    break;
  }

  return out;
}

// ─── CLIENT-SIDE FRAME CAPTURE via captureVisibleTab ──────────────────
// Facebook serves video from cross-origin CDNs, making canvas.drawImage
// + toDataURL throw SecurityError (tainted canvas). Instead, we:
//   1. Scroll the element into view
//   2. Ask the service worker to call chrome.tabs.captureVisibleTab()
//   3. Crop the screenshot to the cropEl's bounding rect on a canvas
// This bypasses ALL CORS because captureVisibleTab captures composited
// pixels, not DOM data.
//
// KEY INSIGHT: Facebook's feed videos render the <video> element in a
// separate DOM layer (often off-screen/zero-size) while displaying the
// content visually inside the post container via CSS compositing.
// We use the POST container (cropEl) for screenshot bounds, and only
// use videoEl for seeking/playback control. This way we always capture
// what's visually on screen, regardless of where <video> is in the DOM.

async function captureFramesFromLiveVideo(videoEl, nFrames = 8, cropEl = null) {
  // cropEl: the element whose on-screen area we crop to.
  // This should be the post container — it's always visible even when
  // the <video> element itself is off-screen.
  // Falls back to videoEl if not provided.
  const displayEl = cropEl || videoEl;
  if (!displayEl || !displayEl.isConnected) return [];

  // Bring the post/display area into view
  displayEl.scrollIntoView({ block: "center", behavior: "instant" });
  await new Promise(r => setTimeout(r, 400));

  const frames = [];

  if (videoEl && videoEl.isConnected) {
    // We have a video element — use it for playback/seek control
    videoEl.muted = true;
    const isPaused = videoEl.paused || videoEl.currentTime === 0;
    const duration = videoEl.duration;
    const hasValidDuration = isFinite(duration) && duration > 0;

    if (isPaused && hasValidDuration) {
      // ─── SEEK-BASED (paused, known duration) ───
      log(`captureFramesFromLiveVideo — seek-based (duration: ${duration.toFixed(2)}s), crop: ${cropEl ? 'postEl' : 'videoEl'}`);
      const savedTime = videoEl.currentTime;

      const inset = Math.min(0.10 * duration, 0.5);
      const timestamps = Array.from({ length: nFrames }, (_, i) =>
        inset + i * (duration - 2 * inset) / (nFrames - 1)
      );

      for (const t of timestamps) {
        if (!displayEl.isConnected) break;
        await seekTo(videoEl, t);
        await new Promise(r => setTimeout(r, 200));
        const frame = await captureElementRect(displayEl);
        if (frame) frames.push({ dataUrl: frame, timestamp: videoEl.currentTime });
      }

      if (videoEl.isConnected) videoEl.currentTime = savedTime;

    } else {
      // ─── INTERVAL-BASED (playing or MSE/unknown duration) ───
      log(`captureFramesFromLiveVideo — interval-based (playing or stream), crop: ${cropEl ? 'postEl' : 'videoEl'}`);
      if (videoEl.paused) {
        try { await videoEl.play().catch(() => {}); } catch (_) {}
      }

      for (let i = 0; i < nFrames; i++) {
        if (!displayEl.isConnected) break;
        const frame = await captureElementRect(displayEl);
        if (frame) frames.push({ dataUrl: frame, timestamp: videoEl.currentTime });
        if (i < nFrames - 1) await new Promise(r => setTimeout(r, 1200));
      }
    }

  } else {
    // ─── NO VIDEO ELEMENT — capture post area as-is ───
    // The video is playing via CSS compositing that our querySelector
    // couldn't reach. Still capture the visible post area at intervals
    // to get the actual displayed frames.
    log(`captureFramesFromLiveVideo — no <video> element; capturing post area directly (${nFrames} frames)`);
    const captureCount = Math.min(nFrames, 5); // fewer captures without seek control
    for (let i = 0; i < captureCount; i++) {
      if (!displayEl.isConnected) break;
      const frame = await captureElementRect(displayEl);
      if (frame) frames.push({ dataUrl: frame, timestamp: null });
      if (i < captureCount - 1) await new Promise(r => setTimeout(r, 1500));
    }
  }

  return frames;
}

// Capture a screenshot of the visible tab and crop to the given element's rect.
// Works for any element — post container, video element, etc.
async function captureElementRect(el) {
  if (!el || !el.isConnected) return null;

  // Prefer cropping the video/media element container if available inside el
  const targetEl = el.querySelector("video") ||
                   el.querySelector('[aria-label*="Video" i]') ||
                   el.querySelector('[aria-label*="فيديو"]') ||
                   el;

  const rect = targetEl.getBoundingClientRect();
  if (rect.width < 10 || rect.height < 10) return null;

  // Ask service worker to screenshot the full visible tab
  const screenshotDataUrl = await new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(
      { type: "HAQQ_CAPTURE_TAB" },
      (response) => {
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        if (!response || response.error) return reject(new Error(response?.error || "No response"));
        resolve(response.dataUrl);
      }
    );
  });

  // Crop to the element's rect, accounting for device pixel ratio
  const dpr = window.devicePixelRatio || 1;

  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      try {
        const dprX = rect.left * dpr;
        const dprY = rect.top * dpr;
        const dprW = rect.width * dpr;
        const dprH = rect.height * dpr;

        // Clamp source rectangle to image boundaries
        const sx = Math.max(0, Math.min(img.width, dprX));
        const sy = Math.max(0, Math.min(img.height, dprY));
        const sRight = Math.max(0, Math.min(img.width, dprX + dprW));
        const sBottom = Math.max(0, Math.min(img.height, dprY + dprH));

        const sw = sRight - sx;
        const sh = sBottom - sy;

        if (sw < 10 || sh < 10) {
          return resolve(null);
        }

        const canvas = document.createElement("canvas");
        canvas.width = Math.round(sw);
        canvas.height = Math.round(sh);
        const ctx = canvas.getContext("2d");
        ctx.drawImage(
          img,
          Math.round(sx), Math.round(sy), Math.round(sw), Math.round(sh),
          0, 0, canvas.width, canvas.height
        );
        resolve(canvas.toDataURL("image/jpeg", 0.85));
      } catch (e) {
        log("captureElementRect crop error:", e.message);
        resolve(null);
      }
    };
    img.onerror = () => resolve(null);
    img.src = screenshotDataUrl;
  });
}

// Keep captureVideoRect as a thin alias for backward compatibility
const captureVideoRect = captureElementRect;

function seekTo(videoEl, t) {
  return new Promise((resolve) => {
    if (!videoEl || !videoEl.isConnected) return resolve();
    const onSeeked = () => { videoEl.removeEventListener("seeked", onSeeked); resolve(); };
    videoEl.addEventListener("seeked", onSeeked);
    videoEl.currentTime = t;
    setTimeout(resolve, 2500); // fallback if seeked never fires
  });
}

// Waits for the video to actually load REAL data (not just the poster)
async function waitForRealVideoData(videoEl, timeoutMs = 8000) {
  if (!videoEl || !videoEl.isConnected) return false;
  const isReady = () => videoEl.readyState >= 2;
  if (isReady()) return true;

  videoEl.muted = true;
  try { await videoEl.play().catch(() => {}); } catch (_) {}

  return new Promise((resolve) => {
    if (isReady()) return resolve(true);
    const onEvt = () => { if (isReady()) { cleanup(); resolve(true); } };
    const cleanup = () => {
      ["loadedmetadata", "loadeddata", "canplay", "durationchange", "playing"].forEach(
        ev => videoEl.removeEventListener(ev, onEvt)
      );
    };
    ["loadedmetadata", "loadeddata", "canplay", "durationchange", "playing"].forEach(
      ev => videoEl.addEventListener(ev, onEvt)
    );
    setTimeout(() => { cleanup(); resolve(isReady()); }, timeoutMs);
  });
}

// ─── VIDEO URL FALLBACK (used only when frame capture isn't possible) ─
// Handles three layered problems, in order:
//
// STAGE 1 — the <video> element may not exist in the DOM yet at all
// (Instagram grid tiles/Reels often render as just a thumbnail <img>
// until interacted with).
//
// STAGE 2 — even once <video> exists, its .src may still be empty or
// blob: until playback actually starts.
//
// STAGE 3 (grid-tile case) — if <video> NEVER appears even after
// synthetic hover events, this is very likely because the tile's
// hover-preview loader checks event.isTrusted, and script-dispatched
// MouseEvents can never report as trusted — a hard browser guarantee.
// In that case, fall back to the tile's own permalink link (its <a
// href>), which points to the real post page — letting the backend
// fetch the real video server-side even though no CDN URL was ever
// obtainable client-side.
async function getVideoUrlWithRetry(postEl, maxRetries = 6, delayMs = 350) {
  const tileLink = postEl.querySelector("a") || postEl;

  postEl.scrollIntoView({ block: "center", behavior: "instant" });
  for (const eventType of ["mouseover", "mouseenter", "pointerenter", "pointerover"]) {
    tileLink.dispatchEvent(new MouseEvent(eventType, { bubbles: true, cancelable: true }));
  }

  for (let attempt = 0; attempt < maxRetries; attempt++) {
    const vid = postEl.querySelector("video");

    if (vid) {
      const src = vid.src || vid.currentSrc || "";
      if (src && !src.startsWith("blob:") && src.length > 10) {
        log(`getVideoUrlWithRetry — real src found on attempt ${attempt + 1}`);
        return { videoUrl: src, videoPoster: vid.getAttribute("poster") || null, foundVideoElement: true };
      }
      if (vid.paused) {
        try {
          vid.muted = true;
          await vid.play().catch(() => {});
        } catch (_) { /* some players block programmatic play */ }
      }
    } else {
      log(`getVideoUrlWithRetry — no <video> element in DOM yet (attempt ${attempt + 1})`);
      for (const eventType of ["mouseover", "mouseenter"]) {
        tileLink.dispatchEvent(new MouseEvent(eventType, { bubbles: true, cancelable: true }));
      }
    }

    if (attempt < maxRetries - 1) {
      await new Promise(r => setTimeout(r, delayMs));
    }
  }

  const vid = postEl.querySelector("video");

  if (!vid) {
    log(`getVideoUrlWithRetry — no <video> element ever appeared after ${maxRetries} attempts, even with synthetic hover — likely blocked by isTrusted. Falling back to permalink.`);

    const linkEl = postEl.querySelector("a[href]");
    if (linkEl) {
      const href = linkEl.getAttribute("href") || "";
      const isPostPermalink = /\/(p|reel|tv)\/[\w-]+/.test(href);
      if (isPostPermalink) {
        const absoluteUrl = new URL(href, location.origin).href;
        log(`getVideoUrlWithRetry — found post permalink instead: ${absoluteUrl}`);
        return { videoUrl: null, videoPoster: null, foundVideoElement: false, postPermalink: absoluteUrl };
      }
    }

    return { videoUrl: null, videoPoster: null, foundVideoElement: false };
  }

  const poster = vid.getAttribute("poster") || null;
  log(`getVideoUrlWithRetry — <video> exists but no real src after ${maxRetries} attempts, falling back to poster`);
  return { videoUrl: poster, videoPoster: poster, foundVideoElement: true, usedPosterFallback: true };
}

// ─── ICONS (shield-check / robot, icon-only) ───────────────
const ICON_STYLE = `style="color:var(--haqq-icon-color, #65676b)"`;

const ICON_CONTENT = `
<svg class="haqq-icon" ${ICON_STYLE} viewBox="0 0 24 24" width="18" height="18" fill="none"
     stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <path d="M12 3l7 3v5c0 5-3.5 8.5-7 10-3.5-1.5-7-5-7-10V6l7-3z"/>
  <path d="M9 12l2 2 4-4"/>
</svg>`.trim();

const ICON_AIMEDIA = `
<svg class="haqq-icon" ${ICON_STYLE} viewBox="0 0 24 24" width="18" height="18" fill="none"
     stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <rect x="4" y="8" width="16" height="12" rx="3"/>
  <path d="M12 8V5"/>
  <circle cx="12" cy="3.5" r="1.2" fill="currentColor" stroke="none"/>
  <circle cx="9" cy="13" r="1.3" fill="currentColor" stroke="none"/>
  <circle cx="15" cy="13" r="1.3" fill="currentColor" stroke="none"/>
  <path d="M9 17h6"/>
  <path d="M2 12h2"/>
  <path d="M20 12h2"/>
</svg>`.trim();

const BTN_DEF = {
  content: { label: ICON_CONTENT, title: "تحقق من النص والصورة معاً" },
  aimedia: { label: ICON_AIMEDIA, title: "كشف الصور/الفيديوهات المولّدة أو المعدّلة بالذكاء الاصطناعي" },
};

// ─── BUTTON (single definition — content + aimedia, frame-capture-first) ──
function makeBtn(type, postEl, content) {
  const def = BTN_DEF[type];

  const btn = document.createElement("button");
  btn.className       = `haqq-btn haqq-btn--${type}`;
  btn.dataset.type    = type;
  btn.dataset.loading = "false";
  btn.innerHTML       = def.label;
  btn.title           = def.title;

  const setLoading = () => {
    btn.classList.add("haqq-btn--loading");
    btn.innerHTML       = `<span class="haqq-spinner"></span>`;
    btn.disabled        = true;
    btn.dataset.loading = "true";
  };

  const setIdle = () => {
    btn.classList.remove("haqq-btn--loading", "haqq-btn--error");
    btn.innerHTML       = def.label;
    btn.disabled         = false;
    btn.dataset.loading = "false";
  };

  const setError = (message) => {
    btn.classList.remove("haqq-btn--loading");
    btn.classList.add("haqq-btn--error");
    btn.innerHTML = "⚠️";
    btn.title     = message;
    setTimeout(() => { setIdle(); btn.title = def.title; }, 3000);
  };

  btn.addEventListener("click", async () => {
    if (btn.dataset.loading === "true") return;

    if (!isContextValid()) {
      killIfContextInvalid();
      setError(CONTEXT_DEAD_MSG);
      return;
    }

    setLoading();

    try {
      const freshContent = extractAll(postEl);

      let finalContent = {
        text:        freshContent.text        ?? content.text,
        imageUrl:    freshContent.imageUrl    ?? content.imageUrl,
        videoUrl:    freshContent.videoUrl    ?? content.videoUrl,
        videoPoster: freshContent.videoPoster ?? content.videoPoster,
      };

      if (type === "aimedia") {
        let frames = null;

        // ── APPROACH: Screenshot the visible post area ──────────────────────────
        // Facebook renders feed videos via GPU compositing — the <video> element
        // is inaccessible or hidden. We don't need it.
        // chrome.tabs.captureVisibleTab() captures fully-composited screen pixels,
        // so whatever the user sees (image, video frame, thumbnail) is captured.
        //
        // For VIDEO posts: detect video player controls in the DOM (seek bar,
        // Play/Pause button) — these ARE accessible even when <video> is not.
        // Take multiple screenshots spaced 1.5s apart so the playing video
        // naturally advances to different frames between captures.
        //
        // For IMAGE posts: 1 screenshot is enough.

        // Step 1: Bring the post into view and let the browser composite it
        postEl.scrollIntoView({ block: "center", behavior: "instant" });
        await new Promise(r => setTimeout(r, 700)); // wait for GPU composite

        // Step 2: Detect whether this is a video or image post by looking at DOM signals
        // (player controls, English & Arabic aria-labels, video tags) — not relying solely on <video> element.
        const isVideoPost = !!(
          postEl.querySelector('[aria-label*="Play" i]') ||
          postEl.querySelector('[aria-label*="Pause" i]') ||
          postEl.querySelector('[aria-label*="Mute" i]') ||
          postEl.querySelector('[aria-label*="Unmute" i]') ||
          postEl.querySelector('[aria-label*="Video" i]') ||
          postEl.querySelector('[aria-label*="تشغيل"]') ||
          postEl.querySelector('[aria-label*="إيقاف"]') ||
          postEl.querySelector('[aria-label*="كتم"]') ||
          postEl.querySelector('[aria-label*="فيديو"]') ||
          postEl.querySelector('[aria-label*="صوت"]') ||
          postEl.querySelector('[role="slider"]') ||
          postEl.querySelector("video") ||
          postEl.querySelector("[data-video-id]") ||
          !!freshContent.videoUrl
        );

        log(`aimedia — isVideoPost=${isVideoPost}`);

        // Try to trigger play/unmute if a <video> element exists so frames advance naturally
        const videoEl = postEl.querySelector("video");
        if (videoEl && isVideoPost) {
          videoEl.muted = true;
          try { await videoEl.play().catch(() => {}); } catch (_) {}
          await new Promise(r => setTimeout(r, 400));
        }

        // Step 3: Take screenshots of the post area.
        // For video: take up to 6 screenshots with 1.5s delays so the playing
        // video advances naturally between captures.
        // For image: 1 screenshot is enough.
        const captureCount = isVideoPost ? 6 : 1;
        const captureDelay = 1500; // ms between frames for video posts

        const captured = [];
        for (let i = 0; i < captureCount; i++) {
          if (i > 0) await new Promise(r => setTimeout(r, captureDelay));

          // Make sure the post is still in view between captures
          if (!postEl.isConnected) break;
          postEl.scrollIntoView({ block: "center", behavior: "instant" });
          await new Promise(r => setTimeout(r, 150));

          try {
            const frame = await captureElementRect(postEl);
            if (frame) {
              captured.push(frame);
              log(`aimedia — ✅ frame ${i + 1}/${captureCount} captured`);
            } else {
              log(`aimedia — ⚠️ frame ${i + 1}/${captureCount} returned null (rect invalid or off-screen)`);
            }
          } catch (e) {
            log(`aimedia — ❌ frame ${i + 1}/${captureCount} error: ${e.message}`);
          }
        }

        if (captured.length > 0) {
          frames = captured;
          log(`aimedia — ✅ successfully captured ${frames.length} frame(s) via postEl screenshot`);
        } else {
          log("aimedia — ⚠️ all frame captures failed or returned null");
        }

        // Step 4: Build finalContent — use captured frames, or fall back to image URL
        if (frames && frames.length) {
          finalContent = { ...finalContent, frames, videoUrl: null, postPermalink: null };
        } else {
          log("aimedia — ⚠️ no client-side frames captured; sending image/video URL as fallback");
          // finalContent already has imageUrl / videoUrl from extractAll()
          // The backend will download the poster/thumbnail as a single-image fallback
        }

        log("aimedia — sending:", {
          hasFrames: !!(frames && frames.length),
          numFrames: frames ? frames.length : 0,
          hasVideoUrl: !!finalContent.videoUrl,
          hasImageUrl: !!finalContent.imageUrl,
        });
      }

      const result = await sendToBackground(type, finalContent);
      if (!result) throw new Error("لا استجابة من الخادم");

      if (result.verdict === "non_news") {
        showNonNews(postEl, btn, type);
        return;
      }

      showVerdict(postEl, result, type, btn);

    } catch (err) {
      setError(err?.message || "Something went wrong");
    } finally {
      if (!btn.classList.contains("haqq-btn--error") &&
          !btn.classList.contains("haqq-btn--done")) {
        setIdle();
      }
    }
  });

  return btn;
}

// ─── LANGUAGE DETECTION ───────────────────────────────────
function detectLanguage(text) {
  const arabicChars  = (text.match(/[\u0600-\u06FF]/g) || []).length;
  const englishChars = (text.match(/[a-zA-Z]/g)        || []).length;
  const total        = arabicChars + englishChars;
  if (total === 0) return "en";
  return arabicChars / total >= 0.5 ? "ar" : "en";
}

// ─── TEXT CLEANING ────────────────────────────────────────
function cleanText(raw) {
  if (!raw) return "";
  return raw
    .toLowerCase()
    .replace(/#\w+/g, " ")
    .replace(/@\w+/g, " ")
    .replace(/https?:\/\/\S+/g, " ")
    .replace(/see more|see less|اقرأ المزيد|read more/gi, " ")
    .replace(/see original|rate this translation|show more/gi, " ")
    .replace(/[·•]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

// ─── RESULT SHAPE HELPER ──────────────────────────────────
function result(verdict, confidence, explanation, sources = []) {
  return { verdict, confidence, explanation, sources };
}

// ─── BACKGROUND MESSAGES ──────────────────────────────────
async function sendToBackground(type, content) {
  if (type === "content") return verifyContent(content);
  if (type === "aimedia") return verifyMedia(content);
  throw new Error("Unknown verification type");
}

// ─── MERGED TEXT + IMAGE VERIFICATION (Facebook only) ─────
const MIN_TEXT_LEN = 15;

async function verifyContent(content) {
  return new Promise((resolve, reject) => {
    if (!isContextValid()) { killIfContextInvalid(); return reject(new Error(CONTEXT_DEAD_MSG)); }

    const directText = cleanText(content.text || "");
    const lang = directText ? detectLanguage(directText) : "ar";

    const t = setTimeout(() => reject(new Error("الخادم لا يستجيب")), 35000);
    try {
      chrome.runtime.sendMessage(
        {
          type: "HAQQ_VERIFY_CONTENT",
          payload: { text: directText, imageUrl: content.imageUrl || null, lang },
        },
        (res) => {
          clearTimeout(t);
          if (chrome.runtime.lastError) { killIfContextInvalid(); return reject(new Error(chrome.runtime.lastError.message)); }
          if (!res)      return reject(new Error("لا استجابة"));
          if (res.error) return reject(new Error(res.error));
          resolve(res.data);
        }
      );
    } catch (e) {
      clearTimeout(t);
      killIfContextInvalid();
      reject(new Error(CONTEXT_DEAD_MSG));
    }
  });
}

function runVerifyText(text) {
  if (!isContextValid()) {
    killIfContextInvalid();
    return Promise.reject(new Error(CONTEXT_DEAD_MSG));
  }
  const lang = detectLanguage(text);
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("الخادم لا يستجيب")), 25000);
    try {
      chrome.runtime.sendMessage(
        { type: "HAQQ_VERIFY_TEXT", payload: { text: text.slice(0, 1000), lang } },
        (res) => {
          clearTimeout(t);
          if (chrome.runtime.lastError) {
            killIfContextInvalid();
            return reject(new Error(chrome.runtime.lastError.message));
          }
          if (!res)      return reject(new Error("لا استجابة"));
          if (res.error) return reject(new Error(res.error));
          resolve(res.data);
        }
      );
    } catch (e) {
      clearTimeout(t);
      killIfContextInvalid();
      reject(new Error(CONTEXT_DEAD_MSG));
    }
  });
}

// ─── AI-GENERATED / MANIPULATED MEDIA DETECTION (single definition) ───
// frames (base64 data URLs captured client-side) takes priority on the
// backend; videoUrl/postPermalink are the fallback path when capture
// wasn't possible.
async function verifyMedia(content) {
  if (!content.imageUrl && !content.videoUrl && !content.postPermalink && !(content.frames && content.frames.length)) {
    return result("inconclusive", 0, "لا توجد صورة أو فيديو لتحليلها في هذا المنشور.", []);
  }

  if (!isContextValid()) {
    killIfContextInvalid();
    return Promise.reject(new Error(CONTEXT_DEAD_MSG));
  }

  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("الخادم لا يستجيب")), 120000);
    try {
      chrome.runtime.sendMessage(
        {
          type: "HAQQ_DETECT_MEDIA",
          payload: {
            imageUrl: content.imageUrl || null,
            videoUrl: content.videoUrl || null,
            postPermalink: content.postPermalink || null,
            frames: content.frames || null,
          },
        },
        (res) => {
          clearTimeout(t);
          if (chrome.runtime.lastError) {
            killIfContextInvalid();
            return reject(new Error(chrome.runtime.lastError.message));
          }
          if (!res)      return reject(new Error("لا استجابة"));
          if (res.error) return reject(new Error(res.error));
          resolve(res.data);
        }
      );
    } catch (e) {
      clearTimeout(t);
      killIfContextInvalid();
      reject(new Error(CONTEXT_DEAD_MSG));
    }
  });
}

// ─── PANEL (v16) ────────────────────────────────────────────
// Locates the native action row purely as a REFERENCE POINT (never
// inserted into), then creates (or reuses) a single `.haqq-panel` div
// as a sibling right after it. Both the toolbar and any later badge
// live inside this one panel — guaranteeing the native
// Like/Comment/Share row is never touched.
const FACEBOOK_ACTION_MARKERS = [
  '[data-ad-rendering-role="like_button"]',
  '[data-ad-rendering-role="comment_button"]',
  '[data-ad-rendering-role="share_button"]',
  '[aria-label="Send this to friends or post it on your profile."]',
  '[aria-label^="Comment on"]',
  '[aria-label^="React with Like to"]',
];

// ─── FACEBOOK ANCHOR FINDERS ──────────────────────────────
// Layout 1: posts with text buttons (Like / Comment / Share)
// Returns the first labeled interactive element found, or null.
function findFacebookLabeledAnchor(postEl) {
  const roleSelectors = [
    '[data-ad-rendering-role="share_button"]',
    '[data-ad-rendering-role="comment_button"]',
    '[data-ad-rendering-role="like_button"]',
  ];
  for (const sel of roleSelectors) {
    const el = postEl.querySelector(sel);
    if (el) return el;
  }
  const ariaSelectors = [
    '[aria-label*="Share"]', '[aria-label*="share"]', '[aria-label*="مشاركة"]', '[aria-label*="إرسال"]',
    '[aria-label*="Comment"]', '[aria-label*="comment"]', '[aria-label*="تعليق"]', '[aria-label*="التعليق"]',
    '[aria-label*="Like"]', '[aria-label*="like"]', '[aria-label*="إعجاب"]', '[aria-label*="أعجبني"]', '[aria-label*="تفاعل"]',
    '[aria-label="Send this to friends or post it on your profile."]',
    '[role="button"][aria-label*="Message"]', '[role="button"][aria-label*="رسالة"]',
    '[role="button"][aria-label*="Send message"]', '[role="button"][aria-label*="أرسل رسالة"]',
  ];
  for (const sel of ariaSelectors) {
    const el = postEl.querySelector(sel);
    if (el) return el;
  }
  return null;
}

// Layout 2: compact icon-only row (👍 128  💬 64  ↗ 12 — no aria-labels)
// Returns the flex container that holds the icon+number pairs, or null.
// This layout has SVG icons paired with plain number text nodes — unlike
// Layout 1 which has role="button" children with visible text labels.
function findFacebookCompactIconRow(postEl) {
  const allEls = postEl.querySelectorAll('div, span');
  for (const el of allEls) {
    // Skip anything that already contains our own elements.
    if (el.querySelector('.haqq-panel, .haqq-btn-group')) continue;
    // Skip if this contains role=button children — that's Layout 1's action row.
    if (el.querySelector('[role="button"]')) continue;
    const style = window.getComputedStyle(el);
    if (style.display !== 'flex') continue;
    // Needs at least 2 SVGs (like + comment icons at minimum).
    const svgs = el.querySelectorAll('svg');
    if (svgs.length < 2) continue;
    const rect = el.getBoundingClientRect();
    // Compact row is short (icon height ~20px, with padding ~30-50px total).
    if (rect.height > 60 || rect.height < 10) continue;
    // Must be reasonably wide (at least 3 icon+number pairs = ~80px).
    if (rect.width < 80) continue;
    // Confirm this looks like the stats row: at least one SVG should have a
    // sibling or nearby text node that looks like a number (reaction count).
    const hasNumberSibling = Array.from(el.querySelectorAll('svg')).some(svg => {
      // Check text content of the direct SVG parent and its neighbors.
      const parent = svg.parentElement;
      if (!parent) return false;
      const txt = parent.textContent?.trim() || '';
      return /\d/.test(txt) && txt.length < 10;
    });
    // Also accept if the element's direct text nodes contain numbers
    // (some FB versions render: <svg/>70 <svg/>86 <svg/>7 directly).
    const directText = Array.from(el.childNodes)
      .filter(n => n.nodeType === Node.TEXT_NODE)
      .map(n => n.textContent?.trim())
      .join('');
    const hasInlineNumbers = /\d/.test(directText);
    if (!hasNumberSibling && !hasInlineNumbers) continue;
    return el;
  }
  return null;
}

// Find the action row for either layout, for panel insertion below it.
// Returns { row, layout } where layout is 1 (labeled) or 2 (compact).
function findFacebookActionBarRow(postEl) {
  // --- Layout 1: labeled buttons (Like / Comment / Share) ---
  const labeledAnchor = findFacebookLabeledAnchor(postEl);
  if (labeledAnchor) {
    let node = labeledAnchor.parentElement;
    for (let i = 0; i < 8 && node && node !== postEl; i++) {
      const s = window.getComputedStyle(node);
      if (s.display === 'flex' && (s.flexDirection === 'row' || s.flexDirection === 'row-reverse' || !s.flexDirection)) {
        const rect = node.getBoundingClientRect();
        if (rect.height < 80) return { row: node, layout: 1 };
      }
      node = node.parentElement;
    }
    return { row: labeledAnchor.parentElement || labeledAnchor, layout: 1 };
  }

  // --- Layout 2: compact icon-only row (no aria-labels) ---
  const compactRow = findFacebookCompactIconRow(postEl);
  if (compactRow) return { row: compactRow, layout: 2 };

  return null;
}

function findInstaTikTokReference(postEl) {
  for (const sel of CFG.toolbarAnchorSelectors) {
    const anchorEl = postEl.querySelector(sel);
    if (!anchorEl) continue;
    return anchorEl.closest("section") || anchorEl.parentElement;
  }
  return null;
}

function getOrCreatePanel(postEl) {
  const existing = postEl.querySelector(":scope > .haqq-panel, .haqq-panel[data-haqq-owned]");
  if (existing) return existing;

  const panel = document.createElement("div");
  panel.className = "haqq-panel";
  panel.setAttribute("data-haqq-owned", "true");

  if (PLATFORM === "facebook") {
    const found = findFacebookActionBarRow(postEl);
    if (found) {
      const { row } = found;

      // Walk up from the found action row until we reach a node whose
      // DIRECT PARENT is postEl. That node is guaranteed to be a top-level
      // section of the post card (header / body / action bar / comments).
      // Inserting the panel after it keeps us safely inside the post card
      // regardless of how deeply nested flex containers Facebook uses.
      let child = row;
      while (child.parentElement && child.parentElement !== postEl) {
        child = child.parentElement;
      }

      if (child.parentElement === postEl) {
        postEl.insertBefore(panel, child.nextSibling);
        log(`Panel inserted after postEl direct child containing action row (facebook)`);
        return panel;
      }
    }
  } else {
    const reference = findInstaTikTokReference(postEl);
    if (reference && reference.parentElement) {
      reference.parentElement.insertBefore(panel, reference.nextSibling);
      log(`Panel inserted after native action row (${PLATFORM})`);
      return panel;
    }
  }

  postEl.appendChild(panel);
  log(`Fallback: Badge panel appended to postEl (${PLATFORM})`);
  return panel;
}


// ─── NON-NEWS BADGE ───────────────────────────────────────
function showNonNews(postEl, btn, type) {
  const panel = getOrCreatePanel(postEl);
  panel.querySelector(`.haqq-badge[data-type="${type}"]`)?.remove();

  const badge = document.createElement("div");
  badge.className    = "haqq-badge haqq-badge--nonnews";
  badge.dataset.type = type;
  badge.innerHTML = `
    <div class="haqq-badge-bar">
      <span class="haqq-badge-icon">💬</span>
      <span class="haqq-badge-typename">${TYPE_NAMES[type]}</span>
      <span class="haqq-badge-verdict">ليس خبراً</span>
      <span class="haqq-badge-right">
        <button class="haqq-dismiss" aria-label="إغلاق">✕</button>
      </span>
    </div>
    <p class="haqq-badge-expl">هذا المحتوى رأي شخصي أو محادثة — لا يحتاج تحققاً إخبارياً.</p>
  `;

  badge.querySelector(".haqq-dismiss").addEventListener("click", () => {
    badge.remove();
    btn.classList.remove("haqq-btn--done");
    btn.innerHTML = BTN_DEF[type].label;
    btn.disabled  = false;
  });

  btn.classList.remove("haqq-btn--loading");
  btn.classList.add("haqq-btn--done");
  btn.innerHTML = "💬";
  btn.disabled  = false;

  panel.appendChild(badge);
}

// ─── VERDICT BADGE ────────────────────────────────────────
const VERDICT_CFG = {
  fact:         { ar: "موثوق",                      cls: "fact",         icon: "✅" },
  verified:     { ar: "موثوق",                      cls: "fact",         icon: "✅" },
  real:         { ar: "حقيقي — غير مولَّد بالذكاء الاصطناعي", cls: "fact",  icon: "✅" },
  unverified:   { ar: "غير مؤكد",                   cls: "unverified",   icon: "⚠️" },
  inconclusive: { ar: "غير حاسم",                   cls: "unverified",   icon: "❔" },
  fake:         { ar: "مضلل على الأرجح",            cls: "fake",         icon: "❌" },
  manipulated:  { ar: "محرَّف / مُعدَّل",           cls: "manipulated",  icon: "🛠️" },
  ai_generated: { ar: "مُولَّد بالذكاء الاصطناعي", cls: "ai",           icon: "🤖" },
};
const TYPE_NAMES = { content: "المحتوى", aimedia: "الوسائط" };

function showVerdict(postEl, result, type, btn) {
  const panel = getOrCreatePanel(postEl);
  panel.querySelector(`.haqq-badge[data-type="${type}"]`)?.remove();

  const cfg  = VERDICT_CFG[result.verdict] || VERDICT_CFG.unverified;
  const pct  = Math.round((result.confidence || 0) * 100);
  const name = TYPE_NAMES[type];

  const sourcesHtml = (result.sources || [])
    .map(s => {
      try {
        const url   = s.url || s;
        const title = s.title || new URL(url).hostname;
        return `<a href="${url}" target="_blank" rel="noopener" class="haqq-src-link">
                  <span class="haqq-src-title">${escapeHtml(title.slice(0, 50))}</span>
                </a>`;
      } catch { return ""; }
    })
    .filter(Boolean)
    .join("");

  const badge = document.createElement("div");
  badge.className    = `haqq-badge haqq-badge--${cfg.cls}`;
  badge.dataset.type = type;

  badge.innerHTML = `
    <div class="haqq-badge-bar">
      <span class="haqq-badge-icon">${cfg.icon}</span>
      <span class="haqq-badge-typename">${name}</span>
      <span class="haqq-badge-verdict">${cfg.ar}</span>
      <span class="haqq-badge-right">
        ${pct > 0 ? `<span class="haqq-pct">${pct}%</span>` : ""}
        ${result.mock ? `<span class="haqq-mock">Mock</span>` : ""}
        <button class="haqq-dismiss" aria-label="إغلاق">✕</button>
      </span>
    </div>
    ${result.explanation
      ? `<p class="haqq-badge-expl">${escapeHtml(result.explanation)}</p>`
      : ""}
    ${sourcesHtml
      ? `<div class="haqq-sources">
           <span class="haqq-src-lbl">📎 المصادر</span>
           <div class="haqq-src-list">${sourcesHtml}</div>
         </div>`
      : ""}
  `;

  badge.querySelector(".haqq-dismiss").addEventListener("click", () => {
    badge.remove();
    btn.classList.remove("haqq-btn--done");
    btn.innerHTML = BTN_DEF[type].label;
    btn.disabled  = false;
  });

  btn.classList.remove("haqq-btn--loading");
  btn.classList.add("haqq-btn--done");
  btn.innerHTML = cfg.icon;
  btn.disabled  = false;

  panel.appendChild(badge);
}

// ─── HELPERS ──────────────────────────────────────────────
function escapeHtml(s = "") {
  return s
    .replace(/&/g,  "&amp;")
    .replace(/</g,  "&lt;")
    .replace(/>/g,  "&gt;")
    .replace(/"/g,  "&quot;");
}

function normalise(str) {
  return str
    .toLowerCase()
    .replace(/[\u064B-\u065F\u0670\u0671\u0640]/g, "")
    .replace(/[آأإٱ]/g, "ا")
    .replace(/ة/g, "ه")
    .replace(/ى/g, "ي")
    .replace(/[^\u0600-\u06FFa-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

// Inserts the HAQQ button(s) directly INTO the platform's own icon
// row/column, sized to match its native icons — as opposed to
// getOrCreatePanel(), which is used ONLY for the verdict badge and
// deliberately stays a separate sibling block so it never overlaps or
// crushes the native Like/Comment/Share/Bookmark icons.
function insertButtonsIntoActionColumn(postEl, buttonGroup) {
  const beforeSelectors = CFG.buttonInsertBeforeSelectors || [];
  for (const sel of beforeSelectors) {
    const beforeEl = postEl.querySelector(sel);
    if (!beforeEl) continue;
    // Climb to the actual icon wrapper (the SVG/div's direct clickable
    // container), not the bare <svg> itself, so our button sits as a
    // sibling of the OTHER ICON WRAPPERS, not nested inside one.
    const wrapper = beforeEl.closest('div, button') || beforeEl;
    wrapper.parentElement?.insertBefore(buttonGroup, wrapper);
    log(`Buttons inserted before "${sel}" in native column (${PLATFORM})`);
    return true;
  }
  return false;
}

// ─── PROCESS ──────────────────────────────────────────────
function processPost(postEl) {
  if (!isValidPost(postEl)) return;

  const content = extractAll(postEl);
  log("Post →", { platform: PLATFORM, text: !!content.text, img: !!content.imageUrl, vid: !!content.videoUrl });

  const hasContentBtn = !CFG.mediaOnly && (content.text || content.imageUrl);
  const hasMediaBtn   = content.imageUrl || content.videoUrl;

  if (!hasContentBtn && !hasMediaBtn) return;

  if (PLATFORM === "tiktok") {
    postEl.setAttribute(PROCESSED_ATTR, content.videoUrl || "true");
  } else {
    postEl.setAttribute(PROCESSED_ATTR, "true");
  }

  const grp = document.createElement("div");
  grp.className = "haqq-btn-group";
  grp.setAttribute("data-haqq-owned", "true");

  if (hasContentBtn) grp.appendChild(makeBtn("content", postEl, content));
  if (hasMediaBtn)   grp.appendChild(makeBtn("aimedia", postEl, content));

  // Instagram/TikTok: buttons go INTO the native icon row/column,
  // matching native icon format. Facebook: unchanged, buttons stay in
  // the panel below the action row.
  const insertedNatively =
    (PLATFORM === "instagram" || PLATFORM === "tiktok") &&
    insertButtonsIntoActionColumn(postEl, grp);

  if (!insertedNatively) {
    const panel = getOrCreatePanel(postEl);
    panel.appendChild(grp);
  }

  // Badge ALWAYS goes in the separate panel, regardless of where the
  // buttons ended up — this is what guarantees it never overlaps any
  // native icon, on any platform.
}

// ─── SCAN + OBSERVER ──────────────────────────────────────
function scanForPosts() {
  if (!isContextValid()) { killIfContextInvalid(); return; }
  const candidates = [...document.querySelectorAll(POST_SELECTOR)];
  // POST_SELECTOR can match nested wrappers for the same post (e.g.
  // TikTok's recommend-list-item-container also contains a nested
  // feed-video section that independently matches) — keep only the
  // outermost match so one visual post gets exactly one toolbar.
  const posts = candidates.filter(
    el => !candidates.some(other => other !== el && other.contains(el))
  );
  let n = 0;
  posts.forEach(el => {
    if (isValidPost(el)) { processPost(el); n++; }
  });
  if (n) log(`+${n} posts (${PLATFORM})`);
}

let scanTimer = null;
let scanInterval = null;
let lastScanTime = 0;

function requestScan() {
  const now = Date.now();
  if (now - lastScanTime > 500) {
    clearTimeout(scanTimer);
    lastScanTime = now;
    scanForPosts();
    return;
  }
  clearTimeout(scanTimer);
  scanTimer = setTimeout(() => {
    lastScanTime = Date.now();
    scanForPosts();
  }, 300);
}

const observer = new MutationObserver(requestScan);

let lastUrl = location.href;
function handleNav() {
  const cur = location.href;
  if (cur === lastUrl) return;
  lastUrl = cur;
  [1000, 2500, 5000].forEach(t => setTimeout(scanForPosts, t));
}
const _push = history.pushState.bind(history);
history.pushState = function (...a) { _push(...a); handleNav(); };
window.addEventListener("popstate", handleNav);

function init() {
  log(`HAQQ v17 init — platform: ${PLATFORM}, mediaOnly: ${CFG.mediaOnly}`);
  scanForPosts();
  [800, 2000, 4000, 7000].forEach(t => setTimeout(scanForPosts, t));
  observer.observe(document.body, { childList: true, subtree: true, attributes: false });
  scanInterval = setInterval(scanForPosts, 3000);
}

document.readyState === "loading"
  ? document.addEventListener("DOMContentLoaded", init)
  : init();

})();