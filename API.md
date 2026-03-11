# API

Base path: `/api/v1`

Endpoints:

- `GET /health` health check
- `GET /users/{telegram_id}/receipts` last 100 receipts
- `GET /users/{telegram_id}/analytics?period=week|month` aggregated analytics
- `GET /users/{telegram_id}/budgets` active budgets with progress
- `GET /users/{telegram_id}/mydata` GDPR-style data export

Telegram:

- `POST /telegram/webhook` receives Telegram updates and feeds aiogram dispatcher

