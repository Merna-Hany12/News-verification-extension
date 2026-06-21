// ─── HAQQ Background Service Worker v9 (NewsData.io + Search Fallback) ─────
import { CONFIG } from "./config.js";
const NEWSDATA_API_KEY = CONFIG.NEWSDATA_API_KEY;
const FREENEWS_API_KEY = CONFIG.FREENEWS_API_KEY;
const NGROK_URL        = CONFIG.NGROK_URL;
const NEWSDATA_BASE    = "https://newsdata.io/api/1/news";
const FREENEWS_BASE    = "https://api.freenewsapi.io/v1/news";
const CURRENTS_API_KEY = CONFIG.CURRENTS_API_KEY;
const GNEWS_API_KEY    = CONFIG.GNEWS_API_KEY;
const CURRENTS_BASE    = "https://api.currentsapi.services/v1/search";
const GNEWS_BASE       = "https://gnews.io/api/v4/search";
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
  "filbalad","mobtada","dotmsr","elbashayer","cairo24",
    "washingtonpost","wsj","bloomberg","time","newsweek",
  "nbcnews","cbsnews","abcnews","usatoday","latimes",
  "independent","telegraph","theguardian","ft",
  "middleeasteye","arabicpost","atalayar"
];

// ─── STOP WORDS (Arabic + English) ────────────────────────
// Filtered together regardless of detected language — Arabic news
// text on Facebook is frequently code-switched with English words
// and names, so filtering against only one list let the other
// language's filler words slip into the search query.
const STOPS_AR = new Set([
  // pronouns / demonstratives / connectors
  "في","من","على","إلى","عن","مع","هذا","هذه","ذلك","تلك",
  "التي","الذي","وهو","وهي","كان","كانت","أن","إن","لكن",
  "كما","حيث","بعد","قبل","عند","حتى","هل","لا","نعم","كل",
  "بين","غير","عبر","خلال","حول","ضد","أو","ثم","لم","لن",
  "قد","فقد","وقد","منذ","إذا","إذ","بما","مما","فمن","وفي",
  "وعلى","ومع","وإن","أما","بل","فإن","ولا","وهذا","وهذه",
  // additional connectors / demonstratives / fillers
  "هناك","هنا","أيضا","ايضا","لذلك","لذا","عندما","كذلك",
  "سوف","لقد","إلا","سوى","معه","معها","منه","منها",
  "إليه","إليها","عليه","عليها","فيه","فيها","لهذا","لهذه",
  // newswire reporting verbs/phrasing
  "قال","قالت","وقال","وقالت","أضاف","أضافت","وأضاف","وأضافت",
  "أعلن","أعلنت","وأعلن","وأعلنت","ذكر","ذكرت","وذكر","وذكرت",
  "أكد","أكدت","وأكد","وأكدت","أشار","أشارت","وأشار","وأشارت",
  "بحسب","وفقا","وفقاً","حسب","تابع","تابعت","أوضح","أوضحت",
  // breaking-news flag words — attached to nearly any story,
  // add no search specificity
  "عاجل","خبر عاجل","عاجل الآن"
]);

const STOPS_EN = new Set([
  "this","that","these","those","with","from","have","has","had",
  "been","were","also","into","over","under","more","most",
  "some","such","than","then","when","where","which","what","while",
  "during","after","before","about","because","through","among",
  "between","against","without","within","upon","said","says",
  "according","reported","reportedly","officials","statement",
  "including","their","there","here","will","would","could","should",
  "they","them","your","just","like","make","made","being","still",
  "only","very","much","many","each","both","other","another",
  "first","last","year","years","time","news",
  // breaking-news flag words
  "breaking","breakingnews"
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

        // ─── OCR: extract text from an image URL via the ngrok /ocr endpoint,
        //     then return the raw text so the content script can pipe it
        //     through the normal HAQQ_VERIFY_TEXT pipeline unchanged.
        case "HAQQ_OCR_IMAGE":
          return sendResponse({ data: await ocrImage(msg.payload) });
          
          
          
        default:
          return sendResponse({ error: "Unknown message type" });
      }
    } catch (e) {
      return sendResponse({ error: e.message });
    }
  })();
  return true;
});


