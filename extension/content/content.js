// ─── HAQQ Content Script v16 ─────────────────────────────────
// v16 fix (from screenshot showing Like/Comment/Share crushed by the
// verdict badge): every prior attempt to merge the toolbar directly
// INTO a platform's native action row (Facebook's Like/Comment/Share,
// or Instagram/TikTok's icon column) kept breaking that row's layout
// once a badge also needed to appear nearby — v15's row-tagging fix
// helped but the underlying merge was still fragile.
//
// Switched approach entirely: the toolbar (buttons) and any resulting
// badge now live inside a single dedicated `.haqq-panel` div, inserted
// as a SIBLING right after the native action row — never merged into
// it. getOrCreatePanel() finds the native row (reusing the existing
// Facebook isFacebookActionRow detection / IG-TikTok anchor selectors)
// purely to know WHERE to place the panel, then creates one normal
// block-level div after it. Both makeToolbar's buttons and any later
// verdict/non-news badge get appended into that same panel. This
// guarantees Like/Comment/Share can never be touched, at the cost of
// the toolbar sitting just below the action row instead of visually
// merged into it — worth it after three rounds of layout breakage from
// the merge approach.
//
// v14 fix (carried over): the first post in the feed (Facebook and
// TikTok both reported) wasn't getting a toolbar at all. Root cause:
// processPost() used to mark a post as PROCESSED_ATTR-"done" before
// extractAll() had even run — so if the scanner reached a post before
// its <video>/<img> had actually mounted, it would find no media,
// permanently mark itself "already handled", and never get a toolbar
// even once the media loaded a moment later. Fixed by only marking
// PROCESSED_ATTR once there's confirmed text/media to show.
//
// v13 fix (carried over): Facebook's real aria-labels are dynamic
// ("React with Like to {page}'s post", "Comment on {page}'s post") or a
// different fixed string for Share ("Send this to friends or post it
// on your profile."). Switched primary detection to Facebook's static
// `data-ad-rendering-role` marker (like_button/comment_button/
// share_button), with the real aria-label strings as fallback.
//
// v7 change (carried over): extension-context-invalidation guard —
// any chrome.runtime call is wrapped; first failure permanently stops
// the observer/interval and shows a "reload the page" state.
//
// v6 change (carried over): Facebook gets "content" (text+image) AND
// "aimedia" (AI-media). Instagram/TikTok get "aimedia" ONLY — a
// deliberate product decision, not a technical limitation.
//
// NOTE: Instagram/TikTok POST_SELECTORs and Facebook's action-row
// markers below are best-effort based on commonly-seen attributes as of
// this writing. All three platforms change markup often — verify
// against live pages via devtools and adjust before relying on this in
// production.
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
  // Where the button icon should sit — right before the bookmark icon,
  // matching the gap in the marked screenshot. VERIFY LIVE — IG's
  // bookmark aria-label has been "Save" historically but changes.
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
  // Insert right before the like (heart) icon — the gap right after
  // the avatar, matching the marked screenshot.
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

  if (PLATFORM === "instagram" || PLATFORM === "tiktok") {
    const insertedNatively = insertButtonsIntoActionColumn(postEl, grp);
    if (!insertedNatively) {
      const panel = getOrCreatePanel(postEl);
      panel.insertBefore(grp, panel.firstChild);
    }
  } else {
    // Facebook (and any other platform): always insert a panel block below
    // the action row — works for both labeled and compact icon-only layouts.
    const panel = getOrCreatePanel(postEl);
    if (!panel.querySelector(":scope > .haqq-btn-group")) {
      panel.insertBefore(grp, panel.firstChild);
    }
  }
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

  if (!CFG.mediaOnly && (content.text || content.imageUrl)) {
    grp.appendChild(makeBtn("content", postEl, content));
  }
  if (content.imageUrl || content.videoUrl) {
    grp.appendChild(makeBtn("aimedia", postEl, content));
  }

  toolbar.appendChild(grp);
  return toolbar;
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
    btn.disabled        = false;
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

// ─── AI-GENERATED / MANIPULATED MEDIA DETECTION ───────────
async function verifyMedia(content) {
  if (!content.imageUrl && !content.videoUrl) {
    return result("inconclusive", 0, "لا توجد صورة أو فيديو لتحليلها في هذا المنشور.", []);
  }

  if (!isContextValid()) {
    killIfContextInvalid();
    return Promise.reject(new Error(CONTEXT_DEAD_MSG));
  }

  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("الخادم لا يستجيب")), 30000);
    try {
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
// Like/Comment/Share row is never touched, regardless of how many
// buttons/badges HAQQ ends up showing for this post.
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
  log(`HAQQ v16 init — platform: ${PLATFORM}, mediaOnly: ${CFG.mediaOnly}`);
  scanForPosts();
  [800, 2000, 4000, 7000].forEach(t => setTimeout(scanForPosts, t));
  observer.observe(document.body, { childList: true, subtree: true, attributes: false });
  scanInterval = setInterval(scanForPosts, 3000);
}

document.readyState === "loading"
  ? document.addEventListener("DOMContentLoaded", init)
  : init();