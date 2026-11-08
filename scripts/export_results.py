"""
Выгрузка оценок из cqa.db в results/<dev|prod>/results.json (формат JSON 1.0).

Запуск из корня репозитория:
  python3 scripts/export_results.py --group dev  --team "Название команды"
  python3 scripts/export_results.py --group prod --team "Название команды" --source data/prod.json

--source: JSON организаторов (тот же формат, что data/dev.json). Из него берутся ID диалогов,
          чтобы в результате было ровно столько записей, сколько в наборе.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Наши внутренние id критериев -> коды из приложения организаторов
ID_MAP = {
    "contact": "contact",
    "needs": "discovery",
    "identification": "identification",
    "consultation": "consultation",
    "empathy": "empathy",
    "dialog_management": "dialogue",
    "closing": "closure",
    "gross_errors": "critical",
}
ORDER = ["contact", "discovery", "identification", "consultation",
         "empathy", "dialogue", "closure", "critical"]
BOOL_CRITERIA = {"contact", "discovery", "identification", "empathy", "closure", "critical"}
SCALES = {"consultation": {0, 5, 10, 15, 20}, "dialogue": {0, 4, 7, 10}}
NULL_OK = {"discovery", "identification", "consultation", "closure"}


def convert_value(code, answer):
    if answer == "na":
        return None
    if code in BOOL_CRITERIA:
        return answer == "yes"
    return int(answer)


def fallback_turns(code, value, transcript):
    """Если модель не указала реплик: проверенный участок по правилам из примера организаторов."""
    operator = [i + 1 for i, m in enumerate(transcript) if m["speaker"] == "operator"]
    client = [i + 1 for i, m in enumerate(transcript) if m["speaker"] == "client"]
    if code == "critical":  # грубых нарушений нет: проверены все реплики оператора
        return operator or [1]
    if value is None:  # N/A: реплика клиента, из которой видно, почему критерий неприменим
        return client[:1] or [1]
    return operator[:1] or [1]


def convert_record(conv_id, ev, transcript):
    criteria = []
    for c in ev["final"]["criteria"]:
        code = ID_MAP.get(c["criterion_id"])
        if code is None:
            continue
        # В нашей базе номера реплик с 0, организаторам нужно с 1
        turns = sorted({e["message_index"] + 1 for e in c.get("evidence", [])})
        rationale = c["explanation"]
        if not c.get("evidence") and c.get("checked_range"):
            rationale += f" (проверено: {c['checked_range']})"
        value = convert_value(code, c["answer"])
        if not turns:
            turns = fallback_turns(code, value, transcript)
        criteria.append({
            "criterion_id": code,
            "value": value,
            "rationale": rationale,
            "evidence_turns": turns,
        })
    criteria.sort(key=lambda x: ORDER.index(x["criterion_id"]))
    critical = next(c["value"] for c in criteria if c["criterion_id"] == "critical")
    return {
        "conversation_id": conv_id,
        "status": "ok",
        "criteria": criteria,
        "score": {"final_score": ev["final"]["total"], "critical_flag": critical},
    }


def validate(results, expected_ids):
    problems = []
    ids = [r["conversation_id"] for r in results]
    if len(ids) != len(set(ids)):
        problems.append("есть повторяющиеся ID")
    if set(ids) != set(expected_ids):
        problems.append(f"ID не совпадают с набором: лишние={set(ids)-set(expected_ids)}, "
                        f"пропущены={set(expected_ids)-set(ids)}")
    for r in results:
        if r["status"] != "ok":
            continue
        cid = r["conversation_id"]
        codes = [c["criterion_id"] for c in r["criteria"]]
        if sorted(codes) != sorted(ORDER):
            problems.append(f"{cid}: неполный набор критериев {codes}")
        for c in r["criteria"]:
            v, code = c["value"], c["criterion_id"]
            if v is None and code not in NULL_OK:
                problems.append(f"{cid}: N/A не разрешён для {code}")
            if code in SCALES and v is not None and v not in SCALES[code]:
                problems.append(f"{cid}: {code}={v} вне шкалы")
            if code in ("dialogue",) and v is None:
                problems.append(f"{cid}: dialogue не может быть null")
            if not c["evidence_turns"]:
                problems.append(f"(предупреждение) {cid}/{code}: нет evidence_turns")
        crit = next(c["value"] for c in r["criteria"] if c["criterion_id"] == "critical")
        if r["score"]["critical_flag"] != crit:
            problems.append(f"{cid}: critical_flag не совпадает с value")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", choices=["dev", "prod"], required=True)
    ap.add_argument("--team", required=True)
    ap.add_argument("--source", help="JSON набора (по умолчанию data/dev.json для dev)")
    ap.add_argument("--db", default=str(ROOT / "cqa.db"))
    args = ap.parse_args()

    source = Path(args.source) if args.source else ROOT / "data" / "dev.json"
    if args.group == "prod" and not args.source:
        sys.exit("Для prod укажите --source с файлом контрольного набора")
    records = json.loads(source.read_text(encoding="utf-8"))["data"]["records"]
    expected_ids = [r["conversation_id"] for r in records]
    transcripts = {r["conversation_id"]: r["transcript"] for r in records}

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    rows = {r["conversation_id"]: r for r in con.execute("SELECT * FROM evaluations")}

    results, models = [], set()
    for cid in expected_ids:
        row = rows.get(cid)
        if row is None:
            results.append({"conversation_id": cid, "status": "error", "criteria": [],
                            "score": None, "error": "Диалог не был оценён."})
            continue
        ev = {"ai": json.loads(row["ai_json"]), "final": json.loads(row["final_json"])}
        models.add(ev["ai"].get("model"))
        results.append(convert_record(cid, ev, transcripts[cid]))

    out = {
        "schema_version": "1.0",
        "team_name": args.team,
        "model": {"mode": "external_api",
                  "name": ", ".join(sorted(m for m in models if m))},
        "results": results,
    }

    problems = validate(results, expected_ids)
    errors = [p for p in problems if not p.startswith("(предупреждение)")]
    warns = [p for p in problems if p.startswith("(предупреждение)")]

    path = ROOT / "results" / args.group / "results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"Записано: {path}")
    print(f"Диалогов: {len(results)} (ok: {ok}, error: {len(results) - ok})")
    print(f"Модели: {out['model']['name']}")
    print(f"Предупреждений (пустые evidence_turns): {len(warns)}")
    if errors:
        print("ОШИБКИ ПРОВЕРКИ:")
        for p in errors:
            print(" -", p)
        sys.exit(1)
    print("Проверка пройдена.")


if __name__ == "__main__":
    main()