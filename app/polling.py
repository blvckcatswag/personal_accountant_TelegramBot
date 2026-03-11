from __future__ import annotations

import asyncio

from app.bot import create_bot, create_dispatcher
from app.container import ServiceContainer
from app.db import SessionLocal, init_db


async def main() -> None:
    container = ServiceContainer.build()
    if not container.settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty")

    await init_db()
    async with SessionLocal() as session:
        await container.category_service(session).seed_defaults()
        await session.commit()

    bot = await create_bot(container.settings.telegram_bot_token)
    dispatcher = create_dispatcher(container)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
