import json
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

import time

from backend import ai, db, scoring

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI()


@app.on_event("startup")
def startup():
    db.init_db()
    # Первый запуск: загружаем чек-лист и диалоги из файлов репозитория
    if db.get_checklist() is None:
        data = json.loads((ROOT / "data" / "checklist.json").read_text(encoding="utf-8"))
        db.save_checklist(data)
    if db.count_conversations() == 0:
        data = json.loads((ROOT / "data" / "dev.json").read_text(encoding="utf-8"))
        db.import_records(data["data"]["records"])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/import")
def import_dataset(group_id: str = "DEV"):
    # Пока читаем локальный файл для DEV. Загрузку по ссылке организаторов добавим позже.
    if group_id != "DEV":
        raise HTTPException(400, "Загрузка по ссылке организаторов ещё не подключена")
    data = json.loads((ROOT / "data" / "dev.json").read_text(encoding="utf-8"))
    return {"imported": db.import_records(data["data"]["records"])}


@app.get("/api/conversations")
def conversations():
    return db.list_conversations()


@app.get("/api/conversations/{conv_id}")
def conversation(conv_id: str):
    conv = db.get_conversation(conv_id)
    if conv is None:
        raise HTTPException(404, "Диалог не найден")
    return {
        "id": conv["conversation_id"],
        "language": conv["language"],
        "channel": conv["channel"],
        "audio_url": conv.get("audio_url"),
        "transcript": conv["transcript"],
        "evaluation": db.get_evaluation(conv_id),
    }


@app.get("/api/checklist")
def get_checklist():
    return db.get_checklist()


@app.put("/api/checklist")
def put_checklist(data: dict = Body(...)):
    if not isinstance(data.get("criteria"), list) or not data["criteria"]:
        raise HTTPException(400, "В чек-листе должен быть список criteria")
    for c in data["criteria"]:
        if not c.get("id") or not c.get("name"):
            raise HTTPException(400, "У каждого критерия должны быть id и name")
        if c.get("weight", 0) < 0:
            raise HTTPException(400, f"Вес критерия {c['id']} не может быть отрицательным")
    return {"version": db.save_checklist(data)}

def evaluate_and_save(conv_id):
    conv = db.get_conversation(conv_id)
    if conv is None:
        raise HTTPException(404, "Диалог не найден")
    checklist = db.get_checklist()
    ai_result = ai.evaluate(conv, checklist)
    calc = scoring.compute_total(checklist, ai_result["answers"])
    final = {"criteria": ai_result["criteria"], **calc}
    db.save_evaluation(conv_id, checklist["version"], ai_result, final,
                       calc["total"], calc["gross_error"])
    return db.get_evaluation(conv_id)


@app.post("/api/conversations/{conv_id}/evaluate")
def evaluate_one(conv_id: str):
    try:
        return evaluate_and_save(conv_id)
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.post("/api/evaluate-all")
def evaluate_all():
    ok, failed = 0, []
    for c in db.list_conversations():
        try:
            evaluate_and_save(c["id"])
            ok += 1
        except Exception as e:
            failed.append({"id": c["id"], "error": str(e)})
        time.sleep(1)  # пауза, чтобы не упереться в лимит запросов в минуту
    return {"evaluated": ok, "failed": failed}
    
# Раздача страницы. Эта строка должна быть ПОСЛЕДНЕЙ в файле.

def _criterion(checklist, cid):
    return next((c for c in checklist["criteria"] if c["id"] == cid), None)


def _fragment(conv, evidence, pad=2):
    """Реплики вокруг доказательства, чтобы проверяющий видел контекст."""
    n = len(conv["transcript"])
    idxs = [e["message_index"] for e in evidence] or [0]
    lo, hi = max(0, min(idxs) - pad), min(n - 1, max(idxs) + pad)
    return [{"index": i, **conv["transcript"][i]} for i in range(lo, hi + 1)]


