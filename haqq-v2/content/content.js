// ─── HAQQ Content Script v3 ───────────────────────────────
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
  // REMOVED the hasActions check — buttons load late, don't gate on them
  if (!el.innerText || el.innerText.trim().length < 10) return false;
  return true;
}


// ─── EXTRACT ──────────────────────────────────────────────
function extractAll(postEl) {
  const out = { text: null, imageUrl: null, videoUrl: null, videoPoster: null };

  // ── Expand "See more" before extracting text ──────────
  for (const btn of postEl.querySelectorAll([
    '[role="button"][tabindex="0"]',
    'div[role="button"]',
    'span[role="button"]',
  ].join(","))) {
    const t = btn.innerText?.trim();
    if (t === "See more" || t === "اقرأ المزيد" || t === "See More") {
      btn.click();
    }
  }
  // ──────────────────────────────────────────────────────

  // Text: use innerText on the message container
  const msgEl =
    postEl.querySelector('[data-ad-comet-preview="message"]') ||
    postEl.querySelector('[data-ad-preview="message"]')       ||
    postEl.querySelector('[data-ad-rendering-role="story_message"]');

  if (msgEl) {
    const t = msgEl.textContent?.trim().replace(/\n+/g, " ");
    if (t && t.length > 10) out.text = t.slice(0, 3000);
  }

  // Fallback text
  if (!out.text) {
    for (const b of postEl.querySelectorAll('[dir="auto"]')) {
      const t = b.textContent?.trim().replace(/\n+/g, " ");
      if (t && t.length > 30) { out.text = t.slice(0, 3000); break; }
    }
  }
  // Video: FB lazy-loads src, poster is always available
  const vid = postEl.querySelector("video");
  if (vid) {
    const src = vid.src || vid.currentSrc || "";
    const poster = vid.getAttribute("poster") || "";
    if (src && !src.startsWith("blob:") && src.length > 10) out.videoUrl = src;
    if (poster) out.videoPoster = poster;
    // Use poster as video proxy so button always shows for video posts
    if (!out.videoUrl && poster) out.videoUrl = poster;
    log("Video — src:", src.slice(0, 60), "| poster:", poster.slice(0, 60));
  }

  // Image: skip UI assets, profile pics, video posters already captured
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
function buildToolbar(postEl, content) {
  const toolbar = document.createElement("div");
  toolbar.className = "haqq-toolbar";

  const lbl = document.createElement("span");
  lbl.className = "haqq-toolbar-lbl";
  lbl.textContent = "🔍 حقّق";
  toolbar.appendChild(lbl);

  const grp = document.createElement("div");
  grp.className = "haqq-btn-group";
  if (content.text)     grp.appendChild(makeBtn("text",  postEl, content));
  if (content.imageUrl) grp.appendChild(makeBtn("image", postEl, content));
  if (content.videoUrl) grp.appendChild(makeBtn("video", postEl, content));
  toolbar.appendChild(grp);

  return toolbar;
}

const BTN_DEF = {
  text:  { label: "📝 نص",    title: "تحقق من صحة النص"   },
  image: { label: "🖼️ صورة", title: "تحليل الصورة"        },
  video: { label: "🎬 فيديو", title: "تحليل الفيديو"       },
};

function makeBtn(type, postEl, content) {
  const def = BTN_DEF[type];

  const btn = document.createElement("button");
  btn.className = `haqq-btn haqq-btn--${type}`;
  btn.dataset.type = type;
  btn.dataset.loading = "false";

  btn.innerHTML = def.label;
  btn.title = def.title;

  const setLoading = () => {
    btn.classList.add("haqq-btn--loading");
    btn.dataset.original = btn.innerHTML;
    btn.innerHTML = `<span class="haqq-spinner"></span>`;
    btn.disabled = true;
    btn.dataset.loading = "true";
  };

  const setIdle = () => {
    btn.classList.remove("haqq-btn--loading", "haqq-btn--error");
    btn.innerHTML = def.label;
    btn.disabled = false;
    btn.dataset.loading = "false";
  };

  const setError = (message) => {
    btn.classList.remove("haqq-btn--loading");
    btn.classList.add("haqq-btn--error");
    btn.innerHTML = "⚠️";
    btn.title = message;

    setTimeout(() => {
      setIdle();
      btn.title = def.title;
    }, 3000);
  };

  const waitForContentUpdate = async (el, timeout = 1500) => {
    const start = Date.now();
    let last = extractAll(el);

    while (Date.now() - start < timeout) {
      await new Promise((r) => setTimeout(r, 150));

      const current = extractAll(el);
      if (
        current.text !== last.text ||
        current.imageUrl !== last.imageUrl ||
        current.videoUrl !== last.videoUrl
      ) {
        last = current;
      } else {
        return current;
      }
    }

    return last;
  };

  btn.addEventListener("click", async () => {
    if (btn.dataset.loading === "true") return;

    setLoading();

    try {
      const freshContent = await waitForContentUpdate(postEl);

      const finalContent = {
        text: freshContent.text ?? content.text,
        imageUrl: freshContent.imageUrl ?? content.imageUrl,
        videoUrl: freshContent.videoUrl ?? content.videoUrl,
        videoPoster: freshContent.videoPoster ?? content.videoPoster,
      };

      const result = await sendToBackground(type, finalContent, postEl);

      showVerdict(postEl, result, type, btn);
    } catch (err) {
      setError(err?.message || "Something went wrong");
    } finally {
      // تأمين إضافي ضد أي تعليق
      if (!btn.classList.contains("haqq-btn--error")) {
        setIdle();
      }
    }
  });

  return btn;
}
function detectLanguage(text) {
  const arabicChars  = (text.match(/[\u0600-\u06FF]/g) || []).length;
  const englishChars = (text.match(/[a-zA-Z]/g) || []).length;
  const total        = arabicChars + englishChars;
  if (total === 0) return "en";
  if (arabicChars / total >= 0.5) return "ar";
  return "en";
}
// ─── BACKGROUND MESSAGES ──────────────────────────────────
async function sendToBackground(type, content) {

  // ── TEXT: AI filter first via background ───────────────
  if (type === "text" && content.text) {

    // Clean text once and use everywhere
    const cleanText = content.text
      .replace(/\n+/g, " ")
      .replace(/\s+/g, " ")
      .replace(/see more/gi, "")
      .replace(/see less/gi, "")
      .replace(/اقرأ المزيد/g, "")
      .replace(/see original/gi, "")
      .replace(/rate this translation/gi, "")
      .replace(/read more[^]*/gi, "")        // remove "Read more" and everything after
      .replace(/https?:\/\/\S+/g, "")        // remove all URLs
      .replace(/·/g, "")
      .trim();

    const ai = await new Promise((resolve) => {
      const t = setTimeout(() => resolve(null), 8000);
      chrome.runtime.sendMessage(
        { type: "HAQQ_CLASSIFY_AI", payload: { text: cleanText } },
        (res) => {
          clearTimeout(t);
          if (chrome.runtime.lastError) return resolve(null);
          resolve(res?.data || null);
        }
      );
    });

    console.log("[HAQQ] AI result:", ai);

    if (!ai) {
      // AI unreachable — fall through to NewsData
    } else if (!ai.is_news && ai.score > 0.75) {
      return {
        verdict: "unverified",
        confidence: ai.score,
        explanation: "💬 هذا المحتوى يبدو رأياً شخصياً وليس خبراً قابلاً للتحقق",
        sources: []
      };
    } 
    // ai.is_news === true → fall through to NewsData with cleanText
    const lang = detectLanguage(cleanText);
    console.log("[HAQQ] Detected language:", lang);

    const msg = {
      type: "HAQQ_VERIFY_TEXT",
      payload: { text: cleanText, lang }  // ← now passes lang
    };
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error("الخادم لا يستجيب")), 25000);
      chrome.runtime.sendMessage(msg, (res) => {
        clearTimeout(t);
        if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
        if (!res)      return reject(new Error("لا استجابة"));
        if (res.error) return reject(new Error(res.error));
        resolve(res.data);
      });
    });
  }

  // ── IMAGE / VIDEO: go straight to background ───────────
  const msg = {
    image: { type: "HAQQ_VERIFY_IMAGE", payload: { imageUrl: content.imageUrl } },
    video: { type: "HAQQ_VERIFY_VIDEO", payload: {
      videoUrl: content.videoUrl, videoPoster: content.videoPoster, text: content.text
    }},
  }[type];

  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("الخادم لا يستجيب")), 25000);
    chrome.runtime.sendMessage(msg, (res) => {
      clearTimeout(t);
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
      if (!res)      return reject(new Error("لا استجابة"));
      if (res.error) return reject(new Error(res.error));
      resolve(res.data);
    });
  });
}
// ─── INSERT TOOLBAR ───────────────────────────────────────
function insertToolbar(postEl, toolbar) {
  for (const sel of ['[aria-label="Leave a comment"]','[aria-label="Comment"]','[aria-label="Like"]']) {
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
  fact:         { ar: "✅ موثوق",                      cls: "fact",         icon: "✅" },
  verified:     { ar: "✅ موثوق",                      cls: "fact",         icon: "✅" },
  unverified:   { ar: "⚠️ غير مؤكد",                  cls: "unverified",   icon: "⚠️" },
  fake:         { ar: "❌ مضلل على الأرجح",            cls: "fake",         icon: "❌" },
  ai_generated: { ar: "🤖 مُولَّد بالذكاء الاصطناعي", cls: "ai",           icon: "🤖" },
};
const TYPE_NAMES = { text: "النص", image: "الصورة", video: "الفيديو" };

function showVerdict(postEl, result, type, btn) {
  postEl.querySelector(`.haqq-badge[data-type="${type}"]`)?.remove();

  const cfg  = VERDICT_CFG[result.verdict] || VERDICT_CFG.unverified;
  const pct  = Math.round((result.confidence || 0) * 100);
  const name = TYPE_NAMES[type];

  const sourcesHtml = (result.sources || [])
    .slice(0, 5)
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
      ? `<div class="haqq-sources"><span class="haqq-src-lbl">📎 المصادر</span><div class="haqq-src-list">${sourcesHtml}</div></div>`
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
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

// ─── SCAN + OBSERVER ──────────────────────────────────────
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
  log("HAQQ v3 init");
  scanForPosts();
  [800, 2000, 4000, 7000].forEach(t => setTimeout(scanForPosts, t));
  observer.observe(document.body, { childList: true, subtree: true, attributes: false });
  setInterval(scanForPosts, 3000);
}

document.readyState === "loading"
  ? document.addEventListener("DOMContentLoaded", init)
  : init();