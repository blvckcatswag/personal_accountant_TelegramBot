from __future__ import annotations

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery("receiptbot", broker=settings.broker_url, backend=settings.result_backend)
celery_app.conf.beat_schedule = {
    "refresh-currency-rates": {
        "task": "app.tasks.refresh_currency_rates",
        "schedule": 60 * 60 * 24,
    },
    "weekly-digest": {
        "task": "app.tasks.send_weekly_digest",
        "schedule": 60 * 60 * 24,
    },
}


@celery_app.task(name="app.tasks.refresh_currency_rates")
def refresh_currency_rates() -> str:
    return "currency rates refresh requested"


@celery_app.task(name="app.tasks.send_weekly_digest")
def send_weekly_digest() -> str:
    return "weekly digest requested"
