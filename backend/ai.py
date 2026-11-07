import json
import os
import re
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent

# На каком языке модель пишет объяснения (цитаты всегда на языке диалога)
EXPLANATION_LANGUAGE = "русском"


# ---------------------------------------------------------------
# Провайдеры: порядок = порядок запасной цепочки.
# Провайдер без ключа в .env пропускается.
# Названия моделей меняются, поэтому их можно переопределить в .env
# (например GEMINI_MODEL=...).
# ---------------------------------------------------------------
PROVIDER_SPECS = [
    # name, ключ, base_url, переменная модели, модель по умолчанию, поддержка json-режима
    ("gemini", "GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/",
     "GEMINI_MODEL", "gemini-flash-latest", True),
    ("mistral", "MISTRAL_API_KEY", "https://api.mistral.ai/v1",
     "MISTRAL_MODEL", "mistral-small-latest", True),
    ("groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1",
     "GROQ_MODEL", "llama-3.3-70b-versatile", True),
    ("anthropic", "ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/",
     "ANTHROPIC_MODEL", "claude-haiku-5-5", False),
]


def get_providers():
    providers = []
    for name, key_env, base_url, model_env, default_model, json_mode in PROVIDER_SPECS:
        key = os.getenv(key_env)
        if key:
            providers.append({
                "name": name,
                "api_key": key,
                "base_url": base_url,
                "model": os.getenv(model_env, default_model),
                "json_mode": json_mode,
            })
    return providers


# ---------------------------------------------------------------
# Промпт: собирается из чек-листа каждый раз заново
# ---------------------------------------------------------------
def format_transcript(conversation):
    lines = []
    for i, m in enumerate(conversation["transcript"]):
        lines.append(f"[{i}] {m['timestamp']} {m['speaker']}: {m['text']}")
    return "\n".join(lines)


def describe_criterion(c):
    parts = [f'- id: "{c["id"]}" | {c["name"]} | тип: {c["type"]} | вес: {c["weight"]}']
    if c["type"] == "yes_no":
        parts.append('  Допустимые ответы: "yes", "no"' + (', "na"' if c.get("allow_na") else ""))
        if c.get("yes_condition"):
            parts.append(f'  "yes", если: {c["yes_condition"]}')
    elif c["type"] == "scale":
        steps = "; ".join(f'{s["value"]} = {s["label"]}' for s in c["scale"])
        parts.append("  Ответ: ОДНО число из ступеней" + (' или "na"' if c.get("allow_na") else ""))
        parts.append(f"  Ступени: {steps}")
    elif c["type"] == "flag":
        parts.append('  Допустимые ответы: "yes" (нарушение НАЙДЕНО), "no" (нарушений нет)')
        if c.get("yes_condition"):
            parts.append(f'  "yes", если: {c["yes_condition"]}')
    if c.get("allow_na") and c.get("na_condition"):
        parts.append(f'  "na", если: {c["na_condition"]}')
    return "\n".join(parts)


