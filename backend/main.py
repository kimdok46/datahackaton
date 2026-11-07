import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Корень репозитория: папка, которая лежит на уровень выше backend/
ROOT = Path(__file__).resolve().parent.parent

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/conversations")
def conversations():
    path = ROOT / "data" / "dev.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data["data"]["records"]
    return [
        {
            "id": r["conversation_id"],
            "language": r["language"],
            "channel": r["channel"],
        }
        for r in records
    ]


# Раздача страницы. Эта строка должна быть ПОСЛЕДНЕЙ в файле,
# иначе она перехватит запросы к /health и /api/...
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
