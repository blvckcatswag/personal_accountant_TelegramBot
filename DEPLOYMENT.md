# Deployment

Railway deployment:

1. Create services: app, PostgreSQL, Redis.
2. In the app service set environment variables from `.env.example`, but use Railway values for `DATABASE_URL`, `REDIS_URL`, `BROKER_URL`, `RESULT_BACKEND`.
3. Configure `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `OCR_ENGINE=google_vision`.
4. Add Google service account JSON into `GOOGLE_SERVICE_ACCOUNT_JSON` as one secret variable. This is preferred for Railway.
5. Use `GOOGLE_APPLICATION_CREDENTIALS` only if you intentionally mount a JSON file inside the container.
6. Set start command to `uvicorn app.main:create_app --factory --host 0.0.0.0 --port $PORT`.
7. Point Telegram webhook to `https://<your-domain>/telegram/webhook`.
8. If Railway injects a non-async postgres URL, rewrite it to `postgresql+asyncpg://...` before saving into `DATABASE_URL`.

Local deployment:

1. Copy `.env.example` to `.env`.
2. Run `docker compose up --build`.
3. Open `http://localhost:8000/api/v1/health`.