def build_prompt(conversation, checklist):
    criteria_text = "\n".join(describe_criterion(c) for c in checklist["criteria"])
    notes = "\n".join(f"- {n}" for n in checklist.get("notes", []))

    system = (
        "Ты внимательный и справедливый проверяющий качества работы операторов "
        "службы поддержки брокерской компании. Ты оцениваешь РАБОТУ ОПЕРАТОРА по чек-листу. "
        "Реплики с ролью bot и system служат только контекстом. "
        "Диалоги бывают на русском, казахском и английском: применяй одни и те же правила ко всем языкам. "
        "Отвечай ТОЛЬКО валидным JSON без пояснений вокруг и без markdown."
    )

    user = f"""Оцени диалог по чек-листу.

КРИТЕРИИ:
{criteria_text}

ВАЖНЫЕ ПРАВИЛА:
- «no» означает, что действие было нужно, но оператор его пропустил или сделал неправильно.
- «na» ставится ТОЛЬКО там, где он разрешён и выполнено условие неприменимости.
- Если требовалась проверка личности перед защищённым действием, а в чате её нет, ставь «no», а не «na».
- Общий вопрос (например, о тарифах) идентификации не требует.
- Консультацию (правильно и полно ли решён запрос) и ведение диалога (ясность и последовательность) оценивай раздельно.
{notes}

ТРЕБОВАНИЯ К ОБОСНОВАНИЮ:
- explanation: 1-2 коротких предложения на {EXPLANATION_LANGUAGE} языке: что подтверждает оценку.
- evidence: список реплик-доказательств. message_index = номер реплики в квадратных скобках,
  quote = ДОСЛОВНАЯ цитата из текста этой реплики на языке диалога (без перевода и пересказа, до 200 символов).
- Если действие пропущено ("no"), в evidence укажи реплику, где оно ожидалось (или оставь пустым), а в checked_range опиши проверенный участок.
- Если "na", в explanation назови выполненное условие неприменимости.
- checked_range: проверенный участок диалога, например "реплики 0-3" или "весь диалог".

ФОРМАТ ОТВЕТА (строго):
{{"criteria": [
  {{"id": "<id критерия>", "answer": <"yes" | "no" | "na" | число>,
    "explanation": "...",
    "evidence": [{{"message_index": 0, "quote": "..."}}],
    "checked_range": "..."}}
]}}
Верни ровно по одному элементу на каждый критерий из списка.

ДИАЛОГ (язык: {conversation["language"]}, канал: {conversation["channel"]}):
{format_transcript(conversation)}
"""
    return system, user


# ---------------------------------------------------------------
# Вызов модели и разбор ответа
# ---------------------------------------------------------------
def call_model(provider, system, user):
    client = OpenAI(api_key=provider["api_key"], base_url=provider["base_url"], timeout=120)
    kwargs = dict(
        model=provider["model"],
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0,
    )
    if provider["json_mode"]:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    text = resp.choices[0].message.content or ""
    usage = {
        "input_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(resp.usage, "completion_tokens", 0) or 0,
    }
    return text, usage


def parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("в ответе нет JSON")
    return json.loads(text[start:end + 1])


def _norm(s):
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def verify_evidence(evidence, transcript):
    """Проверяет цитаты кодом. Неверный индекс исправляет, если цитата найдена в другой реплике."""
    result = []
    for ev in evidence if isinstance(evidence, list) else []:
        if not isinstance(ev, dict):
            continue
        quote = str(ev.get("quote", "")).strip()
        idx = ev.get("message_index")
        if not isinstance(idx, int) or not (0 <= idx < len(transcript)):
            idx = None
        verified = False
        if quote:
            q = _norm(quote)
            if idx is not None and q in _norm(transcript[idx]["text"]):
                verified = True
            else:
                for i, m in enumerate(transcript):
                    if q in _norm(m["text"]):
                        idx, verified = i, True
                        break
        if idx is None:
            continue
        result.append({"message_index": idx, "quote": quote, "verified": verified})
    return result


