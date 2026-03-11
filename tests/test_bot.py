from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage
from aiogram.types import Update

from app import db
from app.bot import BudgetStates, create_dispatcher


class FakeSession:
    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def rollback(self) -> None:
        return None


class FakeReceiptService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def process_upload(self, *, session, user, content: bytes, filename: str):
        self.calls.append(
            {
                "session": session,
                "user": user,
                "content": content,
                "filename": filename,
            }
        )
        return SimpleNamespace(
            store_name="АТБ",
            converted_amount="100.00",
            base_currency="UAH",
            ocr_confidence=0.95,
            items=[],
        )


class FakeUserRepo:
    async def get_or_create(self, **kwargs):
        return SimpleNamespace(id=1, base_currency="UAH", **kwargs)

    async def by_telegram_id(self, telegram_id: int):
        return None


class FakeContainer:
    def __init__(self, receipt_service: FakeReceiptService) -> None:
        self.settings = SimpleNamespace(default_language="uk", default_currency="UAH")
        self._receipt_service = receipt_service

    def user_repo(self, session):
        return FakeUserRepo()

    def receipt_service(self, session):
        return self._receipt_service


@pytest.mark.asyncio
async def test_photo_in_budget_state_processed_as_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    receipt_service = FakeReceiptService()
    dispatcher = create_dispatcher(FakeContainer(receipt_service))
    bot = Bot("42:TEST")
    sent_texts: list[str] = []

    async def fake_get_file(file_id: str):
        return SimpleNamespace(file_path="photos/receipt.jpg")

    async def fake_download_file(file_path: str):
        return BytesIO(b"image-bytes")

    async def fake_call(self, request, timeout=None):
        if isinstance(request, SendMessage):
            sent_texts.append(request.text)
        return None

    monkeypatch.setattr(bot, "get_file", fake_get_file)
    monkeypatch.setattr(bot, "download_file", fake_download_file)
    monkeypatch.setattr(Bot, "__call__", fake_call)
    monkeypatch.setattr(db, "SessionLocal", lambda: FakeSession())

    state = await dispatcher.fsm.get_context(bot=bot, chat_id=100, user_id=100)
    await state.set_state(BudgetStates.waiting_amount)

    update = Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "date": 1773201600,
                "chat": {"id": 100, "type": "private"},
                "from": {
                    "id": 100,
                    "is_bot": False,
                    "first_name": "User",
                    "language_code": "ru",
                },
                "photo": [
                    {
                        "file_id": "photo-file-id",
                        "file_unique_id": "photo-unique-id",
                        "width": 1000,
                        "height": 1000,
                        "file_size": 12345,
                    }
                ],
            },
        },
        context={"bot": bot},
    )

    await dispatcher.feed_update(bot, update)

    assert [call["filename"] for call in receipt_service.calls] == ["receipt.jpg"]
    assert sent_texts[:2] == [
        "Обрабатываю чек...",
        "Чек сохранен.\nАТБ\n100.00 UAH\nOCR confidence: 95%\nПозиции не распознаны.",
    ]
    assert all(text != "Не удалось распознать сумму." for text in sent_texts)
    assert await state.get_state() is None
