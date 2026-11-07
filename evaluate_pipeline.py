import json

def evaluate(conversation: str, checklist: list[str]) -> dict:
    """Временная заглушка (mock) для хакатона, пока API перегружено."""
    # Возвращаем структуру, которую от нас ждет проект
    result_data = {
        "score": len(checklist),
        "max_score": len(checklist),
        "passed": True,
        "quotes_verified": ["Пример цитаты из диалога"],
        "comment": "Тестовая оценка (заглушка для хакатона)"
    }
    
    # Проверяем цитаты нашей же функцией
    result_data = verify_quotes(conversation, result_data)
    return result_data

def verify_quotes(conversation: str, data: dict) -> dict:
    quotes = data.get("quotes_verified", [])
    valid_quotes = []
    hallucinated = []

    for quote in quotes:
        if quote.lower() in conversation.lower():
            valid_quotes.append(quote)
        else:
            hallucinated.append(quote)

    data["quotes_verified"] = valid_quotes
    if hallucinated:
        data["hallucinated_quotes_detected"] = hallucinated
        data["passed"] = False

    return data

# --- ТЕСТОВЫЙ ПРОГОН НА 5 ДИАЛОГОВ ---
if __name__ == "__main__":
    checklist = [
        "Оператор поздоровался",
        "Оператор предложил решение"
    ]
    
    # Твои 5 диалогов (RU, KZ, EN и звонки)
    dialogues = {
        "1_RU": "Клиент: Здравствуйте! Оператор: Здравствуйте, чем могу помочь?",
        "2_KZ": "Клиент: Сәлем! Оператор: Сәлеметсіз бе, қалай көмектесе аламын?",
        "3_EN": "Client: Hello! Operator: Hello, how can I help you today?",
        "4_Call": "Оператор: Алло, техподдержка. Клиент: У меня пропал интернет! Оператор: Проверяем линию.",
        "5_Complex": "Клиент: Верните деньги! Оператор: Здравствуйте, оформляю возврат."
    }
    
    print("🚀 Прогоняем 5 диалогов на заглушке...\n")
    for name, conv in dialogues.items():
        print(f"--- Тест: {name} ---")
        res = evaluate(conv, checklist)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        print()
