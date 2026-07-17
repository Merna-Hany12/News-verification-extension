// ─── HAQQ Content Script v4 ────────────────────────────────
// v4 change: the old three buttons (text / image / video) are now two:
//   • "content"  → merged text+image verification. Direct post text and
//                  OCR text are extracted in PARALLEL; whichever is the
//                  stronger signal is chosen and sent into the
//                  classification + full verification pipeline.
//   • "aimedia"  → real AI-generated / manipulated media detection,
//                  covering both images and video from a single button.
const PROCESSED_ATTR = "data-haqq-processed";
const DEBUG = true;
function log(...args) { if (DEBUG) console.log("[HAQQ]", ...args); }

// ─── SELECTORS ────────────────────────────────────────────
const POST_SELECTOR = [
  'div[aria-posinset]',
  'div[data-pagelet^="TimelineFeedUnit"]',
  'div[data-pagelet^="FeedUnit"]',
  'div[data-pagelet^="PermalinkPost"]',
  'div[data-pagelet^="GroupsFeed"]',
].join(", ");

// ─── VALIDATE ─────────────────────────────────────────────
function isValidPost(el) {
  if (el.hasAttribute(PROCESSED_ATTR)) return false;
  if (el.getAttribute("aria-posinset")) {
    if (el.parentElement?.closest('[aria-posinset]')) return false;
  }
  if (el.querySelector('[data-visualcompletion="loading-state"]')) return false;
  if (el.querySelector('[aria-label="Loading..."]')) return false;
  if (!el.innerText || el.innerText.trim().length < 10) return false;
  return true;
}

// ─── EXTRACT ──────────────────────────────────────────────
function extractAll(postEl) {
  const out = { text: null, imageUrl: null, videoUrl: null, videoPoster: null };

  for (const btn of postEl.querySelectorAll([
    '[role="button"][tabindex="0"]',
    'div[role="button"]',
    'span[role="button"]',
  ].join(","))) {
    const t = btn.innerText?.trim();
    if (t === "See more" || t === "اقرأ المزيد" || t === "See More") btn.click();
  }

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
      if (t && t.length > 30) { out.text = t.slice(0, 3000); break; }
    }
  }

  const vid = postEl.querySelector("video");
  if (vid) {
    const src    = vid.src || vid.currentSrc || "";
    const poster = vid.getAttribute("poster") || "";
    if (src && !src.startsWith("blob:") && src.length > 10) out.videoUrl = src;
    if (poster) out.videoPoster = poster;
    if (!out.videoUrl && poster) out.videoUrl = poster;
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
    if (w < 100 || h < 80) continue;
    out.imageUrl = src;
    break;
  }

  return out;
}

// ─── PROCESS ──────────────────────────────────────────────
function processPost(postEl) {
  if (!isValidPost(postEl)) return;
  postEl.setAttribute(PROCESSED_ATTR, "true");

  const content = extractAll(postEl);
  log("Post →", { text: !!content.text, img: !!content.imageUrl, vid: !!content.videoUrl });

  const toolbar = buildToolbar(postEl, content);
  insertToolbar(postEl, toolbar);
}

// ─── TOOLBAR ──────────────────────────────────────────────
// Two buttons now:
//   "content" — shown whenever there's text and/or an image to check
//   "aimedia" — shown whenever there's an image and/or a video to check
function buildToolbar(postEl, content) {
  const toolbar = document.createElement("div");
  toolbar.className = "haqq-toolbar";

  const lbl = document.createElement("span");
  lbl.className = "haqq-toolbar-lbl";
  lbl.textContent = "🔍 حقّق";
  toolbar.appendChild(lbl);

  const grp = document.createElement("div");
  grp.className = "haqq-btn-group";
  if (content.text || content.imageUrl)                    grp.appendChild(makeBtn("content", postEl, content));
  if (content.imageUrl || content.videoUrl)                 grp.appendChild(makeBtn("aimedia", postEl, content));
  toolbar.appendChild(grp);

  return toolbar;
}

