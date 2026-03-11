# Personal Accountant Bot

Telegram бот для учета расходов по чекам.

Основная идея простая: пользователь отправляет фото чека в Telegram, бот распознает текст, сохраняет покупки, раскладывает их по категориям и потом показывает историю, статистику и прогресс по бюджетам.

## Что умеет бот

- принимать чек как фото, документ или текст
- распознавать текст чека через Google Vision OCR
- сохранять чек, позиции и общую сумму
- определять валюту и пересчитывать сумму в базовую валюту пользователя
- показывать последние чеки
- считать статистику за неделю или месяц
- сохранять бюджет на неделю или месяц
- выгружать пользовательские данные в CSV
- удалять все данные пользователя по команде

## Команды

- `/start`
- `/help`
- `/cancel`
- `/history`
- `/stats week`
- `/stats month`
- `/budget`
- `/currency UAH`
- `/mydata`
- `/deleteaccount`

## Стек

- Python 3.12+
- aiogram 3
- FastAPI
- SQLAlchemy 2 async
- PostgreSQL или SQLite
- Redis
- Google Vision OCR
- Docker
- Railway

## Как запустить локально

1. Скопируй `.env.example` в `.env`.
2. Укажи в `.env` токен Telegram бота.
3. Положи `gcloud_key.json` в корень проекта.
4. Укажи в `.env`:

```env
OCR_ENGINE=google_vision
GOOGLE_APPLICATION_CREDENTIALS=./gcloud_key.json
```

5. Установи зависимости:

```powershell
python -m pip install .[dev]
```

6. Запусти бота:

```powershell
python -m app.polling
```

После запуска можно открыть бота в Telegram, нажать `/start` и отправить фото чека.

## Локальная база

Для простого локального запуска можно оставить SQLite.

Если нужен режим ближе к продакшену, можно поднять контейнеры:

```powershell
docker compose up --build
```

## Railway

Для деплоя на Railway проекту нужны:

- приложение с этим репозиторием
- PostgreSQL
- Redis
- переменные окружения для Telegram и Google Vision

Основная точка входа для приложения:

`uvicorn app.main:create_app --factory --host 0.0.0.0 --port $PORT`

## Полезные файлы

- `app/main.py` - FastAPI приложение и Telegram webhook
- `app/polling.py` - локальный запуск бота через polling
- `app/bot.py` - Telegram handlers
- `app/db.py` - модели и подключение к базе
- `app/services/ocr.py` - OCR и парсинг чека
- `app/services/currency.py` - курсы и конвертация валют
- `app/services/analytics.py` - статистика и экспорт

## Проверка

```powershell
ruff check .
pytest tests
```

## Текущее состояние

Это рабочий MVP-каркас. Базовые сценарии уже собраны, но проект еще можно усиливать дальше:

- улучшать парсинг разных форматов чеков
- добавлять более точную категоризацию
- развивать уведомления и фоновые задачи
- доводить деплой на Railway до полностью production-сценария
