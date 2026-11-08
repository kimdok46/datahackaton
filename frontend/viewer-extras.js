/* frontend/viewer-extras.js: плеер для звонков, время от начала звонка, итоги по секциям.
   Подключается в index.html ПОСЛЕ appeals.js и checklist-editor.js.
   Из index.html использует: API, api(), escapeHtml(), selectedId, checklistData,
   currentAnswers(), openConversation(), calculateScore(). */
(function () {
  const ISO = /^\d{4}-\d{2}-\d{2}T/;

  function minutes(ts, first) {
    if (!ISO.test(String(ts)) || !ISO.test(String(first))) return null;
    const t = x => Date.parse(String(x).replace(/(\.\d{3})\d+/, "$1"));
    const sec = Math.round((t(ts) - t(first)) / 1000);
    if (!Number.isFinite(sec)) return null;
    const s = Math.max(0, sec);
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  // ---------- плеер и время ----------
  async function decorateConversation(id) {
    let conv;
    try { conv = await api("/conversations/" + encodeURIComponent(id)); } catch (e) { return; }
    if (selectedId !== id) return;
    const body = document.getElementById("chatBody");
    if (!body) return;
    const msgs = conv.transcript || [];

    if (conv.audio_url && !body.querySelector("audio")) {
      const audio = document.createElement("audio");
      audio.controls = true;
      audio.preload = "none";
      audio.style.cssText = "width:100%;margin-bottom:12px";
      audio.src = "/api/audio/" + encodeURIComponent(id);
      body.insertBefore(audio, body.firstChild);
    }

    if (msgs.length && ISO.test(String(msgs[0].timestamp))) {
      msgs.forEach((m, i) => {
        const head = document.querySelector("#msg-" + i + " .msg-head");
        const t = minutes(m.timestamp, msgs[0].timestamp);
        if (head && t) head.textContent = (m.speaker || "") + " · " + t;
      });
    }
  }

  const origOpen = window.openConversation;
  window.openConversation = async function (id) {
    await origOpen(id);
    await decorateConversation(id);
  };

  // ---------- итоги по секциям ----------
  function sectionsHtml() {
    if (!selectedId || !checklistData) return "";
    const a = currentAnswers();
    const sec = {};
    (checklistData.criteria || []).forEach(c => {
      const type = c.type || c.answer_type || "yes_no";
      const ans = a[c.id];
      if (type === "flag" || ans === undefined || ans === "" || ans === "na") return;
      const weight = Number(c.weight || 0);
      let pts = 0;
      if (type === "yes_no") {
        pts = ans === "yes" ? weight : 0;
      } else if (type === "scale") {
        const vals = (c.scale || c.values || []).map(v =>
          Number(typeof v === "object" ? (v.value ?? v.score) : v)).filter(Number.isFinite);
        const max = Math.max(0, ...vals);
        const sel = Number(ans);
        pts = max > 0 && Number.isFinite(sel) ? (sel / max) * weight : 0;
      }
      const key = c.section || c.section_id || "";
      const s = sec[key] || (sec[key] = { earned: 0, max: 0 });
      s.earned += pts;
      s.max += weight;
    });
    const keys = Object.keys(sec);
    if (!keys.length) return "";
    const names = Object.fromEntries(
      (checklistData.sections || []).map(x => [x.id, x.title || x.name || x.id]));
    return '<div class="score-caption" style="margin-top:10px">ПО СЕКЦИЯМ</div>' +
      keys.map(k =>
        `<div class="score-caption" style="display:flex;justify-content:space-between;gap:8px"><span>${escapeHtml(names[k] || k)}</span><b>${sec[k].earned.toFixed(2)} / ${sec[k].max.toFixed(2)}</b></div>`
      ).join("");
  }

  const origCalc = window.calculateScore;
  window.calculateScore = function () {
    origCalc();
    const box = document.getElementById("scoreSummary");
    if (box) box.insertAdjacentHTML("beforeend", sectionsHtml());
  };
})();