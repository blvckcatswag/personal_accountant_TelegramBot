from __future__ import annotations

from decimal import Decimal

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message

from app.container import ServiceContainer
from app.repositories import ReceiptRepository
from app.services.analytics import AnalyticsService
from app.services.budgets import BudgetService
from app.services.receipts import DuplicateReceiptError


class BudgetStates(StatesGroup):
    waiting_amount = State()
    waiting_period = State()


def create_dispatcher(container: ServiceContainer) -> Dispatcher:
    router = Router()
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    async def process_receipt_message(message: Message) -> None:
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
        await message.answer("Обрабатываю чек...")
        async for session in _session_scope():
            user_repo = container.user_repo(session)
            user = await user_repo.get_or_create(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                language=message.from_user.language_code or container.settings.default_language,
                currency=container.settings.default_currency,
            )
            service = container.receipt_service(session)
            try:
                receipt = await service.process_upload(
                    session=session,
                    user=user,
                    content=content,
                    filename=filename,
                )
            except DuplicateReceiptError as exc:
                await message.answer(str(exc))
                return
            except Exception as exc:
                await session.rollback()
                await message.answer(f"Не удалось обработать чек: {exc}")
                return
        lines = [
            f"{item.name} -> {item.category_name}: {item.total_price} {item.currency}"
            for item in receipt.items[:10]
        ]
        await message.answer(
            f"Чек сохранен.\n"
            f"{receipt.store_name}\n"
            f"{receipt.converted_amount} {receipt.base_currency}\n"
            f"OCR confidence: {receipt.ocr_confidence:.0%}\n"
            + ("\n".join(lines) if lines else "Позиции не распознаны.")
        )

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        await message.answer(
            "ReceiptBot готов к работе.\n"
            "Отправьте фото или текст чека, затем используйте /stats, /history, /budget, /currency."
        )

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await message.answer(
            "/start - регистрация\n"
            "/stats [week|month] - сводка расходов\n"
            "/history - последние чеки\n"
            "/budget - установить лимит\n"
            "/currency USD - базовая валюта\n"
            "/mydata - экспорт ваших данных\n"
            "/deleteaccount - удалить все данные"
        )

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Текущее действие отменено.")

    @router.message(Command("currency"))
    async def currency(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Укажите валюту: /currency UAH")
            return
        code = parts[1].strip().upper()
        async for session in _session_scope():
            user_repo = container.user_repo(session)
            user = await user_repo.get_or_create(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                language=message.from_user.language_code or container.settings.default_language,
                currency=container.settings.default_currency,
            )
            user.base_currency = code
            await session.commit()
        await message.answer(f"Базовая валюта изменена на {code}.")

    @router.message(Command("budget"))
    async def budget(message: Message, state: FSMContext) -> None:
        await state.set_state(BudgetStates.waiting_amount)
        await message.answer("Введите сумму бюджета, например `8000`.", parse_mode="Markdown")

    @router.message(BudgetStates.waiting_amount, F.text & ~F.text.startswith("/"))
    async def budget_amount(message: Message, state: FSMContext) -> None:
        try:
            amount = Decimal((message.text or "").replace(",", "."))
        except Exception:
            await message.answer("Не удалось распознать сумму.")
            return
        await state.update_data(amount=str(amount))
        await state.set_state(BudgetStates.waiting_period)
        await message.answer("Введите период: WEEK или MONTH.")

    @router.message(BudgetStates.waiting_period, F.text & ~F.text.startswith("/"))
    async def budget_period(message: Message, state: FSMContext) -> None:
        period = (message.text or "").strip().upper()
        if period not in {"WEEK", "MONTH"}:
            await message.answer("Допустимые значения: WEEK или MONTH.")
            return
        data = await state.get_data()
        amount = Decimal(data["amount"])
        starts_at, ends_at = BudgetService.period_bounds(period)
        async for session in _session_scope():
            user_repo = container.user_repo(session)
            budget_repo = container.budget_repo(session)
            user = await user_repo.get_or_create(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                language=message.from_user.language_code or container.settings.default_language,
                currency=container.settings.default_currency,
            )
            await budget_repo.create(
                user_id=user.id,
                period=period,
                amount=amount,
                starts_at=starts_at,
                ends_at=ends_at,
            )
            await session.commit()
        await state.clear()
        await message.answer(
            f"Бюджет {amount} {container.settings.default_currency} "
            f"на {period} сохранен."
        )

    @router.message(BudgetStates.waiting_amount, F.photo | F.document)
    @router.message(BudgetStates.waiting_period, F.photo | F.document)
    async def budget_media(message: Message, state: FSMContext) -> None:
        await state.clear()
        await process_receipt_message(message)

    @router.message(Command("stats"))
    async def stats(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        period = parts[1].strip().lower() if len(parts) > 1 else "month"
        async for session in _session_scope():
            user = await container.user_repo(session).by_telegram_id(message.from_user.id)
            if user is None:
                await message.answer("Сначала отправьте чек или выполните /start.")
                return
            starts_at, ends_at = AnalyticsService.parse_period(period)
            receipts = await ReceiptRepository(session).list_for_period(user.id, starts_at, ends_at)
            summary = container.analytics.build_summary(receipts)
        category_lines = [
            f"{item.category}: {item.total} ({item.percentage:.1f}%)"
            for item in summary.by_category[:5]
        ]
        await message.answer(
            f"Период: {period}\n"
            f"Чеков: {summary.receipt_count}\n"
            f"Сумма: {summary.total_amount} {user.base_currency}\n"
            + ("\n".join(category_lines) if category_lines else "Категории пока отсутствуют.")
        )

    @router.message(Command("history"))
    async def history(message: Message) -> None:
        async for session in _session_scope():
            user = await container.user_repo(session).by_telegram_id(message.from_user.id)
            if user is None:
                await message.answer("История пока пуста.")
                return
            receipts = await ReceiptRepository(session).latest_for_user(user.id)
        if not receipts:
            await message.answer("История пока пуста.")
            return
        lines = [
            f"{receipt.receipt_date:%d.%m.%Y} | {receipt.store_name} | "
            f"{receipt.converted_amount} {receipt.base_currency}"
            for receipt in receipts
        ]
        await message.answer("\n".join(lines))

    @router.message(Command("mydata"))
    async def mydata(message: Message) -> None:
        async for session in _session_scope():
            user = await container.user_repo(session).by_telegram_id(message.from_user.id)
            if user is None:
                await message.answer("Данных для экспорта нет.")
                return
            receipts = await ReceiptRepository(session).latest_for_user(user.id, limit=1000)
            csv_payload = container.analytics.export_csv(receipts)
        document = BufferedInputFile(csv_payload.encode("utf-8"), filename="mydata.csv")
        await message.answer_document(document, caption="Экспорт данных пользователя.")

    @router.message(Command("deleteaccount"))
    async def delete_account(message: Message) -> None:
        async for session in _session_scope():
            repo = container.user_repo(session)
            user = await repo.by_telegram_id(message.from_user.id)
            if user is None:
                await message.answer("Аккаунт не найден.")
                return
            await repo.delete(user)
            await session.commit()
        await message.answer("Все данные удалены.")

    @router.message(F.photo | F.document | F.text)
    async def handle_receipt(message: Message) -> None:
        await process_receipt_message(message)

    async def _session_scope():
        from app.db import SessionLocal

        async with SessionLocal() as session:
            yield session

    return dispatcher


async def create_bot(token: str) -> Bot:
    return Bot(token=token)
