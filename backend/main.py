import json
from pathlib import Path

import html
import re

import requests
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import threading
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

AUDIO_DIR = ROOT / "audio_cache"


def _sniff_audio(head, header_type):
    if header_type.startswith("audio/"):
        return header_type
    if head[:4] == b"RIFF":
        return "audio/wav"
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    if head[:4] == b"OggS":
        return "audio/ogg"
    if head[4:8] == b"ftyp":
        return "audio/mp4"
    if head[:4] == b"fLaC":
        return "audio/flac"
    if head[:4] == b"\x1aE\xdf\xa3":
        return "audio/webm"
    return None


def _download(url):
    try:
        r = requests.get(url, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(502, f"Не удалось скачать запись: {e}")
    if r.status_code != 200:
        raise HTTPException(502, f"Сервер записи ответил кодом {r.status_code}")
    return r, r.headers.get("Content-Type", "").split(";")[0].strip().lower()


@app.get("/api/audio/{conv_id}")
def audio(conv_id: str):
    """Запись звонка: скачиваем один раз и отдаём со своего сервера.
    audio_url ведёт на HTML-страницу плеера, настоящий файл лежит в её теге <source>."""
    conv = db.get_conversation(conv_id)
    if conv is None or not conv.get("audio_url"):
        raise HTTPException(404, "У диалога нет записи")
    safe = "".join(ch for ch in conv_id if ch.isalnum() or ch in "-_")
    f, meta = AUDIO_DIR / safe, AUDIO_DIR / (safe + ".type")
    if not (f.exists() and meta.exists()):
        r, header_type = _download(conv["audio_url"])
        if header_type == "text/html":
            m = re.search(r'<source[^>]+src="([^"]+)"', r.text)
            if not m:
                raise HTTPException(502, "На странице записи не найден аудиофайл")
            r, header_type = _download(html.unescape(m.group(1)))
        ctype = _sniff_audio(r.content[:12], header_type)
        if ctype is None:
            raise HTTPException(502, f"Ответ не похож на аудио: {header_type}, {r.content[:12]!r}")
        AUDIO_DIR.mkdir(exist_ok=True)
        f.write_bytes(r.content)
        meta.write_text(ctype)
    return FileResponse(f, media_type=meta.read_text())


@app.get("/api/checklist")
def get_checklist():
    return db.get_checklist()


@app.put("/api/checklist")
def put_checklist(data: dict = Body(...)):
    crits = data.get("criteria")
    if not isinstance(crits, list) or not crits:
        raise HTTPException(400, "В чек-листе должен быть список criteria")
    seen, weight_sum = set(), 0.0
    for c in crits:
        if not isinstance(c, dict) or not c.get("id") or not c.get("name"):
            raise HTTPException(400, "У каждого критерия должны быть id и name")
        if c["id"] in seen:
            raise HTTPException(400, f"Повторяется id критерия: {c['id']}")
        seen.add(c["id"])
        if c.get("type") not in ("yes_no", "scale", "flag"):
            raise HTTPException(400, f"Критерий {c['id']}: тип должен быть yes_no, scale или flag")
        w = c.get("weight", 0)
        if isinstance(w, bool) or not isinstance(w, (int, float)) or w < 0:
            raise HTTPException(400, f"Вес критерия {c['id']} должен быть числом не меньше 0")
        if c["type"] == "scale":
            steps = c.get("scale")
            if (not isinstance(steps, list) or not steps
                    or any(not isinstance(s, dict) or isinstance(s.get("value"), bool)
                           or not isinstance(s.get("value"), (int, float)) for s in steps)):
                raise HTTPException(400, f"Критерий {c['id']}: нужна шкала со ступенями (value, label)")
            if max(s["value"] for s in steps) <= 0:
                raise HTTPException(400, f"Критерий {c['id']}: максимальная ступень шкалы должна быть больше 0")
        if c["type"] != "flag":
            if not c.get("section"):
                raise HTTPException(400, f"Критерий {c['id']}: не указана секция")
            weight_sum += w
    if weight_sum <= 0:
        raise HTTPException(400, "Сумма весов критериев должна быть больше 0")
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


_job = {"running": False, "total": 0, "done": 0, "failed": []}
_job_lock = threading.Lock()


def _run_job(ids):
    limit_hits = 0  # подряд идущие диалоги, упавшие из-за лимита запросов (429)
    for conv_id in ids:
        failed_429 = False
        for attempt in range(3):
            try:
                evaluate_and_save(conv_id)
                limit_hits = 0
                break
            except Exception as e:
                msg = str(getattr(e, "detail", None) or e)
                if "429" in msg and attempt < 2:
                    time.sleep(65)  # лимит запросов в минуту: ждём и повторяем
                    continue
                _job["failed"].append({"id": conv_id, "error": msg[:200]})
                failed_429 = "429" in msg
                break
        _job["done"] += 1
        if failed_429:
            limit_hits += 1
            if limit_hits >= 2:
                _job["failed"].append({"id": "-", "error": "Лимит API исчерпан, оценка остановлена. Добавьте ключи Mistral/Groq в .env или подождите."})
                break
        time.sleep(1)  # пауза, чтобы не упереться в лимит запросов в минуту
    _job["running"] = False


def _start_job(ids):
    with _job_lock:
        if _job["running"]:
            raise HTTPException(409, "Оценка уже идёт, дождитесь окончания")
        _job.update(running=True, total=len(ids), done=0, failed=[])
    threading.Thread(target=_run_job, args=(ids,), daemon=True).start()
    return {"started": len(ids)}


@app.post("/api/evaluate-all")
def evaluate_all(only_missing: bool = False):
    convs = db.list_conversations()
    if only_missing:
        convs = [c for c in convs if not c["evaluated"]]
        if not convs:
            raise HTTPException(400, "Все диалоги уже оценены")
    return _start_job([c["id"] for c in convs])


@app.post("/api/checklist/apply")
def apply_checklist(data: dict | None = Body(None)):
    all_ids = [c["id"] for c in db.list_conversations()]
    ids = (data or {}).get("ids")
    if ids:
        ids = [i for i in ids if i in all_ids]
        if not ids:
            raise HTTPException(400, "Диалоги с такими id не найдены")
    else:
        ids = all_ids
    return {**_start_job(ids), "checklist_version": db.get_checklist()["version"]}


@app.get("/api/evaluate-status")
def evaluate_status():
    return {"running": _job["running"], "total": _job["total"],
            "done": _job["done"], "failed": list(_job["failed"])}


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
    out = []
    for a in items:
        try:
            out.append(_appeal_view(a))
        except (StopIteration, TypeError, KeyError):
            continue  # оценку или критерий пересоздали при применении чек-листа
    return out


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


# Раздача страницы. Эта строка должна быть ПОСЛЕДНЕЙ в файле.
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")