const BTN_DEF = {
  content: { label: "🔍 تحقّق من المحتوى", title: "تحقق من النص والصورة معاً" },
  aimedia: { label: "🤖 كشف وسائط AI",     title: "كشف الصور/الفيديوهات المولّدة أو المعدّلة بالذكاء الاصطناعي" },
};

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
    btn.innerHTML       = `<span class="haqq-spinner"></span><span class="haqq-btn-loading-txt">جارٍ التحقق…</span>`;
    btn.disabled        = true;
    btn.dataset.loading = "true";
  };

  const setIdle = () => {
    btn.classList.remove("haqq-btn--loading", "haqq-btn--error");
    btn.innerHTML       = def.label;
    btn.disabled        = false;
    btn.dataset.loading = "false";
  };

  const setError = (message) => {
    btn.classList.remove("haqq-btn--loading");
    btn.classList.add("haqq-btn--error");
    btn.innerHTML = "⚠️ حدث خطأ";
    btn.title     = message;
    setTimeout(() => { setIdle(); btn.title = def.title; }, 3000);
  };

  btn.addEventListener("click", async () => {
    if (btn.dataset.loading === "true") return;
    setLoading();

    try {
      // Content is re-extracted fresh on every click (no artificial delay).
      const freshContent = extractAll(postEl);

      const finalContent = {
        text:        freshContent.text        ?? content.text,
        imageUrl:    freshContent.imageUrl    ?? content.imageUrl,
        videoUrl:    freshContent.videoUrl    ?? content.videoUrl,
        videoPoster: freshContent.videoPoster ?? content.videoPoster,
      };

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

// ─── MERGED TEXT + IMAGE VERIFICATION ─────────────────────
// Runs direct-text handling and OCR extraction in parallel (OCR is the only
// slow/async step — the caption text is already in hand), then decides
// which extracted text is the stronger signal before sending it into the
// classification + full LangGraph verification pipeline on the backend.
const MIN_TEXT_LEN = 15;

async function verifyContent(content) {
  const directText = cleanText(content.text || "");

  // OCR is fired immediately, in parallel with everything below — it does
  // NOT wait to find out whether the post text turns out to be enough, or
  // whether the first verdict comes back unverified. This trades an extra
  // /ocr backend call (even when it ends up unused) for lower latency on
  // the retry path, since the OCR result is often already in hand by the
  // time we know we need it.
  const ocrPromise = content.imageUrl
    ? new Promise((resolve) => {
        const t = setTimeout(() => resolve(""), 30000);
        chrome.runtime.sendMessage(
          { type: "HAQQ_OCR_IMAGE", payload: { imageUrl: content.imageUrl } },
          (res) => {
            clearTimeout(t);
            if (chrome.runtime.lastError || !res || res.error) return resolve("");
            resolve(res.data?.text || "");
          }
        );
      })
    : Promise.resolve("");

  console.log("%c[HAQQ] Content verification", "color:#8b5cf6;font-weight:bold");
  console.log("[HAQQ] 📝 Post text:", directText.slice(0, 100), `(${directText.length} chars)`);

  // ── Case 1: post text alone isn't enough to work with — wait for the
  // (already in-flight) OCR result and use whatever text it found instead.
  if (directText.length < MIN_TEXT_LEN) {
    console.log("[HAQQ] Post text too short — waiting for OCR…");
    const ocrText = cleanText(await ocrPromise);
    console.log("[HAQQ] 🔤 OCR text:", ocrText.slice(0, 100), `(${ocrText.length} chars)`);

    if (ocrText.length < MIN_TEXT_LEN) {
      return result("unverified", 0, "لا يوجد نص كافٍ في هذا المنشور للتحقق منه.", []);
    }
    return runVerifyText(ocrText);
  }

  // ── Case 2: post text is enough — verify it first, while OCR keeps
  // running in the background regardless.
  console.log("[HAQQ] Verifying post text first…");
  const firstResult = await runVerifyText(directText);

  // Only if it came back "unverified" — recognized as a claim worth
  // checking, but nothing conclusive was found from the caption alone —
  // do we use the OCR result (already in-flight, likely already resolved)
  // and give it one more shot.
  if (firstResult.verdict === "unverified") {
    console.log("[HAQQ] First verdict was 'unverified' — using OCR result…");
    const ocrText = cleanText(await ocrPromise);
    console.log("[HAQQ] 🔤 OCR text:", ocrText.slice(0, 100), `(${ocrText.length} chars)`);

    if (ocrText.length >= MIN_TEXT_LEN) {
      console.log("[HAQQ] Retrying verification using OCR text…");
      return runVerifyText(ocrText);
    }
  }

  return firstResult;
}

// Sends a single piece of text through the /verify pipeline (classification
// + full LangGraph verification on the backend).
function runVerifyText(text) {
  const lang = detectLanguage(text);
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("الخادم لا يستجيب")), 25000);
    chrome.runtime.sendMessage(
      { type: "HAQQ_VERIFY_TEXT", payload: { text: text.slice(0, 1000), lang } },
      (res) => {
        clearTimeout(t);
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        if (!res)      return reject(new Error("لا استجابة"));
        if (res.error) return reject(new Error(res.error));
        resolve(res.data);
      }
    );
  });
}

