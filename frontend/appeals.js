/* frontend/appeals.js: апелляции и экран проверяющего.
   Подключается в index.html ПОСЛЕ основного скрипта: <script src="appeals.js"></script>
   Из index.html использует: API, api(), escapeHtml(), selectedId, evals, checklistData,
   openConversation(), renderChecklist(). */
(function () {
  let appeals = [];        // все апелляции (экран проверяющего)
  let convAppeals = [];    // апелляции выбранного диалога
  let selectedAppeal = null;

  const h = v => escapeHtml(v);
  const label = v => v === "yes" ? "Да" : v === "no" ? "Нет" : v === "na" ? "N/A" : String(v);
  const fmt = n => (n === null || n === undefined) ? "—" : Number(n).toFixed(2);
  const STATUS = { open: "открыта", confirmed: "подтверждена", changed: "изменена" };

  // ---------- стили (чтобы не трогать index.html) ----------
  const style = document.createElement("style");
  style.textContent = `
  .appeal-box{margin-top:9px}
  .ap-btn{border:1px solid #20a66a;background:#fff;color:#16804b;border-radius:8px;padding:7px 12px;font-size:12px;font-weight:bold;cursor:pointer}
  .ap-btn.primary{background:#20a66a;color:#fff}
  .ap-btn.warn{border-color:#b42318;color:#b42318}
  .ap-btn:disabled{opacity:.6;cursor:wait}
  .ap-chip{display:inline-block;margin-top:8px;padding:4px 8px;border-radius:12px;font-size:11px;background:#fff5dc;color:#946200}
  .ap-chip.done{background:#e2f5e9;color:#16804b}
  .ap-msg{font-size:11px;color:#b42318;margin-top:6px}
  .ap-actions{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
  .ap-card{background:#f5faf7;border:1px solid #dcebe1;border-radius:12px;padding:13px;margin:0 0 15px;font-size:12px;line-height:1.5}
  .ap-title{font-size:11px;color:#75847c;font-weight:bold;margin-bottom:8px}
  .ap-row{display:flex;justify-content:space-between;gap:10px;margin:4px 0}
  .ap-sub{font-size:11px;color:#75847c;margin-top:6px}
  #reviewerPanel{position:fixed;inset:0;background:#f4f7f5;z-index:1000;display:none;flex-direction:column}
  #reviewerPanel .ap-head{display:flex;justify-content:space-between;align-items:center;padding:16px 22px;background:#153c2d;color:#fff}
  #reviewerPanel .ap-body{display:grid;grid-template-columns:340px 1fr;flex:1;min-height:0}
  #apList{background:#fff;border-right:1px solid #e5ebe7;overflow:auto}
  #apDetail{padding:22px;overflow:auto}
  .ap-item{padding:12px 14px;border-bottom:1px solid #edf1ee;cursor:pointer;font-size:12px}
  .ap-item:hover,.ap-item.sel{background:#edf8f1}
  .ap-frag{background:#fff;border:1px solid #e5ebe7;border-radius:10px;padding:10px;margin:8px 0 14px}
  .ap-frag div{padding:5px 6px;border-radius:6px;font-size:12px;line-height:1.45}
  .ap-frag div.hit{background:#fff3d8}
  #apDetail textarea,#apDetail select{width:100%;border:1px solid #e5ebe7;border-radius:8px;padding:9px;font-size:13px;margin:6px 0}
  .ap-err{color:#b42318;background:#fff0ef;padding:10px;border-radius:8px;font-size:12px;margin:10px}
  .nav.ap-nav{cursor:pointer}
  `;
  document.head.appendChild(style);

  async function post(path, body) {
    const r = await fetch(API + path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    });
    const raw = await r.text();
    let data = {};
    try { data = raw ? JSON.parse(raw) : {}; } catch (e) { data = { detail: raw.slice(0, 200) }; }
    if (!r.ok) throw new Error(data.detail || ("Ошибка сервера " + r.status));
    return data;
  }

  // ---------- 1. Кнопка «Оспорить» у критерия ----------
  function addAppealButtons() {
    if (!selectedId || !evals[selectedId]) return;
    document.querySelectorAll("#checklist .criterion").forEach(box => {
      const sel = box.querySelector("[data-criterion]");
      if (!sel || box.querySelector(".appeal-box")) return;
      const cid = sel.dataset.criterion;
      if (!evals[selectedId][cid]) return;

      const mine = convAppeals.filter(a => a.criterion_id === cid);
      const wrap = document.createElement("div");
      wrap.className = "appeal-box";
      const chip = mine.length
        ? `<div class="ap-chip ${mine[0].status === "open" ? "" : "done"}">Апелляция №${mine[0].id}: ${STATUS[mine[0].status] || h(mine[0].status)}</div>` : "";
      wrap.innerHTML = `<button class="ap-btn" type="button">Оспорить</button>${chip}`;
      wrap.querySelector("button").addEventListener("click", () => openAppealForm(wrap, cid));
      box.appendChild(wrap);
    });
  }

  function openAppealForm(wrap, cid) {
    wrap.innerHTML = `
      <textarea rows="3" placeholder="Почему вы не согласны с оценкой?" style="width:100%;border:1px solid #e5ebe7;border-radius:8px;padding:9px;font-size:12px"></textarea>
      <div class="ap-actions">
        <button class="ap-btn primary ap-send" type="button">Отправить апелляцию</button>
        <button class="ap-btn ap-cancel" type="button">Отмена</button>
      </div>
      <div class="ap-msg"></div>`;
    const msg = wrap.querySelector(".ap-msg");
    wrap.querySelector(".ap-cancel").addEventListener("click", () => renderChecklist());
    wrap.querySelector(".ap-send").addEventListener("click", async () => {
      const comment = wrap.querySelector("textarea").value.trim();
      if (!comment) { msg.textContent = "Напишите комментарий."; return; }
      const btn = wrap.querySelector(".ap-send");
      btn.disabled = true;
      try {
        await post("/appeals", { conversation_id: selectedId, criterion_id: cid, comment });
        await refreshConvAppeals();
      } catch (e) {
        msg.textContent = e.message;
        btn.disabled = false;
      }
    });
  }

  // ---------- 2. Блок «исходная оценка / решение / финал» ----------
  async function refreshConvAppeals() {
    const id = selectedId;
    if (!id) { convAppeals = []; renderSummary(); return; }
    try {
      const list = await api("/appeals?conversation_id=" + encodeURIComponent(id));
      if (selectedId !== id) return;
      convAppeals = list;
    } catch (e) { convAppeals = []; }
    renderSummary();
    renderChecklist();
  }

  function renderSummary() {
    let box = document.getElementById("appealSummary");
    if (!box) {
      const anchor = document.getElementById("scoreSummary");
      if (!anchor) return;
      box = document.createElement("div");
      box.id = "appealSummary";
      anchor.insertAdjacentElement("afterend", box);
    }
    if (!convAppeals.length) { box.innerHTML = ""; return; }
    const decided = convAppeals.filter(a => a.status !== "open");
    const opened = convAppeals.length - decided.length;
    box.innerHTML = `<div class="ap-card">
      <div class="ap-title">АПЕЛЛЯЦИИ ПО ДИАЛОГУ</div>
      <div class="ap-row"><span>Оценка ИИ (исходная)</span><b>${fmt(convAppeals[0].original_total)}</b></div>
      <div class="ap-row"><span>Финальный результат</span><b>${fmt(convAppeals[0].final_total)}</b></div>
      ${decided.map(a => `<div class="ap-sub">№${a.id} · ${h(a.criterion_name)}: ${STATUS[a.status]}${a.status === "changed" ? ` (${label(a.ai_answer)} → ${label(a.new_value)})` : ""}. ${h(a.reason || "")}</div>`).join("")}
      ${opened ? `<div class="ap-sub">Ожидают решения: ${opened}</div>` : ""}
    </div>`;
  }

  // ---------- 3. Экран проверяющего ----------
  function setupReviewer() {
    const labels = document.querySelectorAll(".sidebar .side-label");
    const nav = document.createElement("div");
    nav.className = "nav ap-nav";
    nav.textContent = "⚖ Проверяющий";
    nav.addEventListener("click", openReviewer);
    if (labels[1]) labels[1].parentNode.insertBefore(nav, labels[1]);
    else (document.querySelector(".sidebar") || document.body).appendChild(nav);

    const panel = document.createElement("div");
    panel.id = "reviewerPanel";
    panel.innerHTML = `
      <div class="ap-head"><b>Проверяющий: апелляции</b>
        <button class="ap-btn" id="apClose" type="button">Закрыть</button></div>
      <div class="ap-body"><div id="apList"></div><div id="apDetail"></div></div>`;
    document.body.appendChild(panel);
    panel.querySelector("#apClose").addEventListener("click", () => { panel.style.display = "none"; });
  }

  async function openReviewer() {
    document.getElementById("reviewerPanel").style.display = "flex";
    await loadAppeals();
  }

  async function loadAppeals(selectId) {
    try {
      appeals = await api("/appeals");
    } catch (e) {
      document.getElementById("apList").innerHTML = `<div class="ap-err">Не удалось загрузить апелляции: ${h(e.message)}</div>`;
      return;
    }
    appeals.sort((a, b) => (a.status === "open" ? 0 : 1) - (b.status === "open" ? 0 : 1) || b.id - a.id);
    selectedAppeal = appeals.find(a => a.id === selectId) || null;
    renderList();
    renderDetail();
  }

  function renderList() {
    document.getElementById("apList").innerHTML = appeals.length ? appeals.map(a => `
      <div class="ap-item ${selectedAppeal && selectedAppeal.id === a.id ? "sel" : ""}" data-id="${a.id}">
        <b>№${a.id} · ${h(a.conversation_id)}</b><br>
        ${h(a.criterion_name)}<br>
        <span class="ap-chip ${a.status === "open" ? "" : "done"}">${STATUS[a.status] || h(a.status)}</span>
      </div>`).join("") : '<div class="empty" style="padding:30px;color:#82918a;font-size:13px">Апелляций пока нет</div>';
    document.querySelectorAll("#apList .ap-item").forEach(el => el.addEventListener("click", () => {
      selectedAppeal = appeals.find(a => a.id === Number(el.dataset.id));
      renderList();
      renderDetail();
    }));
  }

  function valueOptions(cid) {
    const c = (checklistData?.criteria || []).find(x => x.id === cid);
    if (!c) return [];
    let opts;
    if (c.type === "scale") opts = (c.scale || []).map(s => [String(s.value), s.value + ": " + s.label]);
    else if (c.type === "flag") opts = [["yes", "Да (нарушение найдено)"], ["no", "Нет (нарушения нет)"]];
    else opts = [["yes", "Да"], ["no", "Нет"]];
    if (c.allow_na) opts.push(["na", "N/A (неприменимо)"]);
    return opts;
  }

  function renderDetail() {
    const box = document.getElementById("apDetail");
    const a = selectedAppeal;
    if (!a) { box.innerHTML = '<div class="empty" style="color:#82918a;font-size:13px">Выберите апелляцию слева</div>'; return; }
    const hit = new Set((a.evidence || []).map(e => e.message_index));
    const fragment = (a.fragment || []).map(m =>
      `<div class="${hit.has(m.index) ? "hit" : ""}"><b>[${m.index}] ${h(m.speaker)} · ${h(m.timestamp)}</b>: ${h(m.text)}</div>`).join("");

    let tail;
    if (a.status === "open") {
      const opts = valueOptions(a.criterion_id).map(([v, l]) =>
        `<option value="${h(v)}" ${String(a.current_answer) === v ? "selected" : ""}>${h(l)}</option>`).join("");
      tail = `
        <label><b>Причина решения *</b></label>
        <textarea id="apReason" rows="3" placeholder="Объясните, почему оценка подтверждена или изменена"></textarea>
        <label><b>Новое значение (если меняете)</b></label>
        <select id="apNew">${opts}</select>
        <div class="ap-actions">
          <button class="ap-btn primary" id="apConfirm" type="button">Подтвердить оценку ИИ</button>
          <button class="ap-btn warn" id="apChange" type="button">Изменить оценку</button>
        </div>
        <div class="ap-msg" id="apMsg"></div>`;
    } else {
      tail = `<div class="ap-card">
        <div class="ap-title">РЕШЕНИЕ: ${STATUS[a.status].toUpperCase()}</div>
        <div class="ap-row"><span>Оценка ИИ (исходная)</span><b>${fmt(a.original_total)}</b></div>
        <div class="ap-row"><span>Финальный результат</span><b>${fmt(a.final_total)}</b></div>
        <div class="ap-sub">${a.status === "changed" ? `Критерий: ${label(a.ai_answer)} → ${label(a.new_value)}. ` : ""}Причина: ${h(a.reason || "")}</div>
      </div>`;
    }

    box.innerHTML = `
      <h2 style="margin:0 0 4px">Апелляция №${a.id} · ${h(a.conversation_id)}</h2>
      <div class="ap-chip ${a.status === "open" ? "" : "done"}">${STATUS[a.status] || h(a.status)}</div>
      <p><b>Критерий:</b> ${h(a.criterion_name)}<br>
         <b>Ответ ИИ:</b> ${label(a.ai_answer)} · <b>Сейчас:</b> ${label(a.current_answer)}<br>
         <b>Объяснение ИИ:</b> ${h(a.explanation)}</p>
      <p><b>Комментарий:</b> ${h(a.comment)}</p>
      <b>Фрагмент диалога</b>
      <div class="ap-frag">${fragment || "—"}</div>
      ${tail}`;

    if (a.status === "open") {
      document.getElementById("apConfirm").addEventListener("click", () => decide("confirm"));
      document.getElementById("apChange").addEventListener("click", () => decide("change"));
    }
  }

  async function decide(kind) {
    const a = selectedAppeal;
    if (!a) return;
    const msg = document.getElementById("apMsg");
    const reason = document.getElementById("apReason").value.trim();
    if (!reason) { msg.textContent = "Укажите причину решения."; return; }
    const body = { decision: kind, reason };
    if (kind === "change") body.new_value = document.getElementById("apNew").value;
    try {
      await post("/appeals/" + a.id + "/decision", body);
    } catch (e) { msg.textContent = e.message; return; }
    await loadAppeals(a.id);
    if (selectedId && a.conversation_id === selectedId) await openConversation(selectedId);
  }

  // ---------- подключение к основному скрипту ----------
  const origRender = window.renderChecklist;
  window.renderChecklist = function () { origRender(); addAppealButtons(); };
  const origOpen = window.openConversation;
  window.openConversation = async function (id) { await origOpen(id); await refreshConvAppeals(); };

  setupReviewer();
})();