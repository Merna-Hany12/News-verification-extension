// ─── HAQQ Background Service Worker v8 (NewsData.io) ─────
import { CONFIG } from "./config.js";
const NEWSDATA_API_KEY = CONFIG.NEWSDATA_API_KEY;
const FREENEWS_API_KEY = CONFIG.FREENEWS_API_KEY;
const NGROK_URL        = CONFIG.NGROK_URL;
const NEWSDATA_BASE    = "https://newsdata.io/api/1/news";
const FREENEWS_BASE    = "https://api.freenewsapi.io/v1/news";
// ─── CACHE + DEDUP ────────────────────────────────────────
const cache    = new Map();
const inFlight = new Map();
// ─── STATS ────────────────────────────────────────────────
let stats = { total: 0, fact: 0, unverified: 0, fake: 0, ai_generated: 0 };

// ─── TRUSTED SOURCES ─────────────────────────────────────
const TRUSTED = [
  // International
  "bbc","reuters","ap","apnews","associated press",
  "aljazeera","al jazeera","cnn","nytimes","theguardian",
  "france24","dw","euronews","skynews","sky news","afp",
  // Arabic
  "الجزيرة","رويترز","العربية","alarabiya","france24arabic",
  "aawsat","asharqalawsat","alhurra",
  // Egyptian
  "ahram","alahram","youm7","masrawy","elwatannews",
  "almasryalyoum","shorouk","elshorouk","vetogate",
  "filbalad","mobtada","dotmsr","elbashayer","cairo24"
];

// ─── ARABIC STOP WORDS ────────────────────────────────────
const STOPS = new Set([
  "في","من","على","إلى","عن","مع","هذا","هذه","ذلك","تلك",
  "التي","الذي","وهو","وهي","كان","كانت","أن","إن","لكن",
  "كما","حيث","بعد","قبل","عند","حتى","هل","لا","نعم","كل",
  "بين","غير","عبر","خلال","حول","ضد","أو","ثم","لم","لن",
  "قد","فقد","وقد","منذ","إذا","إذ","بما","مما","فمن","وفي",
  "وعلى","ومع","وإن","أما","بل","فإن","ولا","وهذا","وهذه"
]);

// ─── ROUTER ───────────────────────────────────────────────
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
        case "HAQQ_GET_STATS":
          return sendResponse({ data: { stats } });
        case "HAQQ_RESET_STATS":
          stats = { total: 0, fact: 0, unverified: 0, fake: 0, ai_generated: 0 };
          return sendResponse({ data: { ok: true } });
        case "HAQQ_CLASSIFY_AI":
          return sendResponse({ data: await classifyWithAI(msg.payload.text) });
        default:
          return sendResponse({ error: "Unknown message type" });
      }
    } catch (e) {
      return sendResponse({ error: e.message });
    }
  })();
  return true;
});


async function isNewsText(text) {
  try {
    const res = await fetch("http://localhost:8000/classify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text.slice(0, 500) })
    });
    const data = await res.json();
    return data; // { label, score, is_news }
  } catch (e) {
    console.warn("[HAQQ] AI classifier unreachable:", e.message);
    return null; // fail open — continue with NewsData anyway
  }
}


async function classifyWithAI(text) {
  console.log("[HAQQ] Sending to AI:", text);
  try {
    const res = await fetch(`${NGROK_URL}/classify`, {
      method: "POST",
      headers: { 
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true"
      },
      body: JSON.stringify({ text: text.slice(0, 500) })
    });
    const data = await res.json();
    console.log("[HAQQ] AI response:", data);
    return data;
  } catch (e) {
    console.warn("[HAQQ] AI unreachable:", e.message);
    return null;
  }
}

