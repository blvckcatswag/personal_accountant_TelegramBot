from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from decimal import Decimal

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app import db
from app.container import ServiceContainer
from app.repositories import ReceiptRepository
from app.schemas import DEFAULT_CATEGORY_NAME, ReceiptItemPayload
from app.services.analytics import AnalyticsService
from app.services.budgets import BudgetService
from app.services.receipts import DuplicateReceiptError

logger = logging.getLogger(__name__)

CURRENCY_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")


class BudgetStates(StatesGroup):
    waiting_amount = State()
    waiting_period = State()


class ReceiptConfirmStates(StatesGroup):
    waiting_correction = State()
    waiting_voice_items = State()


class VoiceConfirmStates(StatesGroup):
    waiting_confirm = State()
    waiting_edit = State()


class ManualExpenseStates(StatesGroup):
    waiting_amount = State()
    waiting_description = State()


MANUAL_AMOUNT_ONLY_PATTERN = re.compile(r"^\d+(?:[.,]\d{1,2})?$")
MANUAL_ITEM_PATTERN = re.compile(
    r"^(?P<name>.+?)[\s:=-]+(?P<amount>\d+(?:[.,]\d{1,2})?)\s*"
    r"(?P<currency>грн|uah|usd|eur|pln|rub|₴|\$|€)?$",
    re.IGNORECASE,
)
MANUAL_TOTAL_KEYWORDS = ("итого", "всего", "разом", "сума", "сумма", "всього", "до сплати")

VOICE_KOPECK_PATTERN = re.compile(
    r"(\d+)\s*(?:грив[а-яі]*|грн)\s+(\d{1,2})\s*(?:копе[а-я]*|коп\.?)",
    re.IGNORECASE,
)
VOICE_CURRENCY_PATTERN = re.compile(
    r"(?:грив[а-яі]*|грн|griven[a-z]*|рубл[а-я]+|руб|долларо[а-я]+|евро)",
    re.IGNORECASE,
)
VOICE_CONJUNCTION_PATTERN = re.compile(
    r",?\s+и\s+(?=[А-Яа-яІіЇїЄєҐґA-Za-z])",
    re.IGNORECASE,
)
VOICE_SPLIT_PATTERN = re.compile(
    r"(\d+(?:[.,]\d{1,2})?)\s+(?=[А-Яа-яІіЇїЄєҐґA-Za-z])",
)


