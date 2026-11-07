# API-контракт CQA

Все эндпоинты начинаются с /api. Формат данных: JSON.

## Диалоги
- GET  /api/conversations                  список (id, язык, канал, итог, есть ли оценка)
- GET  /api/conversations/{id}             транскрипт + оценка (если есть)
- POST /api/conversations/{id}/evaluate    запустить оценку одного диалога
- POST /api/evaluate-all                   оценить все диалоги
- POST /api/import?group_id=DEV            загрузить набор организаторов в базу

## Чек-лист
- GET  /api/checklist                      текущая версия
- PUT  /api/checklist                      сохранить (создаёт новую версию)
- POST /api/checklist/apply                пересчитать оценки по текущей версии

## Апелляции
- POST /api/appeals                        {conversation_id, criterion_id, comment}
- GET  /api/appeals                        список
- POST /api/appeals/{id}/decision          {decision: "confirm"|"change", new_value, reason}

## Формат оценки одного диалога
{
  "conversation_id": "p1-ru-001",
  "checklist_version": 1,
  "criteria": [
    {
      "criterion_id": "contact",
      "answer": "yes",
      "score": 10,
      "applicable": true,
      "explanation": "Оператор поздоровался и представился",
      "evidence": [{"message_index": 0, "quote": "Здравствуйте!"}],
      "checked_range": "00:00-00:30"
    }
  ],
  "gross_error": false,
  "total": 85.38,
  "sections": [{"id": "start", "earned": 10, "max": 10}]
}

answer: "yes" | "no" | "na" для да/нет критериев, число для шкальных.
Для N/A: applicable=false и explanation называет выполненное условие.