// ─── AI-GENERATED / MANIPULATED MEDIA DETECTION ───────────
// Single button covering both images and video — sends whichever URL(s)
// are present to the GPU-accelerated detection pipeline on the backend.
async function verifyMedia(content) {
  if (!content.imageUrl && !content.videoUrl) {
    return result("inconclusive", 0, "لا توجد صورة أو فيديو لتحليلها في هذا المنشور.", []);
  }

  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("الخادم لا يستجيب")), 30000);
    chrome.runtime.sendMessage(
      {
        type: "HAQQ_DETECT_MEDIA",
        payload: {
          imageUrl: content.imageUrl || null,
          videoUrl: content.videoUrl || null,
        },
      },
      (res) => {
        clearTimeout(t);
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        if (!res)      return reject(new Error("لا استجابة"));
        if (res.error) return reject(new Error(res.error));
        resolve(res.data);
      }
    );
  });
}

// ─── NON-NEWS BADGE ───────────────────────────────────────
function showNonNews(postEl, btn, type) {
  postEl.querySelector(`.haqq-badge[data-type="${type}"]`)?.remove();

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

  const toolbar = postEl.querySelector(".haqq-toolbar");
  toolbar
    ? toolbar.parentElement?.insertBefore(badge, toolbar)
    : postEl.insertBefore(badge, postEl.firstChild);
}

// ─── INSERT TOOLBAR ───────────────────────────────────────
function insertToolbar(postEl, toolbar) {
  for (const sel of [
    '[aria-label="Leave a comment"]',
    '[aria-label="Comment"]',
    '[aria-label="Like"]'
  ]) {
    const btn = postEl.querySelector(sel);
    if (!btn) continue;
    let node = btn.parentElement;
    for (let i = 0; i < 7 && node && node !== postEl; i++) {
      if (node.children.length >= 2) {
        node.parentElement?.insertBefore(toolbar, node);
        return;
      }
      node = node.parentElement;
    }
  }
  postEl.appendChild(toolbar);
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
  postEl.querySelector(`.haqq-badge[data-type="${type}"]`)?.remove();

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

  const toolbar = postEl.querySelector(".haqq-toolbar");
  toolbar
    ? toolbar.parentElement?.insertBefore(badge, toolbar)
    : postEl.insertBefore(badge, postEl.firstChild);
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

// ─── SCAN + OBSERVER ──────────────────────────────────────
function scanForPosts() {
  let n = 0;
  document.querySelectorAll(POST_SELECTOR).forEach(el => {
    if (isValidPost(el)) { processPost(el); n++; }
  });
  if (n) log(`+${n} posts`);
}

let scanTimer = null;
const observer = new MutationObserver(() => {
  clearTimeout(scanTimer);
  scanTimer = setTimeout(scanForPosts, 120);
});

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
  log("HAQQ v4 init");
  scanForPosts();
  [800, 2000, 4000, 7000].forEach(t => setTimeout(scanForPosts, t));
  observer.observe(document.body, { childList: true, subtree: true, attributes: false });
  setInterval(scanForPosts, 3000);
}

document.readyState === "loading"
  ? document.addEventListener("DOMContentLoaded", init)
  : init();