async function classifyWithAI(text) {
  // ── Guard — never send empty text ─────────────────────
  if (!text || text.trim().length === 0) {
    console.warn("[HAQQ] classifyWithAI — empty text, skipping");
    return null;
  }

  const payload = text.trim().slice(0, 500);
  console.log("[HAQQ] Sending to AI:", payload);

  try {
    const res = await fetch(`${NGROK_URL}/classify`, {
      method: "POST",
      headers: {
        "Content-Type":               "application/json",
        "ngrok-skip-browser-warning": "true"
      },
      body: JSON.stringify({ text: payload })
    });

    console.log("[HAQQ] Classify status:", res.status);

    if (!res.ok) {
      const err = await res.text();
      console.warn("[HAQQ] Classify HTTP error:", err.slice(0, 200));
      return null;
    }

    const data = await res.json();
    console.log("[HAQQ] AI response:", data);

    // ── Guard — server returned validation error ──────────
    if (data.detail) {
      console.warn("[HAQQ] Server validation error:", JSON.stringify(data.detail));
      return null;
    }

    return data;

  } catch (e) {
    console.warn("[HAQQ] AI unreachable:", e.message);
    return null;
  }
}

// ─── OCR IMAGE ─────────────────────────────────────────────────
// Sends the image URL to the ngrok /ocr endpoint and returns the
// extracted text string. The content script then runs that text
// through the normal HAQQ_VERIFY_TEXT pipeline without any changes.
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
        "ngrok-skip-browser-warning": "true"
      },
      body: JSON.stringify({ image_url: imageUrl })
    });

    console.log("[HAQQ] OCR status:", res.status);

    if (!res.ok) {
      const err = await res.text();
      console.warn("[HAQQ] OCR HTTP error:", err.slice(0, 200));
      return { text: "" };
    }

    const data = await res.json();
    console.log("[HAQQ] OCR response:", JSON.stringify(data).slice(0, 200));

    // Accept either { text: "..." } or { extracted_text: "..." } from the server
    const extracted = data.text || data.extracted_text || "";
    return { text: extracted };

  } catch (e) {
    console.warn("[HAQQ] OCR unreachable:", e.message);
    return { text: "" };
  }
}





