// popup.js — all logic here, no inline JS in the HTML

document.addEventListener("DOMContentLoaded", () => {

  // ── Wire up buttons (replaces onclick="..." which CSP blocks) ──
  document.getElementById("saveBtn").addEventListener("click", saveKey);
  document.getElementById("clearBtn").addEventListener("click", clearKey);
  document.getElementById("resetBtn").addEventListener("click", resetStats);

  // ── Load saved key on open ──
  chrome.storage.local.get("newsdata_api_key", (res) => {
    if (chrome.runtime.lastError) {
      showMsg("err", "❌ خطأ في القراءة: " + chrome.runtime.lastError.message);
      return;
    }
    const key = res["newsdata_api_key"];
    if (key) {
      document.getElementById("key").value = key;
      showMsg("ok", "✔ المفتاح موجود ومحفوظ");
    } else {
      showMsg("info", "ℹ️ لا يوجد مفتاح محفوظ بعد");
    }
  });

  loadStats();
});

// ── Save key ──────────────────────────────────────────────
function saveKey() {
  const val = document.getElementById("key").value.trim();

  if (!val) {
    showMsg("err", "❌ الحقل فارغ");
    return;
  }
  if (!val.startsWith("pub_")) {
    showMsg("err", "❌ المفتاح يجب أن يبدأ بـ pub_");
    return;
  }

  const btn = document.getElementById("saveBtn");
  btn.disabled = true;
  btn.textContent = "⏳ جاري الحفظ…";

  chrome.storage.local.set({ newsdata_api_key: val }, () => {
    btn.disabled = false;
    btn.textContent = "💾 حفظ المفتاح";

    if (chrome.runtime.lastError) {
      showMsg("err", "❌ فشل الحفظ: " + chrome.runtime.lastError.message);
      return;
    }

    // Read back to confirm it was actually written
    chrome.storage.local.get("newsdata_api_key", (res) => {
      if (res.newsdata_api_key === val) {
        showMsg("ok", "✔ تم الحفظ: " + val.slice(0, 10) + "…");
      } else {
        showMsg("err", "❌ الحفظ فشل — حاول مرة أخرى");
      }
    });
  });
}

// ── Clear key ─────────────────────────────────────────────
function clearKey() {
  chrome.storage.local.remove("newsdata_api_key", () => {
    document.getElementById("key").value = "";
    showMsg("info", "ℹ️ تم حذف المفتاح");
  });
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