def check_and_normalize(raw, checklist, transcript):
    """Возвращает (результат, список проблем). Если проблем нет, результат годится для scoring."""
    problems = []
    items = {}
    if isinstance(raw, dict) and isinstance(raw.get("criteria"), list):
        for it in raw["criteria"]:
            if isinstance(it, dict):
                items[str(it.get("id"))] = it

    criteria_out, answers = [], {}
    for c in checklist["criteria"]:
        it = items.get(c["id"])
        if it is None:
            problems.append(f'нет критерия "{c["id"]}"')
            continue

        ans = it.get("answer")
        if isinstance(ans, str):
            a = ans.strip().lower()
            if a in ("yes", "да", "true"):
                ans = "yes"
            elif a in ("no", "нет", "false"):
                ans = "no"
            elif a in ("na", "n/a", "н/п"):
                ans = "na"
            else:
                try:
                    ans = int(float(a))
                except ValueError:
                    pass
        elif isinstance(ans, float) and ans.is_integer():
            ans = int(ans)

        if ans == "na":
            if not c.get("allow_na"):
                problems.append(f'критерий "{c["id"]}": N/A не разрешён')
                continue
        elif c["type"] in ("yes_no", "flag"):
            if ans not in ("yes", "no"):
                problems.append(f'критерий "{c["id"]}": ответ должен быть "yes" или "no"')
                continue
        elif c["type"] == "scale":
            steps = [s["value"] for s in c["scale"]]
            if ans not in steps or isinstance(ans, bool):
                problems.append(f'критерий "{c["id"]}": ответ должен быть одним из {steps}')
                continue

        explanation = str(it.get("explanation", "")).strip()
        if not explanation:
            problems.append(f'критерий "{c["id"]}": пустое объяснение')
            continue

        criteria_out.append({
            "criterion_id": c["id"],
            "answer": ans,
            "applicable": ans != "na",
            "explanation": explanation,
            "evidence": verify_evidence(it.get("evidence"), transcript),
            "checked_range": str(it.get("checked_range", "")).strip(),
        })
        answers[c["id"]] = {"answer": ans}

    return {"criteria": criteria_out, "answers": answers}, problems


# ---------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------
def evaluate(conversation, checklist):
    providers = get_providers()
    if not providers:
        raise RuntimeError("Не найден ни один ключ API. Создай файл .env (см. .env.example).")

    system, user = build_prompt(conversation, checklist)
    transcript = conversation["transcript"]
    total_usage = {"input_tokens": 0, "output_tokens": 0}
    errors = []

    for p in providers:
        feedback = ""
        for attempt in range(3):
            try:
                text, usage = call_model(p, system, user + feedback)
            except Exception as e:  # лимит, сеть, неверный ключ: переходим к следующему провайдеру
                errors.append(f"{p['name']}: {e}")
                break
            total_usage["input_tokens"] += usage["input_tokens"]
            total_usage["output_tokens"] += usage["output_tokens"]

            try:
                raw = parse_json(text)
            except ValueError as e:
                errors.append(f"{p['name']}: некорректный JSON ({e})")
                feedback = "\n\nПредыдущий ответ не был валидным JSON. Верни ТОЛЬКО JSON по формату."
                continue

            result, problems = check_and_normalize(raw, checklist, transcript)
            if not problems:
                result["provider"] = p["name"]
                result["model"] = p["model"]
                result["usage"] = total_usage
                return result

            errors.append(f"{p['name']}: {'; '.join(problems)}")
            feedback = "\n\nВ предыдущем ответе были ошибки, исправь их:\n- " + "\n- ".join(problems)

    raise RuntimeError("Не удалось получить оценку. " + " | ".join(errors[-6:]))


# ---------------------------------------------------------------
# Быстрая проверка из терминала:  python -m backend.ai [id диалога]
# ---------------------------------------------------------------
if __name__ == "__main__":
    from backend.scoring import compute_total

    checklist = json.loads((ROOT / "data" / "checklist.json").read_text(encoding="utf-8"))
    records = json.loads((ROOT / "data" / "dev.json").read_text(encoding="utf-8"))["data"]["records"]
    wanted = sys.argv[1] if len(sys.argv) > 1 else records[0]["conversation_id"]
    conv = next(r for r in records if r["conversation_id"] == wanted)

    res = evaluate(conv, checklist)
    calc = compute_total(checklist, res["answers"])
    print(f'Диалог {wanted} ({conv["language"]}), модель: {res["provider"]} / {res["model"]}')
    for c in res["criteria"]:
        print(f'- {c["criterion_id"]}: {c["answer"]} | {c["explanation"]}')
        for ev in c["evidence"]:
            mark = "OK" if ev["verified"] else "НЕ НАЙДЕНА"
            print(f'    [{ev["message_index"]}] «{ev["quote"]}» ({mark})')
    print("ИТОГ:", calc["total"], "| грубая ошибка:", calc["gross_error"])
    print("Токены:", res["usage"])
