# CQA, оценка качества диалогов (Freedom Broker Hackathon)

## Запуск
### Backend (FastAPI)
1. cd backend
2. python -m venv .venv && source .venv/bin/activate
3. pip install -r requirements.txt
4. cp .env.example .env   # вписать FLOW_URL и ключи ИИ
5. uvicorn main:app --reload   # проверка: http://localhost:8000/health

### Загрузка датасета
python scripts/load_dataset.py --group_id DEV

### Frontend
Открыть frontend/index.html (или npx serve frontend)

## Команда и роли
## Архитектура
## Стоимость одной оценки (модель, цена, число вызовов)