def normalize_voice_text(text: str) -> str:
    """Clean currency words, conjunctions and split 'name amount' pairs.

    Converts 'пиво 120грн сухарики 80 грн и молоко 55 грн'
    into     'пиво 120, сухарики 80, молоко 55'

    Also handles 'X гривен Y копеек' → 'X.0Y' decimals.
    """
    # 1. Strip trailing sentence punctuation (Google STT adds periods)
    cleaned = text.rstrip(".!?…")

    # 1.5. Fix Google STT misplaced commas: ", 78" → " 78"
    # (STT puts commas before numbers instead of after; require space
    #  so decimal commas like "70,50" are preserved)
    cleaned = re.sub(r",\s+(\d)", r" \1", cleaned)

    # 2. Merge "X гривен Y копеек" into decimal before stripping currency
    cleaned = VOICE_KOPECK_PATTERN.sub(
        lambda m: f"{m.group(1)}.{m.group(2).zfill(2)}", cleaned,
    )

    # 3. Strip remaining currency words
    cleaned = VOICE_CURRENCY_PATTERN.sub(" ", cleaned)

    # 4. Strip conjunctions ("и" between items)
    cleaned = VOICE_CONJUNCTION_PATTERN.sub(", ", cleaned)

    # 5. Remove punctuation stuck to numbers ("274." → "274")
    cleaned = re.sub(r"(\d)\.(?!\d)", r"\1", cleaned)

    # 6. Normalize commas and whitespace (preserve decimal commas like "70,50")
    cleaned = re.sub(r"(?<!\d)\s*,\s*|\s*,\s+", ", ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # 7. Split items: after a number followed by a letter = new item
    return VOICE_SPLIT_PATTERN.sub(r"\1, ", cleaned)


class DbSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        async with db.SessionLocal() as session:
            data["session"] = session
            return await handler(event, data)


def build_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Добавить чек"),
                KeyboardButton(text="Добавить расход"),
            ],
            [
                KeyboardButton(text="История"),
                KeyboardButton(text="Статистика"),
                KeyboardButton(text="Бюджет"),
            ],
            [
                KeyboardButton(text="Помощь"),
                KeyboardButton(text="Отмена"),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def create_dispatcher(container: ServiceContainer) -> Dispatcher:
    router = Router()
    router.message.middleware(DbSessionMiddleware())
    router.callback_query.middleware(DbSessionMiddleware())
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    async def answer_with_main_menu(message: Message, text: str) -> None:
        await message.answer(text, reply_markup=build_main_keyboard())

    async def get_or_create_user(message: Message, session: AsyncSession):
        return await container.user_repo(session).get_or_create(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            language=message.from_user.language_code or container.settings.default_language,
            currency=container.settings.default_currency,
        )

    def parse_manual_expense_items(
        text: str,
        currency: str,
    ) -> tuple[list[ReceiptItemPayload], Decimal | None]:
        items: list[ReceiptItemPayload] = []
        total_hint: Decimal | None = None
        fragments = [
            fragment.strip()
            for fragment in re.split(r"[\n,;]+", text)
            if fragment.strip()
        ]
        for fragment in fragments:
            match = MANUAL_ITEM_PATTERN.match(fragment)
            if not match:
                continue
            amount = Decimal(match.group("amount").replace(",", "."))
            if amount <= 0:
                continue
            name = match.group("name").strip(" -:=.")
            normalized_name = re.sub(r"\s+", " ", name).strip().lower()
            if len(normalized_name) < 2:
                continue
            if any(keyword in normalized_name for keyword in MANUAL_TOTAL_KEYWORDS):
                total_hint = amount
                continue
            items.append(
                ReceiptItemPayload(
                    name=name,
                    normalized_name=normalized_name,
                    quantity=Decimal("1"),
                    unit="pcs",
                    price_per_unit=amount,
                    total_price=amount,
                    discount=Decimal("0"),
                    currency=currency,
                    category_name=DEFAULT_CATEGORY_NAME,
                    confidence=0.95,
                )
            )
        return items, total_hint

    async def process_receipt_message(
        message: Message, session: AsyncSession, state: FSMContext,
    ) -> None:
        if (message.text or "").startswith("/"):
            return
        if message.photo:
            file = await message.bot.get_file(message.photo[-1].file_id)
            file_buffer = await message.bot.download_file(file.file_path)
            content = file_buffer.read()
            filename = "receipt.jpg"
        elif message.document:
            file = await message.bot.get_file(message.document.file_id)
            file_buffer = await message.bot.download_file(file.file_path)
            content = file_buffer.read()
            filename = message.document.file_name or "receipt.bin"
        else:
            content = (message.text or "").encode("utf-8")
            filename = "receipt.txt"
        await answer_with_main_menu(message, "⏳ Обрабатываю чек...")
        user = await get_or_create_user(message, session)
        service = container.receipt_service(session)
        try:
            receipt = await service.process_upload(
                session=session,
                user=user,
                content=content,
                filename=filename,
            )
            await session.commit()
        except DuplicateReceiptError as exc:
            await answer_with_main_menu(message, f"⚠️ {exc}")
            return
        except Exception:
            logger.exception("Failed to process receipt for user %s", message.from_user.id)
            await session.rollback()
            await answer_with_main_menu(
                message, "❌ Не удалось обработать чек. Попробуйте ещё раз.",
            )
            return
        items_text = "\n".join(
            f"  • {item.name}: {item.total_price} {item.currency}"
            for item in receipt.items[:10]
        )
        confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Всё верно",
                    callback_data=f"receipt_ok:{receipt.id}",
                ),
                InlineKeyboardButton(
                    text="✏️ Исправить сумму",
                    callback_data=f"receipt_fix:{receipt.id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎙️ Надиктовать позиции",
                    callback_data=f"receipt_voice:{receipt.id}",
                ),
            ],
        ])
        await message.answer(
            f"🧾 Чек распознан!\n\n"
            f"🏪 {receipt.store_name}\n"
            f"💰 Сумма: {receipt.converted_amount} {receipt.base_currency}\n"
            f"📊 Уверенность OCR: {receipt.ocr_confidence:.0%}\n\n"
            + (f"📋 Позиции:\n{items_text}" if items_text else "Позиции не распознаны."),
            reply_markup=confirm_kb,
        )

    @router.callback_query(F.data.startswith("receipt_ok:"))
    async def receipt_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
        await callback.answer("✅ Отлично!")
        await callback.message.edit_reply_markup(reply_markup=None)
        await _show_budget_progress(callback.message, session)

    @router.callback_query(F.data.startswith("receipt_fix:"))
    async def receipt_fix(
        callback: CallbackQuery, state: FSMContext, session: AsyncSession,
    ) -> None:
        receipt_id = callback.data.split(":", 1)[1]
        await state.set_state(ReceiptConfirmStates.waiting_correction)
        await state.update_data(fix_receipt_id=receipt_id)
        await callback.answer()
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "✏️ Введите правильную сумму чека:",
            reply_markup=build_main_keyboard(),
        )

    @router.message(
        ReceiptConfirmStates.waiting_correction, F.text & ~F.text.startswith("/"),
    )
    async def receipt_correction(
        message: Message, state: FSMContext, session: AsyncSession,
    ) -> None:
        raw = (message.text or "").strip().replace(",", ".")
        try:
            new_amount = Decimal(raw)
        except Exception:
            await answer_with_main_menu(
                message, "❌ Не удалось распознать сумму. Введите число, например `1398.37`.",
            )
            return
        if new_amount <= 0:
            await answer_with_main_menu(message, "❌ Сумма должна быть больше нуля.")
            return
        data = await state.get_data()
        receipt_id = data.get("fix_receipt_id")
        user = await get_or_create_user(message, session)
        repo = ReceiptRepository(session)
        receipt = await repo.by_id_for_user(receipt_id, user.id)
        if receipt is None:
            await state.clear()
            await answer_with_main_menu(message, "⚠️ Чек не найден.")
            return
        receipt.total_amount = new_amount
        receipt.converted_amount = (new_amount * receipt.exchange_rate).quantize(
            Decimal("0.01"),
        )
        await session.commit()
        await state.clear()
        await answer_with_main_menu(
            message,
            f"✅ Сумма обновлена!\n"
            f"💰 Новая сумма: {receipt.converted_amount} {receipt.base_currency}",
        )

    @router.callback_query(F.data.startswith("receipt_voice:"))
    async def receipt_voice(
        callback: CallbackQuery, state: FSMContext, session: AsyncSession,
    ) -> None:
        receipt_id = callback.data.split(":", 1)[1]
        await state.set_state(ReceiptConfirmStates.waiting_voice_items)
        await state.update_data(voice_receipt_id=receipt_id)
        await callback.answer()
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "🎙️ Отправьте голосовое сообщение с позициями.\n"
            "Формат: «молоко 80 хлеб 30 гречка 70»",
            reply_markup=build_main_keyboard(),
        )

    @router.message(ReceiptConfirmStates.waiting_voice_items, F.voice)
    async def receipt_voice_items(
        message: Message, state: FSMContext, session: AsyncSession,
    ) -> None:
        voice = message.voice
        if voice.duration > 60:
            await answer_with_main_menu(
                message, "⚠️ Слишком длинное (макс. 60 сек). Попробуйте короче!",
            )
            return
        await answer_with_main_menu(message, "🎙️ Распознаю...")
        file = await message.bot.get_file(voice.file_id)
        file_buffer = await message.bot.download_file(file.file_path)
        content = file_buffer.read()
        try:
            speech_engine = container.speech_engine()
            speech_result = await speech_engine.recognize(content)
        except Exception:
            logger.exception("STT failed for user %s", message.from_user.id)
            await answer_with_main_menu(
                message, "❌ Не удалось распознать. Попробуйте ещё раз.",
            )
            return
        if not speech_result.text.strip():
            await answer_with_main_menu(
                message, "🤷 Не удалось разобрать речь. Попробуйте чётче.",
            )
            return
        recognized_text = speech_result.text.strip()
        normalized_text = normalize_voice_text(recognized_text)
        user = await get_or_create_user(message, session)
        items, total_hint = parse_manual_expense_items(
            normalized_text, user.base_currency,
        )
        if not items:
            await answer_with_main_menu(
                message,
                f"🎙️ Распознано:\n«{recognized_text}»\n\n"
                "❌ Не удалось разобрать позиции.\n"
                "Формат: «молоко 80 хлеб 30 гречка 70»",
            )
            return
        data = await state.get_data()
        receipt_id = data.get("voice_receipt_id")
        repo = ReceiptRepository(session)
        receipt = await repo.by_id_for_user(receipt_id, user.id)
        if receipt is None:
            await state.clear()
            await answer_with_main_menu(message, "⚠️ Чек не найден.")
            return
        total_amount = total_hint or sum(
            (item.total_price for item in items), Decimal("0"),
        )
        receipt.total_amount = total_amount
        receipt.converted_amount = (total_amount * receipt.exchange_rate).quantize(
            Decimal("0.01"),
        )
        await session.commit()
        await state.clear()
        preview = "\n".join(
            f"  • {item.name}: {item.total_price} {item.currency}"
            for item in items[:10]
        )
        await answer_with_main_menu(
            message,
            f"🎙️ Позиции обновлены!\n\n"
            f"💰 Сумма: {receipt.converted_amount} {receipt.base_currency}\n\n"
            f"📋 Позиции:\n{preview}",
        )

    @router.message(
        ReceiptConfirmStates.waiting_voice_items, F.text & ~F.text.startswith("/"),
    )
    async def receipt_voice_items_text(
        message: Message, state: FSMContext,
    ) -> None:
        await answer_with_main_menu(
            message,
            "🎙️ Ожидаю голосовое сообщение!\n"
            "Или нажмите /cancel для отмены.",
        )

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        await answer_with_main_menu(
            message,
            "👋 Привет! Я твой личный бухгалтер.\n\n"
            "📸 Отправь фото чека или выбери действие на клавиатуре.\n"
            "Умею сохранять чеки, добавлять расходы вручную, "
            "показывать историю и сводку 📊",
        )

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await answer_with_main_menu(
            message,
            "📖 Что я умею:\n\n"
            "🧾 Добавить чек — фото, документ или текст\n"
            "✍️ Добавить расход — ручной ввод\n"
            "🎙️ Голосовое — надиктуй расходы\n"
            "📊 Статистика — сводка за месяц\n"
            "📜 История — последние чеки\n"
            "💳 Бюджет — установка лимита\n\n"
            "⚙️ Команды:\n"
            "/stats [week|month]\n"
            "/history\n"
            "/budget\n"
            "/delete — удалить последний чек\n"
            "/currency USD\n"
            "/mydata [week|month] — экспорт CSV\n"
            "/cancel",
        )

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await answer_with_main_menu(message, "🚫 Действие отменено.")

    @router.message(Command("currency"))
    async def currency(message: Message, session: AsyncSession) -> None:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await answer_with_main_menu(message, "💱 Укажите валюту: /currency UAH")
            return
        code = parts[1].strip().upper()
        if not CURRENCY_CODE_PATTERN.match(code):
            await answer_with_main_menu(
                message,
                "❌ Некорректный код валюты. Укажите трёхбуквенный код ISO 4217, "
                "например: UAH, USD, EUR.",
            )
            return
        user = await get_or_create_user(message, session)
        user.base_currency = code
        await session.commit()
        await answer_with_main_menu(message, f"✅ Валюта изменена на {code}.")

    @router.message(F.text == "Помощь")
    async def help_button(message: Message, state: FSMContext) -> None:
        await state.clear()
        await help_command(message)

    @router.message(F.text == "Отмена")
    async def cancel_button(message: Message, state: FSMContext) -> None:
        await cancel(message, state)

    @router.message(F.text == "Добавить чек")
    async def add_receipt_button(message: Message, state: FSMContext) -> None:
        await state.clear()
        await answer_with_main_menu(
            message,
            "📸 Отправьте фото, документ или текст чека.\n"
            "Я распознаю сумму и позиции!",
        )

    @router.message(F.text == "Добавить расход")
    async def add_expense_button(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(ManualExpenseStates.waiting_amount)
        await answer_with_main_menu(
            message,
            "✍️ Введите сумму, например `245.90`,\n"
            "или сразу список позиций:\n"
            "`Молоко - 80`\n`Хлеб - 25`",
        )

    @router.message(F.text == "Статистика")
    async def stats_button(message: Message, state: FSMContext, session: AsyncSession) -> None:
        await state.clear()
        await stats(message, session)

    @router.message(F.text == "История")
    async def history_button(message: Message, state: FSMContext, session: AsyncSession) -> None:
        await state.clear()
        await history(message, session)

    @router.message(F.text == "Бюджет")
    async def budget_button(message: Message, state: FSMContext) -> None:
        await state.clear()
        await budget(message, state)

    @router.message(Command("budget"))
    async def budget(message: Message, state: FSMContext) -> None:
        await state.set_state(BudgetStates.waiting_amount)
        await message.answer(
            "💳 Введите сумму бюджета, например `8000`.",
            parse_mode="Markdown",
            reply_markup=build_main_keyboard(),
        )

    @router.message(BudgetStates.waiting_amount, F.text & ~F.text.startswith("/"))
    async def budget_amount(message: Message, state: FSMContext) -> None:
        try:
            amount = Decimal((message.text or "").replace(",", "."))
        except Exception:
            await answer_with_main_menu(message, "❌ Не удалось распознать сумму.")
            return
        await state.update_data(amount=str(amount))
        await state.set_state(BudgetStates.waiting_period)
        await answer_with_main_menu(message, "📅 Введите период: WEEK или MONTH.")

    @router.message(BudgetStates.waiting_period, F.text & ~F.text.startswith("/"))
    async def budget_period(message: Message, state: FSMContext, session: AsyncSession) -> None:
        period = (message.text or "").strip().upper()
        if period not in {"WEEK", "MONTH"}:
            await answer_with_main_menu(message, "❌ Допустимые значения: WEEK или MONTH.")
            return
        data = await state.get_data()
        amount = Decimal(data["amount"])
        starts_at, ends_at = BudgetService.period_bounds(period)
        budget_repo = container.budget_repo(session)
        user = await get_or_create_user(message, session)
        await budget_repo.create(
            user_id=user.id,
            period=period,
            amount=amount,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        await session.commit()
        await state.clear()
        period_label = "неделю" if period == "WEEK" else "месяц"
        await answer_with_main_menu(
            message,
            f"✅ Бюджет установлен!\n"
            f"💰 {amount} {container.settings.default_currency} на {period_label}",
        )

    @router.message(BudgetStates.waiting_amount, F.photo | F.document)
    @router.message(BudgetStates.waiting_period, F.photo | F.document)
    async def budget_media(message: Message, state: FSMContext, session: AsyncSession) -> None:
        await state.clear()
        await process_receipt_message(message, session, state)

    @router.message(ManualExpenseStates.waiting_amount, F.text & ~F.text.startswith("/"))
    async def manual_expense_amount(
        message: Message, state: FSMContext, session: AsyncSession,
    ) -> None:
        raw_text = (message.text or "").strip()
        if not MANUAL_AMOUNT_ONLY_PATTERN.match(raw_text):
            user = await get_or_create_user(message, session)
            items, total_hint = parse_manual_expense_items(raw_text, user.base_currency)
            if items:
                service = container.receipt_service(session)
                try:
                    total_amount = total_hint or sum(
                        (item.total_price for item in items),
                        Decimal("0"),
                    )
                    receipt = await service.create_manual_expense(
                        session=session,
                        user=user,
                        amount=total_amount,
                        description="; ".join(item.name for item in items),
                        currency=user.base_currency,
                        items=items,
                    )
                    await session.commit()
                except Exception:
                    logger.exception(
                        "Failed to save manual expense for user %s", message.from_user.id,
                    )
                    await session.rollback()
                    await answer_with_main_menu(
                        message, "❌ Не удалось сохранить. Попробуйте ещё раз.",
                    )
                    return
                await state.clear()
                preview = "\n".join(
                    f"  • {item.name}: {item.total_price} {item.currency}"
                    for item in receipt.items[:5]
                )
                await answer_with_main_menu(
                    message,
                    f"✅ Расход сохранён!\n"
                    f"💰 {receipt.converted_amount} {receipt.base_currency}\n\n"
                    f"{preview}",
                )
                await _show_budget_progress(message, session)
                return
        try:
            amount = Decimal(raw_text.replace(",", "."))
        except Exception:
            await answer_with_main_menu(
                message,
                "❌ Не удалось распознать сумму. Пример: `245.90` или `Молоко - 80`.",
            )
            return
        if amount <= 0:
            await answer_with_main_menu(message, "❌ Сумма должна быть больше нуля.")
            return
        await state.update_data(amount=str(amount))
        await state.set_state(ManualExpenseStates.waiting_description)
        await answer_with_main_menu(
            message,
            "📝 Введите описание, например `Такси домой` или `Продукты в АТБ`.",
        )

    @router.message(ManualExpenseStates.waiting_description, F.text & ~F.text.startswith("/"))
    async def manual_expense_description(
        message: Message, state: FSMContext, session: AsyncSession,
    ) -> None:
        description = (message.text or "").strip()
        if len(description) < 2:
            await answer_with_main_menu(message, "❌ Описание слишком короткое.")
            return
        data = await state.get_data()
        amount = Decimal(data["amount"])
        user = await get_or_create_user(message, session)
        service = container.receipt_service(session)
        try:
            receipt = await service.create_manual_expense(
                session=session,
                user=user,
                amount=amount,
                description=description,
                currency=user.base_currency,
            )
            await session.commit()
        except Exception:
            logger.exception(
                "Failed to save manual expense for user %s", message.from_user.id,
            )
            await session.rollback()
            await answer_with_main_menu(
                message, "❌ Не удалось сохранить. Попробуйте ещё раз.",
            )
            return
        await state.clear()
        item = receipt.items[0]
        await answer_with_main_menu(
            message,
            f"✅ Расход сохранён!\n"
            f"💰 {receipt.converted_amount} {receipt.base_currency}\n"
            f"📝 {item.name}",
        )
        await _show_budget_progress(message, session)

    @router.message(ManualExpenseStates.waiting_amount, F.photo | F.document)
    @router.message(ManualExpenseStates.waiting_description, F.photo | F.document)
    async def manual_expense_media(
        message: Message, state: FSMContext, session: AsyncSession,
    ) -> None:
        await state.clear()
        await process_receipt_message(message, session, state)

    def _build_voice_preview(
        recognized_text: str, items: list[ReceiptItemPayload], total: Decimal,
        currency: str,
    ) -> tuple[str, InlineKeyboardMarkup]:
        preview = "\n".join(
            f"  • {item.name}: {item.total_price} {item.currency}"
            for item in items[:15]
        )
        text = (
            f"🎙️ Распознано:\n«{recognized_text}»\n\n"
            f"📋 Позиции:\n{preview}\n\n"
            f"💰 Итого: {total} {currency}\n\n"
            "Всё верно?"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Сохранить", callback_data="voice_ok"),
                InlineKeyboardButton(text="✏️ Исправить", callback_data="voice_edit"),
            ],
        ])
        return text, kb

    async def _recognize_voice(
        message: Message,
    ) -> str | None:
        """Run STT and return recognized text, or None on failure."""
        voice = message.voice
        if voice.duration > 60:
            await answer_with_main_menu(
                message,
                "⚠️ Голосовое слишком длинное (макс. 60 сек).\n"
                "Попробуйте короче!",
            )
            return None
        await answer_with_main_menu(message, "🎙️ Распознаю голосовое...")
        file = await message.bot.get_file(voice.file_id)
        file_buffer = await message.bot.download_file(file.file_path)
        content = file_buffer.read()
        try:
            speech_engine = container.speech_engine()
            result = speech_engine.recognize(content)
            if asyncio.iscoroutine(result):
                speech_result = await result
            else:
                speech_result = result
        except Exception:
            logger.exception("STT failed for user %s", message.from_user.id)
            await answer_with_main_menu(
                message, "❌ Не удалось распознать голосовое. Попробуйте ещё раз.",
            )
            return None
        if not speech_result.text.strip():
            await answer_with_main_menu(
                message, "🤷 Не удалось разобрать речь. Попробуйте сказать чётче.",
            )
            return None
        return speech_result.text.strip()

    async def _show_voice_preview(
        message: Message, state: FSMContext, session: AsyncSession,
        recognized_text: str,
    ) -> None:
        """Parse recognized text and show confirmation preview."""
        normalized_text = normalize_voice_text(recognized_text)
        user = await get_or_create_user(message, session)
        items, total_hint = parse_manual_expense_items(
            normalized_text, user.base_currency,
        )
        if not items:
            await answer_with_main_menu(
                message,
                f"🎙️ Распознано:\n«{recognized_text}»\n\n"
                "❌ Не удалось разобрать позиции.\n"
                "Попробуйте формат: «молоко 80, хлеб 30, гречка 70»\n"
                "Или отправьте текст вручную.",
            )
            await state.set_state(VoiceConfirmStates.waiting_edit)
            await state.update_data(voice_recognized="")
            return
        total_amount = total_hint or sum(
            (item.total_price for item in items), Decimal("0"),
        )
        text, kb = _build_voice_preview(
            recognized_text, items, total_amount, user.base_currency,
        )
        await state.set_state(VoiceConfirmStates.waiting_confirm)
        await state.update_data(
            voice_recognized=recognized_text,
            voice_normalized=normalized_text,
            voice_currency=user.base_currency,
        )
        await message.answer(text, reply_markup=kb)

    @router.message(ManualExpenseStates.waiting_amount, F.voice)
    @router.message(ManualExpenseStates.waiting_description, F.voice)
    @router.message(F.voice)
    async def voice_message(
        message: Message, state: FSMContext, session: AsyncSession,
    ) -> None:
        await state.clear()
        recognized_text = await _recognize_voice(message)
        if recognized_text is None:
            return
        await _show_voice_preview(message, state, session, recognized_text)

    @router.callback_query(F.data == "voice_ok")
    async def voice_confirm(
        callback: CallbackQuery, state: FSMContext, session: AsyncSession,
    ) -> None:
        data = await state.get_data()
        normalized_text = data.get("voice_normalized", "")
        currency = data.get("voice_currency", container.settings.default_currency)
        items, total_hint = parse_manual_expense_items(normalized_text, currency)
        if not items:
            await callback.answer("❌ Нет позиций для сохранения.")
            return
        user = await get_or_create_user(callback.message, session)
        service = container.receipt_service(session)
        try:
            total_amount = total_hint or sum(
                (item.total_price for item in items), Decimal("0"),
            )
            receipt = await service.create_manual_expense(
                session=session,
                user=user,
                amount=total_amount,
                description="; ".join(item.name for item in items),
                currency=currency,
                items=items,
            )
            await session.commit()
        except Exception:
            logger.exception(
                "Failed to save voice expense for user %s", callback.from_user.id,
            )
            await session.rollback()
            await callback.answer("❌ Ошибка сохранения.")
            return
        await callback.answer("✅ Сохранено!")
        await callback.message.edit_reply_markup(reply_markup=None)
        preview = "\n".join(
            f"  • {item.name}: {item.total_price} {item.currency}"
            for item in receipt.items[:10]
        )
        await callback.message.answer(
            f"✅ Расход сохранён!\n\n"
            f"💰 Сумма: {receipt.converted_amount} {receipt.base_currency}\n\n"
            f"📋 Позиции:\n{preview}",
            reply_markup=build_main_keyboard(),
        )
        await _show_budget_progress(callback.message, session)
        await state.clear()

    @router.callback_query(F.data == "voice_edit")
    async def voice_edit(
        callback: CallbackQuery, state: FSMContext,
    ) -> None:
        await callback.answer()
        await callback.message.edit_reply_markup(reply_markup=None)
        data = await state.get_data()
        recognized = data.get("voice_recognized", "")
        await state.set_state(VoiceConfirmStates.waiting_edit)
        await callback.message.answer(
            "✏️ Отправьте исправленный текст.\n"
            "Формат: «молоко 80, хлеб 30, гречка 70»\n\n"
            f"Текущий текст:\n«{recognized}»",
            reply_markup=build_main_keyboard(),
        )

    @router.message(
        VoiceConfirmStates.waiting_edit, F.text & ~F.text.startswith("/"),
    )
    async def voice_edit_text(
        message: Message, state: FSMContext, session: AsyncSession,
    ) -> None:
        corrected_text = (message.text or "").strip()
        if len(corrected_text) < 3:
            await answer_with_main_menu(message, "❌ Текст слишком короткий.")
            return
        await _show_voice_preview(message, state, session, corrected_text)

    @router.message(VoiceConfirmStates.waiting_edit, F.voice)
    async def voice_edit_resend(
        message: Message, state: FSMContext, session: AsyncSession,
    ) -> None:
        recognized_text = await _recognize_voice(message)
        if recognized_text is None:
            return
        await _show_voice_preview(message, state, session, recognized_text)

    async def _show_budget_progress(message: Message, session: AsyncSession) -> None:
        """Show budget progress bar after saving an expense."""
        user = await container.user_repo(session).by_telegram_id(message.from_user.id)
        if user is None:
            return
        budgets = await container.budget_repo(session).list_active(user.id, date.today())
        if not budgets:
            return
        repo = ReceiptRepository(session)
        lines: list[str] = []
        for budget in budgets:
            starts_at, ends_at = AnalyticsService.parse_period(
                budget.period.lower() if isinstance(budget.period, str) else budget.period,
            )
            receipts = await repo.list_for_period(user.id, starts_at, ends_at)
            progress = container.budgets.calculate_progress(budget, receipts)
            label = "📅 Неделя" if budget.period in ("WEEK", "week") else "📅 Месяц"
            bar = progress.render_bar
            status = "🔴" if progress.exceeded else "🟢"
            lines.append(
                f"{status} {label}: {progress.spent}/{progress.amount} "
                f"{user.base_currency}\n{bar}",
            )
        if lines:
            await message.answer(
                "💳 Бюджет:\n\n" + "\n\n".join(lines),
                reply_markup=build_main_keyboard(),
            )

    @router.message(Command("stats"))
    async def stats(message: Message, session: AsyncSession) -> None:
        parts = (message.text or "").split(maxsplit=1)
        period = parts[1].strip().lower() if len(parts) > 1 else "month"
        user = await container.user_repo(session).by_telegram_id(message.from_user.id)
        if user is None:
            await answer_with_main_menu(
                message, "🤷 Данных пока нет. Отправьте чек или /start.",
            )
            return
        starts_at, ends_at = AnalyticsService.parse_period(period)
        receipts = await ReceiptRepository(session).list_for_period(user.id, starts_at, ends_at)
        summary = container.analytics.build_summary(receipts)
        period_label = "📅 Неделя" if period == "week" else "📅 Месяц"
        await answer_with_main_menu(
            message,
            f"📊 Статистика\n\n"
            f"{period_label}\n"
            f"🧾 Чеков: {summary.receipt_count}\n"
            f"💰 Потрачено: {summary.total_amount} {user.base_currency}",
        )

    @router.message(Command("history"))
    async def history(message: Message, session: AsyncSession) -> None:
        user = await container.user_repo(session).by_telegram_id(message.from_user.id)
        if user is None:
            await answer_with_main_menu(message, "📜 История пока пуста.")
            return
        receipts = await ReceiptRepository(session).latest_for_user(user.id)
        if not receipts:
            await answer_with_main_menu(message, "📜 История пока пуста.")
            return
        lines: list[str] = []
        for receipt in receipts:
            lines.append(
                f"📌 {receipt.receipt_date:%d.%m.%Y} — {receipt.store_name} — "
                f"{receipt.converted_amount} {receipt.base_currency}"
            )
        delete_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🗑️ Удалить последний",
                callback_data=f"delete_receipt:{receipts[0].id}",
            )],
        ])
        await message.answer(
            "📜 Последние чеки:\n\n" + "\n".join(lines),
            reply_markup=delete_kb,
        )

    @router.callback_query(F.data.startswith("delete_receipt:"))
    async def delete_receipt_callback(
        callback: CallbackQuery, session: AsyncSession,
    ) -> None:
        receipt_id = callback.data.split(":", 1)[1]
        user = await container.user_repo(session).by_telegram_id(callback.from_user.id)
        if user is None:
            await callback.answer("⚠️ Пользователь не найден.")
            return
        repo = ReceiptRepository(session)
        receipt = await repo.by_id_for_user(receipt_id, user.id)
        if receipt is None:
            await callback.answer("⚠️ Чек не найден.")
            return
        amount_info = f"{receipt.converted_amount} {receipt.base_currency}"
        await repo.delete(receipt)
        await session.commit()
        await callback.answer("🗑️ Удалено!")
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"🗑️ Чек удалён ({amount_info}).",
            reply_markup=build_main_keyboard(),
        )

    @router.message(Command("delete"))
    async def delete_last(message: Message, session: AsyncSession) -> None:
        user = await container.user_repo(session).by_telegram_id(message.from_user.id)
        if user is None:
            await answer_with_main_menu(message, "🤷 Нет данных.")
            return
        receipts = await ReceiptRepository(session).latest_for_user(user.id, limit=1)
        if not receipts:
            await answer_with_main_menu(message, "📜 Нечего удалять.")
            return
        receipt = receipts[0]
        confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить",
                    callback_data=f"delete_receipt:{receipt.id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="delete_cancel",
                ),
            ],
        ])
        await message.answer(
            f"🗑️ Удалить последний чек?\n\n"
            f"📌 {receipt.receipt_date:%d.%m.%Y} — {receipt.store_name}\n"
            f"💰 {receipt.converted_amount} {receipt.base_currency}",
            reply_markup=confirm_kb,
        )

    @router.callback_query(F.data == "delete_cancel")
    async def delete_cancel_callback(callback: CallbackQuery) -> None:
        await callback.answer("🚫 Отменено.")
        await callback.message.edit_reply_markup(reply_markup=None)

    @router.message(Command("mydata"))
    async def mydata(message: Message, session: AsyncSession) -> None:
        user = await container.user_repo(session).by_telegram_id(message.from_user.id)
        if user is None:
            await answer_with_main_menu(message, "🤷 Данных для экспорта нет.")
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1:
            period = parts[1].strip().lower()
            starts_at, ends_at = AnalyticsService.parse_period(period)
            receipts = await ReceiptRepository(session).list_for_period(
                user.id, starts_at, ends_at,
            )
            period_label = period
        else:
            receipts = await ReceiptRepository(session).latest_for_user(user.id, limit=1000)
            period_label = "все"
        if not receipts:
            await answer_with_main_menu(message, "📜 Нет данных за этот период.")
            return
        csv_payload = container.analytics.export_csv(receipts)
        document = BufferedInputFile(
            csv_payload.encode("utf-8"), filename=f"mydata_{period_label}.csv",
        )
        await message.answer_document(document, caption=f"📦 Данные ({period_label}).")

    @router.message(Command("deleteaccount"))
    async def delete_account(message: Message, session: AsyncSession) -> None:
        repo = container.user_repo(session)
        user = await repo.by_telegram_id(message.from_user.id)
        if user is None:
            await answer_with_main_menu(message, "🤷 Аккаунт не найден.")
            return
        await repo.delete(user)
        await session.commit()
        await answer_with_main_menu(message, "🗑️ Все данные удалены.")

    @router.message(F.photo | F.document | F.text)
    async def handle_receipt(
        message: Message, session: AsyncSession, state: FSMContext,
    ) -> None:
        await process_receipt_message(message, session, state)

    return dispatcher


async def create_bot(token: str) -> Bot:
    return Bot(token=token)
