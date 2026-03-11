from __future__ import annotations

from contextlib import asynccontextmanager

from aiogram import Bot
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import ORJSONResponse

from app.api import build_api_router
from app.bot import create_dispatcher
from app.config import get_settings
from app.container import ServiceContainer
from app.db import SessionLocal, init_db


def create_app() -> FastAPI:
    settings = get_settings()
    container = ServiceContainer.build()
    dispatcher = create_dispatcher(container)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await init_db()
        async with SessionLocal() as session:
            await container.category_service(session).seed_defaults()
            await session.commit()
        yield

    app = FastAPI(
        title="ReceiptBot",
        version="0.1.0",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )
    app.include_router(build_api_router(container))

    @app.post("/telegram/webhook")
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        if settings.telegram_webhook_secret and (
            x_telegram_bot_api_secret_token != settings.telegram_webhook_secret
        ):
            raise HTTPException(status_code=401, detail="Invalid Telegram secret")
        if not settings.telegram_bot_token:
            raise HTTPException(status_code=500, detail="Telegram token is not configured")
        payload = await request.json()
        bot = Bot(settings.telegram_bot_token)
        update = Update.model_validate(payload)
        await dispatcher.feed_update(bot, update)
        return {"ok": True}

    return app
