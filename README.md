# ReceiptBot

Telegram-бот для учета расходов по фото чеков, с OCR-пайплайном, мультивалютностью, бюджетами, аналитикой и FastAPI API.

## Что реализовано

- `aiogram 3` бот с командами `/start`, `/help`, `/cancel`, `/stats`, `/history`, `/budget`, `/currency`, `/mydata`, `/deleteaccount`
- асинхронный FastAPI backend с webhook endpoint и REST API
- SQLAlchemy 2 async-модели для пользователей, чеков, позиций, категорий, бюджетов, уведомлений и курсов валют
- OCR pipeline MVP: загрузка файла, mock OCR engine, парсер текста чека, дедупликация
- категоризация по словарю и fuzzy matching
- бюджеты и прогресс-бары
- аналитика по категориям и магазинам, CSV экспорт
- мультивалютность с NBU / ExchangeRate providers и кэшированием курсов
- Celery skeleton для периодических задач
- Railway / Docker / GitHub Actions конфигурация
- базовые unit-тесты

## Быстрый старт

```bash
cp .env.example .env
pip install .[dev]
uvicorn app.main:create_app --factory --reload
```

Проверка health endpoint:

```bash
curl http://localhost:8000/api/v1/health
```

Для локального Telegram-бота без webhook:

```bash
python -m app.polling
```

## Архитектура

- `app/main.py`: FastAPI app, lifespan, Telegram webhook
- `app/bot.py`: команды и сценарии aiogram
- `app/db.py`: async engine, ORM-модели
- `app/repositories.py`: доступ к данным
- `app/services/*`: OCR, категоризация, валюты, аналитика, бюджеты, storage
- `app/tasks.py`: Celery beat/task skeleton

## Локальная разработка

```bash
docker compose up --build
ruff check .
pytest
```

## Ограничения текущего MVP

- по умолчанию используется `MockOCREngine`, поэтому реальное распознавание фото надо подключать через внешний OCR provider
- OCR fallback на GPT пока задан архитектурно, но не реализован как отдельный engine
- уведомления и полноценные scheduled digests заведены как каркас Celery, без production-рассылки
