// ─── HAQQ Background Service Worker v10 (LangGraph backend) ─────────────────
// All search, scoring, and keyword extraction has moved to the Python
// LangGraph pipeline (/verify).  This file is now only responsible for:
//   • routing chrome messages
//   • calling /verify  (text)
//   • calling /ocr     (image → text → /verify)
//   • calling /classify (direct AI classification badge)
//   • image/video verification via keywordsFromUrl (no backend needed)
//   • stats tracking
// ─────────────────────────────────────────────────────────────────────────────

import { CONFIG } from "./config.js";

const NGROK_URL = CONFIG.NGROK_URL;

// ─── CACHE + DEDUP ────────────────────────────────────────────────────────────
const cache    = new Map();
const inFlight = new Map();

// ─── STATS ────────────────────────────────────────────────────────────────────
let stats = { total: 0, fact: 0, unverified: 0, fake: 0, ai_generated: 0 };

// ─── ROUTER ───────────────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      switch (msg.type) {
        case "HAQQ_VERIFY_TEXT":
          return sendResponse({ data: await verifyText(msg.payload) });

        case "HAQQ_VERIFY_IMAGE":
          return sendResponse({ data: await verifyImage(msg.payload) });

        case "HAQQ_VERIFY_VIDEO":
          return sendResponse({ data: await verifyVideo(msg.payload) });

        case "HAQQ_OCR_IMAGE":
          return sendResponse({ data: await ocrImage(msg.payload) });

        case "HAQQ_CLASSIFY_AI":
          return sendResponse({ data: await classifyWithAI(msg.payload.text) });

        case "HAQQ_GET_STATS":
          return sendResponse({ data: { stats } });

        case "HAQQ_RESET_STATS":
          stats = { total: 0, fact: 0, unverified: 0, fake: 0, ai_generated: 0 };
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

// ─── TEXT VERIFICATION ────────────────────────────────────────────────────────
// Delegates entirely to the LangGraph /verify pipeline on the backend.
// Pipeline: classify → extract keywords → search → LLM verify → score
async function verifyText({ text, lang }) {
  if (!text || text.trim().length < 20)
    return result("unverified", 0, "النص قصير جداً للتحقق.", []);

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
      return result("unverified", 0, "⚠️ خطأ في الاتصال بخادم التحقق", []);
    }
  })();

  inFlight.set(cKey, promise);
  return promise;
}

// ─── IMAGE VERIFICATION ───────────────────────────────────────────────────────
// Extracts keywords from the image URL and pipes them through verifyText.
// If the URL yields no useful keywords, returns unverified immediately.
async function verifyImage({ imageUrl }) {
  const keywords = keywordsFromUrl(imageUrl);
  if (!keywords)
    return result("unverified", 0.3, "لا يمكن استخراج معلومات من رابط الصورة", []);

  // Reuse the text pipeline — keywords become the "text" to verify
  return verifyText({ text: keywords, lang: "ar" });
}

// ─── VIDEO VERIFICATION ───────────────────────────────────────────────────────
async function verifyVideo({ text, videoPoster, videoUrl }) {
  if (text && text.trim().length > 15) return verifyText({ text, lang: "ar" });
  if (videoPoster) return verifyImage({ imageUrl: videoPoster });
  if (videoUrl)    return verifyImage({ imageUrl: videoUrl });
  return result("unverified", 0.2, "⚠️ الفيديو يحتاج وصفاً نصياً للتحقق", []);
}

// ─── OCR ──────────────────────────────────────────────────────────────────────
// Sends image URL to /ocr, gets back extracted text, then pipes it through
// verifyText so the full LangGraph pipeline runs on the extracted text.
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

// Extracts readable keywords from an image URL path.
// Used by verifyImage when there's no text caption to work with.
function keywordsFromUrl(url) {
  try {
    return new URL(url).pathname
      .split("/")
      .map(p => p.replace(/[-_.]/g, " ").trim())
      .filter(p => p.length > 4 && !/^\d+$/.test(p))
      .slice(0, 3)
      .join(" ") || null;
  } catch {
    return null;
  }
}

// Uniform result shape — matches what the backend also returns.
function result(verdict, confidence, explanation, sources = []) {
  return { verdict, confidence, explanation, sources };
}