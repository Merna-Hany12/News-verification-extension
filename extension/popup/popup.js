// popup.js — all logic here, no inline JS in the HTML

document.addEventListener("DOMContentLoaded", () => {

  document.getElementById("resetBtn").addEventListener("click", resetStats);

  // ── Load saved lang on open ──
  chrome.storage.local.get(["news_lang"], (res) => {
    if (chrome.runtime.lastError) {
      showMsg("err", "❌ خطأ في القراءة: " + chrome.runtime.lastError.message);
      return;
    }
    const lang = res.news_lang || "ar";
    document.getElementById("lang").value = lang;
    updatePopupLanguage(lang);
  });

  document.getElementById("lang").addEventListener("change", (e) => {
    const newLang = e.target.value;
    updatePopupLanguage(newLang);
    chrome.storage.local.set({ news_lang: newLang }, () => {
      showMsg("ok", newLang === "en" ? "✔ Language saved" : "✔ تم حفظ اللغة");
    });
  });

  loadStats();
});

const POPUP_I18N = {
  "ui-sub": { ar: "كاشف المحتوى المضلل على منصات التواصل", en: "Misinformation & AI-media detector" },
  "ui-total": { ar: "إجمالي", en: "Total" },
  "ui-fact": { ar: "موثوق", en: "Verified" },
  "ui-unver": { ar: "غير مؤكد", en: "Unverified" },
  "ui-fake": { ar: "مضلل", en: "Fake" },
  "ui-ai": { ar: "AI", en: "AI Generated" },
  "resetBtn": { ar: "↺ إعادة تعيين الإحصائيات", en: "↺ Reset Stats" }
};

function updatePopupLanguage(lang) {
  document.documentElement.dir = lang === "en" ? "ltr" : "rtl";
  for (const [id, translations] of Object.entries(POPUP_I18N)) {
    const el = document.getElementById(id);
    if (el) el.textContent = translations[lang] || translations.ar;
  }
}

// ── Stats ─────────────────────────────────────────────────
function loadStats() {
  chrome.runtime.sendMessage({ type: "HAQQ_GET_STATS" }, (res) => {
    if (chrome.runtime.lastError) return;
    if (!res?.data?.stats) return;
    const s = res.data.stats;
    document.getElementById("s-total").textContent = s.total        || 0;
    document.getElementById("s-fact").textContent  = s.fact         || 0;
    document.getElementById("s-unver").textContent = s.unverified   || 0;
    document.getElementById("s-fake").textContent  = s.fake         || 0;
    document.getElementById("s-ai").textContent    = s.ai_generated || 0;
  });
}

function resetStats() {
  chrome.runtime.sendMessage({ type: "HAQQ_RESET_STATS" }, () => loadStats());
}

// ── Message display ───────────────────────────────────────
let msgTimer;
function showMsg(cls, text) {
  clearTimeout(msgTimer);
  const el = document.getElementById("msg");
  el.className = "msg " + cls;
  el.textContent = text;
  if (cls === "ok" || cls === "info") {
    msgTimer = setTimeout(() => {
      el.textContent = "";
      el.className = "msg";
    }, 4000);
  }
}