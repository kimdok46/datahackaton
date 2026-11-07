import json
from pathlib import Path

from backend.scoring import compute_total

ROOT = Path(__file__).resolve().parent.parent
checklist = json.loads((ROOT / "data" / "checklist.json").read_text(encoding="utf-8"))


def test_example_with_na():
    answers = {
        "contact": {"answer": "yes"},
        "needs": {"answer": "na"},
        "identification": {"answer": "na"},
        "consultation": {"answer": 15},
        "empathy": {"answer": "yes"},
        "dialog_management": {"answer": 7},
        "closing": {"answer": "yes"},
        "gross_errors": {"answer": "no"},
    }
    r = compute_total(checklist, answers)
    assert r["total"] == 85.38, r["total"]
    assert r["gross_error"] is False


def test_all_max_is_100():
    answers = {
        "contact": {"answer": "yes"},
        "needs": {"answer": "yes"},
        "identification": {"answer": "yes"},
        "consultation": {"answer": 20},
        "empathy": {"answer": "yes"},
        "dialog_management": {"answer": 10},
        "closing": {"answer": "yes"},
        "gross_errors": {"answer": "no"},
    }
    assert compute_total(checklist, answers)["total"] == 100.0


def test_all_zero_with_gross_error():
    answers = {
        "contact": {"answer": "no"},
        "needs": {"answer": "no"},
        "identification": {"answer": "no"},
        "consultation": {"answer": 0},
        "empathy": {"answer": "no"},
        "dialog_management": {"answer": 0},
        "closing": {"answer": "no"},
        "gross_errors": {"answer": "yes"},
    }
    r = compute_total(checklist, answers)
    assert r["total"] == 0.0
    assert r["gross_error"] is True


def test_all_na_gives_none():
    # Критерии без права на N/A (контакт, эмпатия, ведение диалога) всегда применимы,
    # поэтому None возможен только при чек-листе, где все веса исключены.
    # Проверяем это на урезанном чек-листе.
    small = {
        "criteria": [
            {"id": "x", "type": "yes_no", "weight": 10, "allow_na": True, "section": "s"}
        ]
    }
    assert compute_total(small, {"x": {"answer": "na"}})["total"] is None


if __name__ == "__main__":
    test_example_with_na()
    test_all_max_is_100()
    test_all_zero_with_gross_error()
    test_all_na_gives_none()
    print("Все тесты прошли")
