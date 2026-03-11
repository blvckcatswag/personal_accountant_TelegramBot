# Runbook

Common diagnostics:

- Bot does not answer: check `TELEGRAM_BOT_TOKEN`, webhook URL and `/api/v1/health`.
- OCR failures: inspect stored `raw_ocr_json`, verify OCR provider keys, fall back to text upload.
- Currency conversion errors: verify NBU / ExchangeRate API keys and `currency_rates` table contents.
- Database issues: run migrations or delete local SQLite file for local reset.

Recovery:

- restart Railway service
- re-run Alembic migrations
- restore PostgreSQL backup if production data is corrupted

