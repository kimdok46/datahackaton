import json
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from backend import db

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


# Раздача страницы. Эта строка должна быть ПОСЛЕДНЕЙ в файле.
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
