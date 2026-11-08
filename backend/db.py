import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "cqa.db"  # файл базы, он уже в .gitignore (*.db)


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def now():
    return datetime.now().isoformat(timespec="seconds")


def init_db():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            language TEXT,
            channel TEXT,
            data_json TEXT
        );
        CREATE TABLE IF NOT EXISTS checklists (
            version INTEGER PRIMARY KEY,
            data_json TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS evaluations (
            conversation_id TEXT PRIMARY KEY,
            checklist_version INTEGER,
            ai_json TEXT,
            final_json TEXT,
            total REAL,
            gross_error INTEGER,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS appeals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT,
            criterion_id TEXT,
            comment TEXT,
            status TEXT,
            decision TEXT,
            new_value TEXT,
            reason TEXT,
            created_at TEXT,
            decided_at TEXT
        );
        """)


# ---------- диалоги ----------

def import_records(records):
    with conn() as c:
        for r in records:
            c.execute(
                "INSERT OR REPLACE INTO conversations (id, language, channel, data_json) VALUES (?, ?, ?, ?)",
                (r["conversation_id"], r["language"], r["channel"], json.dumps(r, ensure_ascii=False)),
            )
    return len(records)


def count_conversations():
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]


def list_conversations():
    with conn() as c:
        rows = c.execute("""
            SELECT c.id, c.language, c.channel, e.total, e.gross_error
            FROM conversations c
            LEFT JOIN evaluations e ON e.conversation_id = c.id
            ORDER BY c.id
        """).fetchall()
    return [
        {
            "id": r["id"],
            "language": r["language"],
            "channel": r["channel"],
            "total": r["total"],
            "gross_error": None if r["gross_error"] is None else bool(r["gross_error"]),
            "evaluated": r["total"] is not None or r["gross_error"] is not None,
        }
        for r in rows
    ]


def get_conversation(conv_id):
    with conn() as c:
        row = c.execute("SELECT data_json FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    return json.loads(row["data_json"]) if row else None


# ---------- чек-листы ----------

def get_checklist():
    with conn() as c:
        row = c.execute("SELECT data_json FROM checklists ORDER BY version DESC LIMIT 1").fetchone()
    return json.loads(row["data_json"]) if row else None


def save_checklist(data):
    with conn() as c:
        last = c.execute("SELECT MAX(version) FROM checklists").fetchone()[0] or 0
        version = last + 1
        data["version"] = version
        c.execute(
            "INSERT INTO checklists (version, data_json, created_at) VALUES (?, ?, ?)",
            (version, json.dumps(data, ensure_ascii=False), now()),
        )
    return version


# ---------- оценки ----------

def save_evaluation(conv_id, checklist_version, ai_result, final_result, total, gross_error):
    with conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO evaluations
               (conversation_id, checklist_version, ai_json, final_json, total, gross_error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                conv_id,
                checklist_version,
                json.dumps(ai_result, ensure_ascii=False),
                json.dumps(final_result, ensure_ascii=False),
                total,
                int(gross_error),
                now(),
            ),
        )


def get_evaluation(conv_id):
    with conn() as c:
        row = c.execute("SELECT * FROM evaluations WHERE conversation_id = ?", (conv_id,)).fetchone()
    if not row:
        return None
    return {
        "checklist_version": row["checklist_version"],
        "ai": json.loads(row["ai_json"]),
        "final": json.loads(row["final_json"]),
        "total": row["total"],
        "gross_error": bool(row["gross_error"]),
        "created_at": row["created_at"],
    }


# ---------- апелляции ----------

def create_appeal(conv_id, criterion_id, comment):
    with conn() as c:
        cur = c.execute(
            """INSERT INTO appeals (conversation_id, criterion_id, comment, status, created_at)
               VALUES (?, ?, ?, 'open', ?)""",
            (conv_id, criterion_id, comment, now()),
        )
        return cur.lastrowid


def list_appeals():
    with conn() as c:
        rows = c.execute("SELECT * FROM appeals ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def get_appeal(appeal_id):
    with conn() as c:
        row = c.execute("SELECT * FROM appeals WHERE id = ?", (appeal_id,)).fetchone()
    return dict(row) if row else None


def decide_appeal(appeal_id, status, decision, new_value, reason):
    with conn() as c:
        c.execute(
            """UPDATE appeals SET status = ?, decision = ?, new_value = ?, reason = ?, decided_at = ?
               WHERE id = ?""",
            (status, decision, None if new_value is None else str(new_value), reason, now(), appeal_id),
        )

def get_checklist_version(version):
    with conn() as c:
        row = c.execute("SELECT data_json FROM checklists WHERE version = ?", (version,)).fetchone()
    return json.loads(row["data_json"]) if row else None