def _check_value(c, v):
    """Проверяет новое значение критерия при решении проверяющего."""
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("yes", "no", "na"):
            v = s
        else:
            try:
                v = int(float(s))
            except ValueError:
                raise HTTPException(400, "Некорректное значение")
    if v == "na":
        if not c.get("allow_na"):
            raise HTTPException(400, "Для этого критерия N/A не разрешён")
    elif c["type"] in ("yes_no", "flag"):
        if v not in ("yes", "no"):
            raise HTTPException(400, 'Значение должно быть "yes", "no" или "na"')
    elif c["type"] == "scale":
        steps = [s["value"] for s in c["scale"]]
        if v not in steps:
            raise HTTPException(400, f"Значение должно быть одним из {steps}")
    return v


def _appeal_view(a):
    conv = db.get_conversation(a["conversation_id"])
    ev = db.get_evaluation(a["conversation_id"])
    checklist = db.get_checklist_version(ev["checklist_version"]) or db.get_checklist()
    crit = _criterion(checklist, a["criterion_id"])
    ai_c = next(c for c in ev["ai"]["criteria"] if c["criterion_id"] == a["criterion_id"])
    cur_c = next(c for c in ev["final"]["criteria"] if c["criterion_id"] == a["criterion_id"])
    original_total = scoring.compute_total(checklist, ev["ai"]["answers"])["total"]
    return {
        **a,
        "criterion_name": crit["name"] if crit else a["criterion_id"],
        "ai_answer": ai_c["answer"],
        "current_answer": cur_c["answer"],
        "explanation": ai_c["explanation"],
        "evidence": ai_c["evidence"],
        "fragment": _fragment(conv, ai_c["evidence"]),
        "original_total": original_total,
        "final_total": ev["total"],
    }


@app.post("/api/appeals")
def create_appeal(data: dict = Body(...)):
    conv_id, cid = data.get("conversation_id"), data.get("criterion_id")
    comment = (data.get("comment") or "").strip()
    if not comment:
        raise HTTPException(400, "Добавьте комментарий")
    ev = db.get_evaluation(conv_id)
    if ev is None:
        raise HTTPException(404, "У диалога ещё нет оценки")
    if not any(c["criterion_id"] == cid for c in ev["final"]["criteria"]):
        raise HTTPException(404, "Критерий не найден")
    return {"id": db.create_appeal(conv_id, cid, comment)}


@app.get("/api/appeals")
def get_appeals(conversation_id: str | None = None):
    items = db.list_appeals()
    if conversation_id:
        items = [a for a in items if a["conversation_id"] == conversation_id]
    return [_appeal_view(a) for a in items]


@app.post("/api/appeals/{appeal_id}/decision")
def decide_appeal(appeal_id: int, data: dict = Body(...)):
    a = db.get_appeal(appeal_id)
    if a is None:
        raise HTTPException(404, "Апелляция не найдена")
    if a["status"] != "open":
        raise HTTPException(400, "По этой апелляции решение уже принято")
    decision = data.get("decision")
    reason = (data.get("reason") or "").strip()
    if decision not in ("confirm", "change"):
        raise HTTPException(400, 'decision должен быть "confirm" или "change"')
    if not reason:
        raise HTTPException(400, "Укажите причину решения")

    ev = db.get_evaluation(a["conversation_id"])
    checklist = db.get_checklist_version(ev["checklist_version"]) or db.get_checklist()
    crit = _criterion(checklist, a["criterion_id"])
    final = ev["final"]

    new_value = None
    if decision == "change":
        new_value = _check_value(crit, data.get("new_value"))

    for c in final["criteria"]:
        if c["criterion_id"] == a["criterion_id"]:
            c["reviewed"] = True
            c["review_reason"] = reason
            if decision == "change":
                c["original_answer"] = c["answer"]
                c["answer"] = new_value
                c["applicable"] = new_value != "na"

    # Итог пересчитывает код по тем же правилам, что и раньше
    answers = {c["criterion_id"]: {"answer": c["answer"]} for c in final["criteria"]}
    calc = scoring.compute_total(checklist, answers)
    final = {"criteria": final["criteria"], **calc}
    db.save_evaluation(a["conversation_id"], ev["checklist_version"], ev["ai"],
                       final, calc["total"], calc["gross_error"])

    db.decide_appeal(appeal_id, "changed" if decision == "change" else "confirmed",
                     decision, new_value, reason)
    return _appeal_view(db.get_appeal(appeal_id))

app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
