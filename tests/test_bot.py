from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage
from aiogram.types import Update

from app import db
from app.bot import BudgetStates, ManualExpenseStates, create_dispatcher


class FakeSession:
    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class FakeReceiptService:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.manual_calls: list[dict] = []

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
            id="test-receipt-id",
            store_name="АТБ",
            converted_amount="100.00",
            base_currency="UAH",
            ocr_confidence=0.95,
            items=[],
        )

    async def create_manual_expense(
        self,
        *,
        session,
        user,
        amount,
        description: str | None,
        currency: str,
        items=None,
    ):
        self.manual_calls.append(
            {
                "session": session,
                "user": user,
                "amount": amount,
                "description": description,
                "currency": currency,
                "items": items,
            }
        )
        response_items = items or [
            SimpleNamespace(
                name=description,
                category_name="Транспорт",
                total_price=amount,
                currency=currency,
            )
        ]
        return SimpleNamespace(
            store_name="Ручной расход",
            converted_amount=amount,
            base_currency=currency,
            ocr_confidence=1.0,
            items=response_items,
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
    requests: list[SendMessage] = []

    async def fake_get_file(file_id: str):
        return SimpleNamespace(file_path="photos/receipt.jpg")

    async def fake_download_file(file_path: str):
        return BytesIO(b"image-bytes")

    async def fake_call(self, request, **kwargs):
        if isinstance(request, SendMessage):
            requests.append(request)
        return None

    monkeypatch.setattr(bot, "get_file", fake_get_file)
    monkeypatch.setattr(bot, "download_file", fake_download_file)
    monkeypatch.setattr(Bot, "__call__", fake_call)
    monkeypatch.setattr(db, "SessionLocal", lambda: FakeSession())

    state = dispatcher.fsm.get_context(bot=bot, chat_id=100, user_id=100)
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
    sent_texts = [request.text for request in requests]
    assert any("Обрабатываю чек" in text for text in sent_texts)
    assert any("Чек распознан" in text for text in sent_texts)
    assert all("Не удалось распознать сумму" not in text for text in sent_texts)
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_start_command_shows_reply_keyboard(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatcher = create_dispatcher(FakeContainer(FakeReceiptService()))
    bot = Bot("42:TEST")
    requests: list[SendMessage] = []

    async def fake_call(self, request, **kwargs):
        if isinstance(request, SendMessage):
            requests.append(request)
        return None

    monkeypatch.setattr(Bot, "__call__", fake_call)
    monkeypatch.setattr(db, "SessionLocal", lambda: FakeSession())

    update = Update.model_validate(
        {
            "update_id": 2,
            "message": {
                "message_id": 11,
                "date": 1773201601,
                "chat": {"id": 100, "type": "private"},
                "from": {
                    "id": 100,
                    "is_bot": False,
                    "first_name": "User",
                    "language_code": "ru",
                },
                "text": "/start",
                "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
            },
        },
        context={"bot": bot},
    )

    await dispatcher.feed_update(bot, update)

    assert len(requests) == 1
    request = requests[0]
    assert "личный бухгалтер" in request.text
    assert request.reply_markup is not None
    buttons = [button.text for row in request.reply_markup.keyboard for button in row]
    assert buttons == [
        "Добавить чек",
        "Добавить расход",
        "Добавить доход",
        "История",
        "Статистика",
        "Бюджет",
        "Помощь",
        "Отмена",
    ]


@pytest.mark.asyncio
async def test_manual_expense_flow_saves_expense(monkeypatch: pytest.MonkeyPatch) -> None:
    receipt_service = FakeReceiptService()
    dispatcher = create_dispatcher(FakeContainer(receipt_service))
    bot = Bot("42:TEST")
    requests: list[SendMessage] = []

    async def fake_call(self, request, **kwargs):
        if isinstance(request, SendMessage):
            requests.append(request)
        return None

    monkeypatch.setattr(Bot, "__call__", fake_call)
    monkeypatch.setattr(db, "SessionLocal", lambda: FakeSession())

    updates = [
        {
            "update_id": 3,
            "message": {
                "message_id": 12,
                "date": 1773201602,
                "chat": {"id": 100, "type": "private"},
                "from": {
                    "id": 100,
                    "is_bot": False,
                    "first_name": "User",
                    "language_code": "ru",
                },
                "text": "Добавить расход",
            },
        },
        {
            "update_id": 4,
            "message": {
                "message_id": 13,
                "date": 1773201603,
                "chat": {"id": 100, "type": "private"},
                "from": {
                    "id": 100,
                    "is_bot": False,
                    "first_name": "User",
                    "language_code": "ru",
                },
                "text": "245.90",
            },
        },
        {
            "update_id": 5,
            "message": {
                "message_id": 14,
                "date": 1773201604,
                "chat": {"id": 100, "type": "private"},
                "from": {
                    "id": 100,
                    "is_bot": False,
                    "first_name": "User",
                    "language_code": "ru",
                },
                "text": "Такси домой",
            },
        },
    ]

    for payload in updates:
        update = Update.model_validate(payload, context={"bot": bot})
        await dispatcher.feed_update(bot, update)

    state = dispatcher.fsm.get_context(bot=bot, chat_id=100, user_id=100)
    sent_texts = [request.text for request in requests]

    assert await state.get_state() is None
    assert [call["amount"] for call in receipt_service.manual_calls] == [Decimal("245.90")]
    assert [call["description"] for call in receipt_service.manual_calls] == ["Такси домой"]
    assert any("Введите сумму" in text for text in sent_texts)
    assert any("Введите описание" in text for text in sent_texts)
    assert any("Расход сохранён" in text for text in sent_texts)


@pytest.mark.asyncio
async def test_manual_expense_button_sets_state(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatcher = create_dispatcher(FakeContainer(FakeReceiptService()))
    bot = Bot("42:TEST")

    async def fake_call(self, request, **kwargs):
        return None

    monkeypatch.setattr(Bot, "__call__", fake_call)
    monkeypatch.setattr(db, "SessionLocal", lambda: FakeSession())

    update = Update.model_validate(
        {
            "update_id": 6,
            "message": {
                "message_id": 15,
                "date": 1773201605,
                "chat": {"id": 100, "type": "private"},
                "from": {
                    "id": 100,
                    "is_bot": False,
                    "first_name": "User",
                    "language_code": "ru",
                },
                "text": "Добавить расход",
            },
        },
        context={"bot": bot},
    )

    await dispatcher.feed_update(bot, update)

    state = dispatcher.fsm.get_context(bot=bot, chat_id=100, user_id=100)
    assert await state.get_state() == ManualExpenseStates.waiting_amount.state


@pytest.mark.asyncio
async def test_manual_expense_multiline_input_saves_items(monkeypatch: pytest.MonkeyPatch) -> None:
    receipt_service = FakeReceiptService()
    dispatcher = create_dispatcher(FakeContainer(receipt_service))
    bot = Bot("42:TEST")
    requests: list[SendMessage] = []

    async def fake_call(self, request, **kwargs):
        if isinstance(request, SendMessage):
            requests.append(request)
        return None

    monkeypatch.setattr(Bot, "__call__", fake_call)
    monkeypatch.setattr(db, "SessionLocal", lambda: FakeSession())

    updates = [
        {
            "update_id": 7,
            "message": {
                "message_id": 16,
                "date": 1773201606,
                "chat": {"id": 100, "type": "private"},
                "from": {
                    "id": 100,
                    "is_bot": False,
                    "first_name": "User",
                    "language_code": "ru",
                },
                "text": "Добавить расход",
            },
        },
        {
            "update_id": 8,
            "message": {
                "message_id": 17,
                "date": 1773201607,
                "chat": {"id": 100, "type": "private"},
                "from": {
                    "id": 100,
                    "is_bot": False,
                    "first_name": "User",
                    "language_code": "ru",
                },
                "text": "Молоко - 80\nХлеб - 25",
            },
        },
    ]

    for payload in updates:
        update = Update.model_validate(payload, context={"bot": bot})
        await dispatcher.feed_update(bot, update)

    state = dispatcher.fsm.get_context(bot=bot, chat_id=100, user_id=100)

    assert await state.get_state() is None
    assert [call["amount"] for call in receipt_service.manual_calls] == [Decimal("105")]
    assert receipt_service.manual_calls[0]["items"] is not None
    assert [item.name for item in receipt_service.manual_calls[0]["items"]] == ["Молоко", "Хлеб"]
    assert any("Расход сохранён" in request.text for request in requests)