async function verifyText({ text, lang }) {
  // Basic validation
  if (!text || text.trim().length < 15) {
    return result(
      "unverified",
      0,
      "النص قصير جداً للتحقق.",
      []
    );
  }

  // AI classification first
  let classification = null;

  try {
    classification = await classifyWithAI(text);
    console.log("[HAQQ] Classification result:", classification);
  } catch (e) {
    console.warn("[HAQQ] Classification failed:", e);
  }

  // Skip news search if AI says it's not news
  if (
    classification &&
    (
      classification.is_news === false ||
      classification.label === "non_news"
    )
  ) {
    return {
      verdict: "non_news",
      confidence: classification.score || 0.95,
      explanation: "المحتوى لا يبدو خبراً، لذلك تم تخطي البحث الإخباري.",
      sources: []
    };
  }

  // Extract keywords
  const keywords = extractKeywords(text);

  if (!keywords) {
    return result(
      "unverified",
      0.2,
      "لا توجد كلمات مفتاحية كافية للبحث",
      []
    );
  }

  // Cache key
  const cKey = "text::" + keywords;

  if (cache.has(cKey))
    return cache.get(cKey);

  if (inFlight.has(cKey))
    return inFlight.get(cKey);

  const promise = (async () => {
    console.log("[HAQQ] Searching news for:", keywords);
    console.log("[HAQQ] Language:", lang);

    let articles = [];

    if (lang === "ar") {
      const [ndAr, fnAr] = await Promise.all([
        fetchNewsData(keywords, "ar"),
        fetchFreeNews(keywords, "ar")
      ]);

      articles = [...ndAr, ...fnAr];

    } else {
      const [ndEn, fnEn] = await Promise.all([
        fetchNewsData(keywords, "en"),
        fetchFreeNews(keywords, "en")
      ]);

      articles = [...ndEn, ...fnEn];
    }

    console.log("[HAQQ] Total articles found:", articles.length);

    const out = scoreArticles(text, keywords, articles);

    cache.set(cKey, out);
    inFlight.delete(cKey);

    stats.total++;
    stats[out.verdict] =
      (stats[out.verdict] || 0) + 1;

    return out;
  })();

  inFlight.set(cKey, promise);

  return promise;
}

async function verifyImage({ imageUrl }) {
  const keywords = keywordsFromUrl(imageUrl);
  if (!keywords)
    return result("unverified", 0.3, "لا يمكن استخراج معلومات من رابط الصورة", []);

  const cKey = "img::" + keywords;
  if (cache.has(cKey))    return cache.get(cKey);
  if (inFlight.has(cKey)) return inFlight.get(cKey);

  const promise = (async () => {
    const [ndAr, ndEn, fnAr, fnEn] = await Promise.all([
      fetchNewsData(keywords, "ar"),
      fetchNewsData(keywords, "en"),
      fetchFreeNews(keywords, "ar"),
      fetchFreeNews(keywords, "en"),
    ]);
    const articles = [...ndAr, ...ndEn, ...fnAr, ...fnEn];
    const out = scoreArticles("", keywords, articles);
    cache.set(cKey, out);
    inFlight.delete(cKey);
    return out;
  })();

  inFlight.set(cKey, promise);
  return promise;
}

// ─── VIDEO VERIFICATION ───────────────────────────────────
async function verifyVideo({ text, videoPoster, videoUrl }) {
  if (text && text.trim().length > 15) return verifyText({ text });
  if (videoPoster) return verifyImage({ imageUrl: videoPoster });
  if (videoUrl)    return verifyImage({ imageUrl: videoUrl });
  return result("unverified", 0.2, "⚠️ الفيديو يحتاج وصفاً نصياً للتحقق", []);
}

// ─── NEWSDATA FETCH ───────────────────────────────────────
async function fetchNewsData(query, lang) {
  const url = new URL(NEWSDATA_BASE);
  url.searchParams.set("apikey",   NEWSDATA_API_KEY);
  url.searchParams.set("q",        query);
  url.searchParams.set("language", lang);
  url.searchParams.set("size",     "10");

  try {
    const res  = await fetch(url);
    const data = await res.json();
    if (data.status === "error") {
      console.warn("[HAQQ] NewsData error:", data.message);
      return [];
    }
    return data.results || [];
  } catch (e) {
    console.error("[HAQQ] NewsData fetch error:", e.message);
    return [];
  }
}

