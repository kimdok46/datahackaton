def compute_total(checklist, answers):
    """
    checklist: словарь из data/checklist.json
    answers:   {criterion_id: {"answer": ...}}
               для да/нет критериев answer = "yes" | "no" | "na"
               для шкальных answer = число (ступень шкалы) или "na"
               для критерия-флага (грубые ошибки) answer = "yes" | "no"
    Возвращает словарь: total, sections, gross_error, details.
    """
    earned_sum = 0.0
    weight_sum = 0.0
    sections = {}
    details = {}
    gross_error = False

    for c in checklist["criteria"]:
        cid = c["id"]
        if cid not in answers:
            raise ValueError(f"Нет ответа по критерию {cid}")
        answer = answers[cid]["answer"]

        # Критерий-флаг: баллов не даёт, только отметка
        if c["type"] == "flag":
            gross_error = (answer == "yes")
            details[cid] = {"points": None, "max": 0, "applicable": True}
            continue

        # N/A
        if answer == "na":
            if not c.get("allow_na"):
                raise ValueError(f"Для критерия {cid} N/A не разрешён")
            details[cid] = {"points": None, "max": c["weight"], "applicable": False}
            continue

        # Баллы
        if c["type"] == "yes_no":
            if answer not in ("yes", "no"):
                raise ValueError(f"Критерий {cid}: ответ должен быть yes, no или na")
            points = c["weight"] if answer == "yes" else 0.0
        elif c["type"] == "scale":
            steps = [s["value"] for s in c["scale"]]
            if answer not in steps:
                raise ValueError(f"Критерий {cid}: {answer} нет среди ступеней {steps}")
            points = answer / max(steps) * c["weight"]
        else:
            raise ValueError(f"Неизвестный тип критерия: {c['type']}")

        earned_sum += points
        weight_sum += c["weight"]
        s = sections.setdefault(c["section"], {"earned": 0.0, "max": 0.0})
        s["earned"] += points
        s["max"] += c["weight"]
        details[cid] = {"points": round(points, 2), "max": c["weight"], "applicable": True}

    # Если применимых критериев с весом нет, итог не считается
    if weight_sum == 0:
        total = None
    else:
        total = round(earned_sum / weight_sum * 100, 2)

    for s in sections.values():
        s["earned"] = round(s["earned"], 2)

    return {
        "total": total,
        "sections": sections,
        "gross_error": gross_error,
        "details": details,
    }