async function verifyText({ text, lang }) {
  if (!text || text.trim().length < 20)
    return result("unverified", 0, "النص قصير جداً للتحقق.", []);

    // ── 1. CLASSIFY ───────────────────────────────────────
  console.log("[HAQQ] Classifying...");

  const classification = await classifyWithAI(text.slice(0, 500));
  console.log("[HAQQ] Classification:", classification);

 // ── 2. NOT NEWS → stop ────────────────────────────────
  if (
    classification &&
    !classification.detail &&
    (
      !classification.is_news ||
      classification.news_score < 0.50 ||
      classification.news_score <= classification.non_news_score
    )
  ) {
    return {
      verdict:     "non_news",
      confidence:  classification?.non_news_score ?? 0.9,
      explanation: "💬 هذا محتوى غير إخباري",
      sources:     []
    };
  }

  // ── null classification → fail open, continue to search ──
  if (!classification) {
    console.warn("[HAQQ] Classification failed — continuing to search anyway");
  }
  // ── 3. IS NEWS → extract keywords ─────────────────────
  const keywords = extractKeywords(text);
  console.log("[HAQQ] Keywords:", keywords, "| length:", keywords?.length);

  if (!keywords)
    return result("unverified", 0.2, "لا توجد كلمات مفتاحية", []);

  const kwCount = keywords.trim().split(/\s+/).length;
  if (kwCount < 2)
    return result("unverified", 0.2, "الكلمات المفتاحية غير كافية للبحث", []);

  const apiQuery    = fitKeywordsToLimit(keywords, API_QUERY_LIMIT);
  const searchQuery = fitKeywordsToLimit(keywords, SEARCH_QUERY_LIMIT);

  console.log("[HAQQ] API query:", apiQuery);
  console.log("[HAQQ] Search-engine query:", searchQuery);

  // ── 4. CACHE ──────────────────────────────────────────
  const cKey = "text::" + normalise(text).slice(0, 100);
  if (cache.has(cKey))    return cache.get(cKey);
  if (inFlight.has(cKey)) return inFlight.get(cKey);

  // ── 5. SEARCH ─────────────────────────────────────────
  const promise = (async () => {
    console.log("[HAQQ] Searching:", searchQuery);
    console.log("[HAQQ] Language:", lang);

    let articles = [];
    if (lang === "ar") {
      const [ndAr, curAr, gnAr] = await Promise.all([
        fetchNewsData(apiQuery, "ar"),
        fetchCurrents(apiQuery, "ar"),
        fetchGNews(apiQuery, "ar"),
      ]);

      console.group("[HAQQ] Articles (ar)");
      console.log(`📰 NewsData (${ndAr.length})`, ndAr.map(a => ({ title: a.title, source: a.source_id })));
      console.log(`📰 Currents (${curAr.length})`, curAr.map(a => ({ title: a.title, source: a.source_name })));
      console.log(`📰 GNews   (${gnAr.length})`, gnAr.map(a => ({ title: a.title, source: a.source_name })));
      console.groupEnd();

      articles = [...ndAr, ...curAr, ...gnAr];

      if (articles.length === 0) {
        console.log("[HAQQ] Still empty — trying search engine fallback...");

        articles = await fetchSearchFallback(searchQuery, "ar");
      }
   } else {
      const [ndEn, curEn, gnEn] = await Promise.all([
        fetchNewsData(searchQuery, "en"),
        fetchCurrents(searchQuery, "en"),
        fetchGNews(searchQuery, "en"),
      ]);

      console.group("[HAQQ] Articles (en)");
      console.log(`📰 NewsData (${ndEn.length})`, ndEn.map(a => ({ title: a.title, source: a.source_id })));
      console.log(`📰 Currents (${curEn.length})`, curEn.map(a => ({ title: a.title, source: a.source_name })));
      console.log(`📰 GNews   (${gnEn.length})`, gnEn.map(a => ({ title: a.title, source: a.source_name })));
      console.groupEnd();

      articles = [...ndEn, ...curEn, ...gnEn];

      if (articles.length === 0) {
        console.log("[HAQQ] Empty — trying search engine fallback...");
        articles = await fetchSearchFallback(searchQuery, "en");
      }
    }
    console.log("[HAQQ] Total articles:", articles.length);

    const out         = scoreArticles(text, searchQuery, articles);
    out._cKey         = cKey;
    out._originalText = text;

    cache.set(cKey, out);
    inFlight.delete(cKey);
    stats.total++;
    stats[out.verdict] = (stats[out.verdict] || 0) + 1;
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

// ─── SEARCH ENGINE FALLBACK (Google News RSS primary, DuckDuckGo secondary) ──
// Replaces GDELT (5s+ rate limiting), Brave (dropped free tier Feb 2026),
// and SearXNG public instances (their bot/limiter plugin 403s automated
// format=json requests by design — not fixable by picking a different one).
// Google News RSS is a public feed meant for machine consumption rather
// than a scraped HTML search page, so it isn't subject to the same
// bot-detection wall. It's unofficial/undocumented (Google could change
// the format without notice) but has been stable for years and is widely
// used for exactly this purpose. No API key, no card, no signup.

async function fetchGoogleNewsRSS(query, lang) {
  const safeQuery = query.slice(0, 200).trim();
  if (safeQuery.length < 4) {
    console.warn("[HAQQ] Google News RSS query too short — skipping");
    return [];
  }

  const url = new URL("https://news.google.com/rss/search");
  url.searchParams.set("q", safeQuery);
  if (lang === "ar") {
    url.searchParams.set("hl", "ar");
    url.searchParams.set("gl", "EG");
    url.searchParams.set("ceid", "EG:ar");
  } else {
    url.searchParams.set("hl", "en-US");
    url.searchParams.set("gl", "US");
    url.searchParams.set("ceid", "US:en");
  }

  const controller = new AbortController();
  const timer       = setTimeout(() => controller.abort(), 8000);

  try {
    const res = await fetch(url, { signal: controller.signal });
    clearTimeout(timer);

    if (!res.ok) {
      console.warn("[HAQQ] Google News RSS HTTP error:", res.status);
      return [];
    }

    const xml     = await res.text();
    const results = parseGoogleNewsRSS(xml);
    console.log(`[HAQQ] Google News RSS got ${results.length} results`);
    return results;

  } catch (e) {
    clearTimeout(timer);
    console.warn("[HAQQ] Google News RSS fetch error:", e.message);
    return [];
  }
}

// Service workers have no DOMParser, so the RSS/XML is pulled apart with regex.
function parseGoogleNewsRSS(xml) {
  const results = [];
  const itemRe  = /<item>([\s\S]*?)<\/item>/g;

  let match;
  while ((match = itemRe.exec(xml)) !== null && results.length < 10) {
    const block = match[1];
    const title = extractXmlTag(block, "title");
    const link  = extractXmlTag(block, "link");
    const desc  = extractXmlTag(block, "description");

    if (!title || !link) continue;

    const srcMatch  = block.match(/<source[^>]*url="([^"]*)"[^>]*>([\s\S]*?)<\/source>/);
    const srcName   = srcMatch ? decodeXmlEntities(stripCdata(srcMatch[2])).trim() : "";
    let   srcHost   = "";
    if (srcMatch) {
      try { srcHost = new URL(srcMatch[1]).hostname.replace(/^www\./, ""); } catch {}
    }

    results.push({
      title:       decodeXmlEntities(stripCdata(title)),
      description: decodeXmlEntities(stripTags(stripCdata(desc))),
      link:        decodeXmlEntities(stripCdata(link)),
      source_id:   srcHost || srcName.toLowerCase(),
      source_name: srcName,
      _api:        "googlenews_rss"
    });
  }
  return results;
}

function extractXmlTag(block, tag) {
  const m = block.match(new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`, "i"));
  return m ? m[1] : "";
}

function stripCdata(str) {
  return str.replace(/^<!\[CDATA\[/, "").replace(/\]\]>$/, "").trim();
}

function decodeXmlEntities(str) {
  return str
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .trim();
}

// ─── DuckDuckGo HTML scrape — no API key, secondary fallback ──
async function fetchDuckDuckGo(query, lang) {
  const safeQuery = query.slice(0, 200).trim();
  if (safeQuery.length < 4) {
    console.warn("[HAQQ] DDG query too short — skipping");
    return [];
  }

  const url = new URL("https://html.duckduckgo.com/html/");
  url.searchParams.set("q",  safeQuery);
  url.searchParams.set("kl", lang === "ar" ? "xa-ar" : "us-en");

  try {
    const res = await fetch(url, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
      }
    });

    if (!res.ok) {
      console.warn("[HAQQ] DDG HTTP error:", res.status);
      return [];
    }

    const html    = await res.text();
    const results = parseDuckDuckGoHTML(html);
    console.log(`[HAQQ] DDG got ${results.length} results`);
    return results;

  } catch (e) {
    console.warn("[HAQQ] DDG fetch error:", e.message);
    return [];
  }
}

// Service workers have no DOMParser, so results are pulled out with regex.
function parseDuckDuckGoHTML(html) {
  const results = [];
  const blockRe = /<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>[\s\S]*?(?:<a[^>]+class="result__snippet"[^>]*>([\s\S]*?)<\/a>)?/g;

  let match;
  while ((match = blockRe.exec(html)) !== null && results.length < 10) {
    const rawHref  = match[1];
    const rawTitle = stripTags(match[2]);
    const rawDesc  = match[3] ? stripTags(match[3]) : "";
    const realUrl  = decodeDuckDuckGoUrl(rawHref);

    if (!rawTitle || !realUrl) continue;

    let hostname = "";
    try { hostname = new URL(realUrl).hostname.replace(/^www\./, ""); } catch {}

    results.push({
      title:       rawTitle,
      description: rawDesc,
      link:        realUrl,
      source_id:   hostname,
      source_name: hostname,
      _api:        "duckduckgo"
    });
  }
  return results;
}

// DDG wraps result links: //duckduckgo.com/l/?uddg=<encoded-real-url>&rut=...
function decodeDuckDuckGoUrl(href) {
  try {
    const full = href.startsWith("//") ? "https:" + href : href;
    const u    = new URL(full);
    const real = u.searchParams.get("uddg");
    return real ? decodeURIComponent(real) : full;
  } catch {
    return href;
  }
}

function stripTags(str) {
  return str
    .replace(/<[^>]+>/g, "")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&nbsp;/g, " ")
    .trim();
}

// ─── Combined entry point: Google News RSS first, DuckDuckGo if empty ──
async function fetchSearchFallback(query, lang) {
  let results = await fetchGoogleNewsRSS(query, lang);
  if (results.length === 0) {
    console.log("[HAQQ] Google News RSS empty — trying DuckDuckGo...");
    results = await fetchDuckDuckGo(query, lang);
  }
  return results;
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

    // ── Log full response so we can see the real error ───
    console.log("[HAQQ] NewsData full response:", JSON.stringify(data).slice(0, 300));

    if (data.status === "error") {
      console.warn("[HAQQ] NewsData error:", 
        data.message        ||
        data.results?.message ||
        data.results?.code    ||
        JSON.stringify(data)
      );
      return [];
    }

    console.log(`[HAQQ] NewsData got ${(data.results || []).length} articles`);
    return data.results || [];

  } catch (e) {
    console.error("[HAQQ] NewsData fetch error:", e.message);
    return [];
  }
}


// ─── CURRENTS API ─────────────────────────────────────────
async function fetchCurrents(query, lang) {
  const url = new URL("https://api.currentsapi.services/v1/search");
  url.searchParams.set("apiKey",   CURRENTS_API_KEY);
  url.searchParams.set("keywords", query);
  url.searchParams.set("language", lang);
  url.searchParams.set("limit",    "10");

  try {
    const res  = await fetch(url);
    const data = await res.json();
    if (!data.news) return [];
    return data.news.map(a => ({
      title:       a.title       || "",
      description: a.description || "",
      link:        a.url         || "#",
      source_id:   a.author      || "",
      source_name: a.author      || "",
      _api:        "currents"
    }));
  } catch (e) {
    console.warn("[HAQQ] Currents fetch error:", e.message);
    return [];
  }
}

// ─── GNEWS API ────────────────────────────────────────────
async function fetchGNews(query, lang) {
  const url = new URL("https://gnews.io/api/v4/search");
  url.searchParams.set("token",    GNEWS_API_KEY);
  url.searchParams.set("q",        query);
  url.searchParams.set("lang",     lang);
  url.searchParams.set("max",      "10");

  try {
    const res  = await fetch(url);
    const data = await res.json();
    if (!data.articles) return [];
    return data.articles.map(a => ({
      title:       a.title                 || "",
      description: a.description           || "",
      link:        a.url                   || "#",
      source_id:   a.source?.name?.toLowerCase() || "",
      source_name: a.source?.name          || "",
      _api:        "gnews"
    }));
  } catch (e) {
    console.warn("[HAQQ] GNews fetch error:", e.message);
    return [];
  }
}
async function fetchFreeNews(query, lang) {
  const url = new URL(FREENEWS_BASE);
  url.searchParams.set("q",        query);
  url.searchParams.set("language", lang);
  url.searchParams.set("limit",    "10");

  // FIX: 5s timeout — if FreeNews doesn't respond, skip it silently
  const controller = new AbortController();
  const timer      = setTimeout(() => controller.abort(), 10000);

  try {
    const res  = await fetch(url, {
      headers: { "x-api-key": FREENEWS_API_KEY },
      signal:  controller.signal
    });
    clearTimeout(timer);

    const data = await res.json();
    console.log("[HAQQ] FreeNews raw response:", data);
    if (!data.articles) return [];

    return data.articles.map(a => ({
      title:       a.title       || "",
      description: a.description || a.subtitle || "",
      link:        a.url         || "#",
      source_id:   a.source?.id   || "",
      source_name: a.source?.name || "",
      _api:        "freenews"
    }));

  } catch (e) {
    clearTimeout(timer);
    if (e.name === "AbortError")
      console.warn("[HAQQ] FreeNews timed out — skipping");
    else
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
// Arabic words run shorter than English ones on average, so a single
// length cutoff was either too loose for English or too strict for
// Arabic. A flat `length > 3` was silently dropping real 3-letter
// Arabic words (e.g. "مصر" = Egypt, "حرب" = war, "نفط" = oil) before
// they ever reached the stop-word check.
function isArabicWord(w) {
  return /[\u0600-\u06FF]/.test(w);
}

function extractKeywords(text) {
  const tokens = normalise(text)
    .split(/\s+/)
    .filter(w => {
      const minLen = isArabicWord(w) ? 2 : 4;
      return w.length >= minLen && !STOPS_AR.has(w) && !STOPS_EN.has(w);
    });

  const words = [...new Set(tokens)];
  const query = words.join(" ");

  console.log("[HAQQ] Keywords built (full):", query, "| length:", query.length);
  return query || null;
}

// ─── Per-destination keyword fitting ──────────────────────
// News APIs (NewsData/Currents/GNews) want short, precise queries.
// RSS/search-engine fallback handles longer queries fine — give it
// more room instead of reusing the API-fitted string.
const API_QUERY_LIMIT    = 95;  // NewsData / Currents / GNews
const SEARCH_QUERY_LIMIT = 200; // Google News RSS / DuckDuckGo

function fitKeywordsToLimit(keywords, limit) {
  if (keywords.length <= limit) return keywords;
  return keywords.slice(0, limit).replace(/\s\S*$/, "").trim();
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