async function fetchFreeNews(query, lang) {
  const url = new URL("https://api.freenewsapi.io/v1/news");
  url.searchParams.set("q",        query);
  url.searchParams.set("language", lang);
  url.searchParams.set("limit",    "10");

  try {
    const res  = await fetch(url, {
      headers: {
        "x-api-key": FREENEWS_API_KEY
      }
    });
    const data = await res.json();
    console.log("[HAQQ] FreeNews raw response:", data);

    if (!data.articles) return [];

    return data.articles.map(a => ({
      title:       a.title       || "",
      description: a.description || a.subtitle || "",
      link:        a.url         || "#",
      source_id:   a.source?.id   || "",
      source_name: a.source?.name || "",
      _api: "freenews"
    }));
  } catch (e) {
    console.error("[HAQQ] FreeNews fetch error:", e.message);
    return [];
  }
}
// ─── SCORING ──────────────────────────────────────────────
function scoreArticles(originalText, queryKeywords, articles) {
  if (!articles.length)
    return result("unverified", 0.2, "لم يُعثر على أخبار مرتبطة بهذا المحتوى", []);

  const kws = queryKeywords.split(" ").map(normalise).filter(w => w.length > 2);

  let trustedWithMatch   = 0;
  let untrustedWithMatch = 0;
  let totalOverlap       = 0;
  const sources          = [];

  for (const a of articles) {
    const blob    = normalise(`${a.title || ""} ${a.description || ""}`);
    const srcId   = (a.source_id   || "").toLowerCase();
    const srcName = (a.source_name || "").toLowerCase();
    const overlap = kws.filter(k => blob.includes(k)).length;
    const trusted = TRUSTED.some(t => srcId.includes(t) || srcName.includes(t));

    totalOverlap += overlap;

    if (overlap >= 1) {
      if (trusted) trustedWithMatch++;
      else untrustedWithMatch++;
      if (sources.length < 5)
        sources.push({ url: a.link || "#", title: a.title || srcName });
    }
  }

  const ratio = Math.min(totalOverlap / (kws.length * articles.length + 0.001), 1);

  if (trustedWithMatch >= 2)
    return result("fact",       Math.min(0.75 + ratio * 0.22, 0.97), `✅ ${trustedWithMatch} مصادر موثوقة تؤكد المحتوى`, sources);
  if (trustedWithMatch === 1)
    return result("fact",       Math.min(0.62 + ratio * 0.18, 0.85), "✅ مصدر موثوق واحد يؤكد المحتوى", sources);
  if (untrustedWithMatch >= 3)
    return result("unverified", Math.min(0.42 + ratio * 0.12, 0.60), "⚠️ الخبر منتشر لكن لم يُؤكَّد من مصادر موثوقة", sources);
  if (untrustedWithMatch >= 1)
    return result("unverified", 0.30, "⚠️ نتائج محدودة — لا يمكن التحقق بشكل كافٍ", sources);

  return result("unverified", 0.20, "⚠️ لا تطابق مع أي مصدر إخباري", []);
}

// ─── KEYWORD EXTRACTION ───────────────────────────────────
function extractKeywords(text) {
  const tokens = normalise(text)
    .split(/\s+/)
    .filter(w => w.length > 3 && !STOPS.has(w));
  return [...new Set(tokens)].slice(0, 5).join(" ");
}

function keywordsFromUrl(url) {
  try {
    return new URL(url).pathname
      .split("/")
      .map(p => p.replace(/[-_.]/g, " ").trim())
      .filter(p => p.length > 4 && !/^\d+$/.test(p))
      .slice(0, 3).join(" ") || null;
  } catch { return null; }
}

// ─── ARABIC NORMALISE ─────────────────────────────────────
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

// ─── RESULT BUILDER ───────────────────────────────────────
function result(verdict, confidence, explanation, sources = []) {
  return { verdict, confidence, explanation, sources };
}