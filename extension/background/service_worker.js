// ─── HAQQ Background Service Worker v12 ──────────────────────────────────────
// v12 change: added a single HAQQ_VERIFY_CONTENT handler that calls the new
// backend endpoint /verify-content, which now owns the whole "verify text ->
// maybe OCR -> maybe re-verify" decision server-side. This replaces what used
// to be up to 3 separate chrome.runtime round trips (HAQQ_VERIFY_TEXT ->
// HAQQ_OCR_IMAGE -> HAQQ_VERIFY_TEXT again) from content.js's old client-side
// orchestration with a single request/response.
//
// HAQQ_VERIFY_TEXT and HAQQ_OCR_IMAGE are KEPT (not removed) since other
// flows may still call them directly — only content.js's merged "content"
// button flow has moved to HAQQ_VERIFY_CONTENT. Safe to delete the two old
// handlers later once confirmed nothing else sends those message types.
//
// v11 change (carried over): the old HAQQ_VERIFY_IMAGE / HAQQ_VERIFY_VIDEO
// handlers (which only ever did a keyword-from-URL guess) are replaced with
// a single HAQQ_DETECT_MEDIA handler that calls the real GPU-accelerated
// AI-media detection pipeline for both images and video.
//
// Responsibilities of this file:
//   • routing chrome messages
//   • calling /verify-content (merged text+OCR verification — "content" btn)
//   • calling /verify         (text only — kept for any other caller)
//   • calling /ocr            (image → text — kept for any other caller)
//   • calling /classify       (direct AI news/non-news classification badge)
//   • calling /detect-media   (GPU AI-generated / manipulated media detection)
//   • stats tracking
// ─────────────────────────────────────────────────────────────────────────────

import { CONFIG } from "./config.js";

const NGROK_URL = CONFIG.NGROK_URL;

// ─── CACHE + DEDUP ────────────────────────────────────────────────────────────
const cache    = new Map();
const inFlight = new Map();

// ─── STATS ────────────────────────────────────────────────────────────────────
let stats = {
  total: 0,
  fact: 0, unverified: 0, fake: 0,
  ai_generated: 0, manipulated: 0, real: 0, inconclusive: 0,
};

// ─── ROUTER ───────────────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      const storage = await chrome.storage.local.get("news_lang");
      if (msg.payload && typeof msg.payload === "object") {
        msg.payload.lang = storage.news_lang || "ar";
      }

      switch (msg.type) {
        case "HAQQ_VERIFY_CONTENT":
          return sendResponse({ data: await verifyContentBackend(msg.payload) });

        case "HAQQ_VERIFY_TEXT":
          return sendResponse({ data: await verifyText(msg.payload) });

        case "HAQQ_DETECT_MEDIA":
          return sendResponse({ data: await detectMedia(msg.payload) });

        case "HAQQ_CAPTURE_TAB":
          // Used by content.js to capture the visible tab for video frame extraction.
          // This bypasses CORS/tainted-canvas because captureVisibleTab captures
          // composited pixels from the GPU, not DOM element data.
          try {
            const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
            if (!tab?.id) return sendResponse({ error: "No active tab" });
            const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
              format: "jpeg",
              quality: 85,
            });
            return sendResponse({ dataUrl });
          } catch (e) {
            console.warn("[HAQQ] captureVisibleTab error:", e.message);
            return sendResponse({ error: e.message });
          }

        case "HAQQ_OCR_IMAGE":
          return sendResponse({ data: await ocrImage(msg.payload) });

        case "HAQQ_CLASSIFY_AI":
          return sendResponse({ data: await classifyWithAI(msg.payload.text) });

        case "HAQQ_GET_STATS":
          return sendResponse({ data: { stats } });

        case "HAQQ_RESET_STATS":
          stats = {
            total: 0,
            fact: 0, unverified: 0, fake: 0,
            ai_generated: 0, manipulated: 0, real: 0, inconclusive: 0,
          };
          return sendResponse({ data: { ok: true } });

        default:
          return sendResponse({ error: "Unknown message type" });
      }
    } catch (e) {
      return sendResponse({ error: e.message });
    }
  })();
  return true;
});

