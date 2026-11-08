/* frontend/checklist-editor.js: редактор чек-листа и применение к диалогам.
   Подключается в index.html ПОСЛЕ основного скрипта: <script src="checklist-editor.js"></script>
   Из index.html использует: API, api(), escapeHtml(), selectedId, checklistData,
   openConversation(), renderChecklist(), calculateScore(). */
(function () {
  let draft = null;        // редактируемая копия чек-листа
  let origIds = new Set(); // id критериев, которые уже есть на сервере (их id менять нельзя)
  let dirty = false;
  let pollTimer = null;
  let polling = false;

  const h = v => escapeHtml(v);
  const TYPES = [["yes_no", "Да / Нет"], ["scale", "Шкала"], ["flag", "Флаг (грубые ошибки)"]];

  // ---------- стили ----------
  const style = document.createElement("style");
  style.textContent = `
  #checklistPanel{position:fixed;inset:0;background:#f4f7f5;z-index:1001;display:none;flex-direction:column}
  #checklistPanel .ce-head{display:flex;justify-content:space-between;align-items:center;padding:16px 22px;background:#153c2d;color:#fff}
  #ceBody{flex:1;overflow:auto;padding:20px 22px}
  #ceFoot{background:#fff;border-top:1px solid #e5ebe7;padding:12px 22px}
  .ce-card{background:#fff;border:1px solid #e5ebe7;border-radius:12px;padding:14px;margin-bottom:12px}
  .ce-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:8px}
  .ce-row label{flex:1;min-width:140px;font-size:11px;color:#75847c;font-weight:bold}
  .ce-row label input,.ce-row label select,.ce-row label textarea,.ce-step input{width:100%;margin-top:4px;border:1px solid #e5ebe7;border-radius:8px;padding:8px;font-size:13px;font-weight:normal;color:#263a31;background:#fff}
  .ce-row label.ce-chk{display:flex;align-items:center;gap:6px;flex:0 0 auto;min-width:0;align-self:flex-end;padding-bottom:8px}
  .ce-row label.ce-chk input{width:auto;margin:0}
  .ce-step{display:flex;gap:8px;margin-bottom:6px}
  .ce-step input[type=number]{width:90px;flex:0 0 90px}
  .ce-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
  .ce-warn{background:#fff5dc;color:#946200;border-radius:8px;padding:10px;font-size:12px;margin-bottom:12px}
  .ce-warn.ok{background:#e2f5e9;color:#16804b}
  .ce-err{background:#fff0ef;color:#b42318;border-radius:8px;padding:10px;font-size:12px;margin-bottom:12px}
  .ce-foot-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .ce-foot-row input{flex:1;min-width:220px;border:1px solid #e5ebe7;border-radius:8px;padding:9px;font-size:13px}
  #ceStatus{font-size:12px;margin-top:8px;color:#263a31;line-height:1.5}
  `;
  document.head.appendChild(style);

  async function request(method, path, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    const r = await fetch(API + path, opt);
    const raw = await r.text();
    let data = {};
    try { data = raw ? JSON.parse(raw) : {}; } catch (e) { data = { detail: raw.slice(0, 200) }; }
    if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Ошибка сервера " + r.status);
    return data;
  }

  // ---------- панель ----------
  function setup() {
    const navs = Array.from(document.querySelectorAll(".sidebar .nav"));
    let nav = navs.find(n => n.textContent.includes("Чек-лист"));
    if (!nav) {
      nav = document.createElement("div");
      nav.className = "nav";
      nav.textContent = "✓ Чек-лист";
      (document.querySelector(".sidebar") || document.body).appendChild(nav);
    }
    nav.style.cursor = "pointer";
    nav.addEventListener("click", openPanel);

    const panel = document.createElement("div");
    panel.id = "checklistPanel";
    panel.innerHTML = `
      <div class="ce-head"><b>Редактор чек-листа</b>
        <button class="ap-btn" id="ceClose" type="button">Закрыть</button></div>
      <div id="ceBody"></div>
      <div id="ceFoot">
        <div class="ce-foot-row">
          <button class="ap-btn" id="ceSave" type="button">Сохранить</button>
          <input id="ceIds" placeholder="ID диалогов через запятую (пусто = все диалоги)">
          <button class="ap-btn primary" id="ceApply" type="button">Применить к диалогам</button>
        </div>
        <div id="ceStatus"></div>
      </div>`;
    document.body.appendChild(panel);
    panel.querySelector("#ceClose").addEventListener("click", () => { panel.style.display = "none"; });
    panel.querySelector("#ceSave").addEventListener("click", () => save());
    panel.querySelector("#ceApply").addEventListener("click", applyAll);

    const body = panel.querySelector("#ceBody");
    body.addEventListener("input", onInput);
    body.addEventListener("change", onChange);
    body.addEventListener("click", onClick);
  }

  async function openPanel() {
    document.getElementById("checklistPanel").style.display = "flex";
    setStatus("");
    try {
      if (!dirty || !draft) await loadDraft();
      else render();
    } catch (e) {
      document.getElementById("ceBody").innerHTML = `<div class="ce-err">Не удалось загрузить чек-лист: ${h(e.message)}</div>`;
      return;
    }
    // если оценка уже идёт (например, страницу перезагрузили), показываем прогресс
    try {
      const s = await request("GET", "/evaluate-status");
      if (s.running) startPolling();
    } catch (e) { /* не критично */ }
  }

  async function loadDraft() {
    const data = await request("GET", "/checklist");
    draft = JSON.parse(JSON.stringify(data.data || data));
    draft.criteria = draft.criteria || [];
    draft.criteria.forEach(c => {
      if (c.type === "scale" && !Array.isArray(c.scale)) c.scale = [];
    });
    origIds = new Set(draft.criteria.map(c => c.id));
    dirty = false;
    render();
  }

  function setStatus(text, isError) {
    const el = document.getElementById("ceStatus");
    el.style.color = isError ? "#b42318" : "#263a31";
    el.textContent = text;
  }

  function weightSum() {
    return draft.criteria.reduce((s, c) => s + (c.type === "flag" ? 0 : (Number(c.weight) || 0)), 0);
  }

  function updateSum() {
    const el = document.getElementById("ceSum");
    if (!el) return;
    const sum = weightSum();
    el.className = "ce-warn" + (sum === 100 ? " ok" : "");
    el.innerHTML = `Сумма весов критериев: <b>${sum}</b> ${sum === 100 ? "✓" : "(рекомендуется 100)"}`;
  }

  // ---------- отрисовка ----------
  function criterionHtml(c, i) {
    const sections = draft.sections || [];
    const known = sections.some(s => s.id === c.section);
    const secOpts = sections.map(s =>
      `<option value="${h(s.id)}" ${s.id === c.section ? "selected" : ""}>${h(s.name || s.title || s.id)}</option>`).join("")
      + (known || !c.section ? "" : `<option value="${h(c.section)}" selected>${h(c.section)}</option>`);
    const typeOpts = TYPES.map(([v, l]) =>
      `<option value="${v}" ${c.type === v ? "selected" : ""}>${l}</option>`).join("");
    const isFlag = c.type === "flag";

    let cond = "";
    if (c.type !== "scale") {
      cond += `<div class="ce-row"><label>Условие «Да»
        <textarea rows="2" data-f="yes_condition">${h(c.yes_condition || "")}</textarea></label></div>`;
    }
    if (c.allow_na && !isFlag) {
      cond += `<div class="ce-row"><label>Условие N/A
        <textarea rows="2" data-f="na_condition">${h(c.na_condition || "")}</textarea></label></div>`;
    }

    let scale = "";
    if (c.type === "scale") {
      scale = `<div style="font-size:11px;color:#75847c;font-weight:bold;margin:4px 0 6px">Ступени шкалы (значение и описание)</div>`
        + (c.scale || []).map((s, j) => `
        <div class="ce-step">
          <input type="number" data-s="${j}" data-sf="value" value="${h(s.value ?? "")}">
          <input data-s="${j}" data-sf="label" value="${h(s.label || "")}">
          <button class="ap-btn warn" type="button" data-act="delstep" data-j="${j}">✕</button>
        </div>`).join("")
        + `<button class="ap-btn" type="button" data-act="addstep">+ Ступень</button>`;
    }

    return `<div class="ce-card" data-i="${i}">
      <div class="ce-row">
        <label>ID<input data-f="id" value="${h(c.id || "")}" ${origIds.has(c.id) ? "readonly" : ""}></label>
        <label style="flex:2">Название<input data-f="name" value="${h(c.name || "")}"></label>
      </div>
      <div class="ce-row">
        <label>Секция<select data-f="section">${secOpts}</select></label>
        <label>Тип<select data-f="type">${typeOpts}</select></label>
        <label>Вес<input type="number" min="0" step="any" data-f="weight" value="${h(isFlag ? 0 : (c.weight ?? 0))}" ${isFlag ? "disabled" : ""}></label>
        <label class="ce-chk"><input type="checkbox" data-f="allow_na" ${c.allow_na && !isFlag ? "checked" : ""} ${isFlag ? "disabled" : ""}> N/A разрешён</label>
      </div>
      ${cond}${scale}
      <div class="ce-actions">
        <button class="ap-btn" type="button" data-act="up">↑</button>
        <button class="ap-btn" type="button" data-act="down">↓</button>
        <button class="ap-btn warn" type="button" data-act="del">Удалить критерий</button>
      </div>
    </div>`;
  }

  function render() {
    const body = document.getElementById("ceBody");
    body.innerHTML = `<div id="ceSum" class="ce-warn"></div>
      ${draft.criteria.map(criterionHtml).join("")}
      <button class="ap-btn primary" type="button" data-act="add">+ Добавить критерий</button>`;
    updateSum();
  }

  // ---------- обработчики ----------
  function cardOf(el) {
    const card = el.closest(".ce-card");
    return card ? draft.criteria[Number(card.dataset.i)] : null;
  }

  function markDirty() { dirty = true; }

  function onInput(e) {
    const el = e.target;
    if (el.tagName === "SELECT" || el.type === "checkbox") return;
    const c = cardOf(el);
    if (!c) return;
    if (el.dataset.sf) {
      const st = c.scale[Number(el.dataset.s)];
      st[el.dataset.sf] = el.dataset.sf === "value" ? (el.value === "" ? "" : Number(el.value)) : el.value;
    } else if (el.dataset.f === "weight") {
      c.weight = el.value === "" ? 0 : Number(el.value);
      updateSum();
    } else if (el.dataset.f) {
      c[el.dataset.f] = el.value;
    }
    markDirty();
  }

  function onChange(e) {
    const el = e.target;
    const c = cardOf(el);
    if (!c) return;
    const f = el.dataset.f;
    if (f === "section") {
      c.section = el.value;
    } else if (f === "type") {
      c.type = el.value;
      if (c.type === "scale" && (!Array.isArray(c.scale) || !c.scale.length)) {
        c.scale = [{ value: 10, label: "" }, { value: 0, label: "" }];
      }
      if (c.type === "flag") { c.weight = 0; c.allow_na = false; }
      render();
    } else if (f === "allow_na") {
      c.allow_na = el.checked;
      render();
    } else {
      return;
    }
    markDirty();
  }

  function onClick(e) {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const act = btn.dataset.act;
    const card = btn.closest(".ce-card");
    const i = card ? Number(card.dataset.i) : -1;
    const list = draft.criteria;
    if (act === "add") {
      const sec = (draft.sections && draft.sections[0] && draft.sections[0].id) || "";
      list.push({ id: "criterion_" + (list.length + 1), section: sec, name: "Новый критерий",
        type: "yes_no", weight: 0, allow_na: false, yes_condition: "", na_condition: null });
    } else if (act === "del") {
      if (!confirm("Удалить критерий «" + (list[i].name || list[i].id) + "»?")) return;
      list.splice(i, 1);
    } else if (act === "up" && i > 0) {
      [list[i - 1], list[i]] = [list[i], list[i - 1]];
    } else if (act === "down" && i >= 0 && i < list.length - 1) {
      [list[i + 1], list[i]] = [list[i], list[i + 1]];
    } else if (act === "addstep") {
      list[i].scale.push({ value: "", label: "" });
    } else if (act === "delstep") {
      list[i].scale.splice(Number(btn.dataset.j), 1);
    } else {
      return;
    }
    markDirty();
    render();
  }

  // ---------- сохранение ----------
  function buildPayload() {
    const ids = new Set();
    const criteria = draft.criteria.map((c, i) => {
      const id = String(c.id || "").trim();
      const name = String(c.name || "").trim();
      if (!id || !name) throw new Error("У каждого критерия должны быть ID и название (критерий №" + (i + 1) + ").");
      if (ids.has(id)) throw new Error("ID «" + id + "» повторяется.");
      ids.add(id);
      const flag = c.type === "flag";
      const o = {
        id, order: i + 1, section: c.section, name, type: c.type,
        weight: flag ? 0 : (Number(c.weight) || 0),
        allow_na: flag ? false : !!c.allow_na
      };
      if (c.type !== "scale") o.yes_condition = String(c.yes_condition || "").trim() || null;
      o.na_condition = (o.allow_na && String(c.na_condition || "").trim()) || null;
      if (c.type === "scale") {
        if (!c.scale.length) throw new Error("У критерия «" + name + "» нет ступеней шкалы.");
        o.scale = c.scale.map(s => {
          if (s.value === "" || s.value === null || !Number.isFinite(Number(s.value))) {
            throw new Error("У критерия «" + name + "» у ступени шкалы не указано число.");
          }
          return { value: Number(s.value), label: String(s.label || "") };
        });
      }
      return o;
    });
    return { ...draft, criteria };
  }

  async function save() {
    let payload;
    try { payload = buildPayload(); } catch (e) { setStatus(e.message, true); return false; }
    const btn = document.getElementById("ceSave");
    btn.disabled = true;
    try {
      const res = await request("PUT", "/checklist", payload);
      const fresh = await request("GET", "/checklist");
      checklistData = fresh.data || fresh;
      await loadDraft();
      renderChecklist();
      calculateScore();
      setStatus("Сохранено. Версия чек-листа: " + res.version + ". Чтобы пересчитать оценки, нажмите «Применить к диалогам».");
      return true;
    } catch (e) {
      setStatus("Ошибка сохранения: " + e.message, true);
      return false;
    } finally {
      btn.disabled = false;
    }
  }

  // ---------- применение к диалогам ----------
  async function applyAll() {
    if (dirty && !(await save())) return;
    const ids = document.getElementById("ceIds").value.split(/[\s,;]+/).filter(Boolean);
    const btn = document.getElementById("ceApply");
    btn.disabled = true;
    try {
      const res = await request("POST", "/checklist/apply", ids.length ? { ids } : {});
      setStatus("Запущена оценка диалогов: " + res.started + " (версия чек-листа " + res.checklist_version + ").");
      startPolling();
    } catch (e) {
      setStatus("Ошибка: " + e.message, true);
      btn.disabled = false;
    }
  }

  function startPolling() {
    document.getElementById("ceApply").disabled = true;
    clearInterval(pollTimer);
    pollTimer = setInterval(tick, 2000);
    tick();
  }

  async function tick() {
    if (polling) return;
    polling = true;
    try {
      const s = await request("GET", "/evaluate-status");
      const errs = s.failed || [];
      let text = "Оценено " + s.done + " из " + s.total + (errs.length ? ", ошибок: " + errs.length : "");
      if (!s.running) {
        clearInterval(pollTimer);
        pollTimer = null;
        document.getElementById("ceApply").disabled = false;
        text = "Готово. " + text + ".";
        if (errs.length) {
          text += " " + errs.slice(0, 3).map(x => x.id + ": " + x.error).join("; ");
        }
        setStatus(text, errs.length > 0);
        if (typeof selectedId !== "undefined" && selectedId) await openConversation(selectedId);
      } else {
        setStatus(text, false);
      }
    } catch (e) {
      setStatus("Не удалось получить статус: " + e.message, true);
    } finally {
      polling = false;
    }
  }

  setup();
})();