// ─── MERGED CONTENT VERIFICATION (text + OCR fallback, server-side) ──────────
// Replaces what used to be up to 3 separate chrome.runtime round trips
// (verify text -> ocr -> verify ocr text) with a single call to the
// backend's /verify-content, which now owns that whole decision itself —
// including firing OCR concurrently with text verification, and retrying
// with OCR text when the direct-text verdict comes back "unverified" or
// "non_news".
async function verifyContentBackend({ text, imageUrl, lang }) {
  const cKey = "content::" + (text || "").trim().slice(0, 100) + "::" + (imageUrl || "");
  if (cache.has(cKey))    return cache.get(cKey);
  if (inFlight.has(cKey)) return inFlight.get(cKey);

  const promise = (async () => {
    try {
      const res = await fetch(`${NGROK_URL}/verify-content`, {
        method:  "POST",
        headers: {
          "Content-Type":               "application/json",
          "ngrok-skip-browser-warning": "true",
        },
        body: JSON.stringify({
          text:      (text || "").trim().slice(0, 1000),
          image_url: imageUrl || null,
          lang,
        }),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const out = await res.json();   // { verdict, confidence, explanation, sources, text_source }

      cache.set(cKey, out);
      inFlight.delete(cKey);
      stats.total++;
      stats[out.verdict] = (stats[out.verdict] || 0) + 1;
      return out;

    } catch (e) {
      console.warn("[HAQQ] /verify-content error:", e.message);
      inFlight.delete(cKey);
      const errMsg = lang === "en" ? "⚠️ Error connecting to verification server" : "⚠️ خطأ في الاتصال بخادم التحقق";
      return result("unverified", 0, errMsg, []);
    }
  })();

  inFlight.set(cKey, promise);
  return promise;
}

// ─── TEXT VERIFICATION (text only, no OCR) ────────────────────────────────────
// Delegates entirely to the LangGraph /verify pipeline on the backend.
// Pipeline: classify → extract keywords → search → LLM verify → score
// Kept for any caller that wants text-only verification without the
// merged OCR-fallback behavior /verify-content now provides.
async function verifyText({ text, lang }) {
  if (!text || text.trim().length < 20) {
    const shortMsg = lang === "en" ? "Text is too short to verify." : "النص قصير جداً للتحقق.";
    return result("unverified", 0, shortMsg, []);
  }

  const cKey = "text::" + text.trim().slice(0, 100);
  if (cache.has(cKey))    return cache.get(cKey);
  if (inFlight.has(cKey)) return inFlight.get(cKey);

  const promise = (async () => {
    try {
      const res = await fetch(`${NGROK_URL}/verify`, {
        method:  "POST",
        headers: {
          "Content-Type":               "application/json",
          "ngrok-skip-browser-warning": "true",
        },
        body: JSON.stringify({ text: text.trim().slice(0, 1000), lang }),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const out = await res.json();   // { verdict, confidence, explanation, sources }

      cache.set(cKey, out);
      inFlight.delete(cKey);
      stats.total++;
      stats[out.verdict] = (stats[out.verdict] || 0) + 1;
      return out;

    } catch (e) {
      console.warn("[HAQQ] /verify error:", e.message);
      inFlight.delete(cKey);
      const errMsg = lang === "en" ? "⚠️ Error connecting to verification server" : "⚠️ خطأ في الاتصال بخادم التحقق";
      return result("unverified", 0, errMsg, []);
    }
  })();

  inFlight.set(cKey, promise);
  return promise;
}

// ─── AI-GENERATED / MANIPULATED MEDIA DETECTION ───────────────────────────────
// Sends whichever of imageUrl / videoUrl are present to the backend's
// GPU-accelerated detection ensemble (/detect-media). The backend decides
// internally how to weight image vs. video when both are supplied (e.g. a
// video's poster frame plus the video itself).
// Expected response shape: { verdict, confidence, explanation, mediaType }
// where verdict is one of: real | ai_generated | manipulated | inconclusive

async function detectMedia({
  imageUrl,
  videoUrl,
  postPermalink,
  frames,
  platform,
  lang,
}) {
  if (!imageUrl && !videoUrl && !postPermalink && !(frames && frames.length)) {
    const emptyMsg =
      lang === "en"
        ? "No media available to analyze."
        : "لا توجد وسائط لتحليلها.";

    return result("inconclusive", 0, emptyMsg, []);
  }


  const cKey = "media::" + (videoUrl || postPermalink || imageUrl || "frames");
  if (cache.has(cKey))    return cache.get(cKey);
  if (inFlight.has(cKey)) return inFlight.get(cKey);

  const promise = (async () => {
    try {
      // Convert data URLs to raw base64 for the backend
      let extractedFrames = null;
      if (frames && frames.length) {
        extractedFrames = frames.map(f => {
          if (f.startsWith("data:")) return f.split(",")[1];
          return f;
        });
        console.log(`[HAQQ] Sending ${extractedFrames.length} client-captured frames to backend`);
      }

      const res = await fetch(`${NGROK_URL}/detect-media`, {
        method:  "POST",
        headers: {
          "Content-Type":               "application/json",
          "ngrok-skip-browser-warning": "true",
        },
        body: JSON.stringify({ 
          image_url: imageUrl || null, 
          video_url: videoUrl || null,
          post_permalink: postPermalink || null,
          extracted_frames: extractedFrames || null,
          platform: platform || "generic"
          lang: lang || "ar"
        }),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const out = await res.json();

      cache.set(cKey, out);
      inFlight.delete(cKey);
      stats.total++;
      stats[out.verdict] = (stats[out.verdict] || 0) + 1;
      return out;

    } catch (e) {
      console.warn("[HAQQ] /detect-media error:", e.message);
      inFlight.delete(cKey);
      return result("inconclusive", 0, "⚠️ خطأ في الاتصال بخادم كشف الوسائط", []);
    }
  })();

  inFlight.set(cKey, promise);
  return promise;
}

// ─── OCR (standalone, no verification) ────────────────────────────────────────
// Sends image URL to /ocr, gets back extracted text. Kept for any caller
// that wants raw OCR text without the merged verify-content flow.
async function ocrImage({ imageUrl }) {
  if (!imageUrl) {
    console.warn("[HAQQ] ocrImage — no imageUrl provided");
    return { text: "" };
  }

  console.log("[HAQQ] OCR request for:", imageUrl.slice(0, 80));

  try {
    const res = await fetch(`${NGROK_URL}/ocr`, {
      method:  "POST",
      headers: {
        "Content-Type":               "application/json",
        "ngrok-skip-browser-warning": "true",
      },
      body: JSON.stringify({ image_url: imageUrl }),
    });

    if (!res.ok) {
      console.warn("[HAQQ] OCR HTTP error:", res.status);
      return { text: "" };
    }

    const data      = await res.json();
    const extracted = data.text || data.extracted_text || "";
    console.log("[HAQQ] OCR extracted:", extracted.slice(0, 100));
    return { text: extracted };

  } catch (e) {
    console.warn("[HAQQ] OCR unreachable:", e.message);
    return { text: "" };
  }
}

// ─── DIRECT AI CLASSIFICATION ─────────────────────────────────────────────────
// Used by the HAQQ_CLASSIFY_AI message to show the is-this-news badge
// without running the full verification pipeline.
async function classifyWithAI(text) {
  if (!text || text.trim().length === 0) {
    console.warn("[HAQQ] classifyWithAI — empty text, skipping");
    return null;
  }

  try {
    const res = await fetch(`${NGROK_URL}/classify`, {
      method:  "POST",
      headers: {
        "Content-Type":               "application/json",
        "ngrok-skip-browser-warning": "true",
      },
      body: JSON.stringify({ text: text.trim().slice(0, 500) }),
    });

    if (!res.ok) {
      console.warn("[HAQQ] Classify HTTP error:", res.status);
      return null;
    }

    const data = await res.json();
    if (data.detail) {
      console.warn("[HAQQ] Server validation error:", JSON.stringify(data.detail));
      return null;
    }

    return data;   // { label, score, news_score, non_news_score, is_news }

  } catch (e) {
    console.warn("[HAQQ] AI unreachable:", e.message);
    return null;
  }
}

// ─── HELPERS ──────────────────────────────────────────────────────────────────

// Uniform result shape — matches what the backend also returns.
function result(verdict, confidence, explanation, sources = []) {
  return { verdict, confidence, explanation, sources };
}