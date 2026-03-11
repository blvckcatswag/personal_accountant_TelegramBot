from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import (
    Budget,
    Category,
    CurrencyRate,
    Notification,
    Receipt,
    ReceiptItem,
    User,
    UserCategoryRule,
)


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(
        self,
        telegram_id: int,
        username: str | None,
        language: str,
        currency: str,
    ) -> User:
        result = await self.session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user:
            if username is not None:
                user.username = username
            return user
        user = User(
            telegram_id=telegram_id,
            username=username,
            language=language,
            base_currency=currency,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def by_telegram_id(self, telegram_id: int) -> User | None:
        result = await self.session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalar_one_or_none()

    async def delete(self, user: User) -> None:
        await self.session.delete(user)


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> list[Category]:
        result = await self.session.execute(select(Category).order_by(Category.name))
        return list(result.scalars().all())

    async def by_name(self, name: str) -> Category | None:
        result = await self.session.execute(
            select(Category).where(func.lower(Category.name) == name.lower())
        )
        return result.scalar_one_or_none()

    async def ensure_many(self, categories: Iterable[dict[str, str]]) -> None:
        existing = {category.name for category in await self.list_all()}
        for category in categories:
            if category["name"] in existing:
                continue
            self.session.add(Category(**category))
        await self.session.flush()


class UserCategoryRuleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(self, user_id: int) -> list[UserCategoryRule]:
        result = await self.session.execute(
            select(UserCategoryRule)
            .where(UserCategoryRule.user_id == user_id)
            .order_by(UserCategoryRule.priority)
        )
        return list(result.scalars().all())

    async def create(
        self,
        user_id: int,
        pattern: str,
        category_id: int,
        priority: int = 100,
    ) -> None:
        self.session.add(
            UserCategoryRule(
                user_id=user_id,
                pattern=pattern.lower().strip(),
                category_id=category_id,
                priority=priority,
            )
        )
        await self.session.flush()


class ReceiptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_duplicate(self, user_id: int, receipt_hash: str) -> Receipt | None:
        result = await self.session.execute(
            select(Receipt).where(Receipt.user_id == user_id, Receipt.receipt_hash == receipt_hash)
        )
        return result.scalar_one_or_none()

    async def create_with_items(
        self,
        *,
        user_id: int,
        store_name: str,
        store_inn: str | None,
        receipt_date: datetime,
        total_amount: Decimal,
        currency: str,
        base_currency: str,
        converted_amount: Decimal,
        exchange_rate: Decimal,
        ocr_confidence: float,
        image_key: str | None,
        raw_ocr_json: dict,
        receipt_hash: str,
        items: list[dict],
    ) -> Receipt:
        receipt = Receipt(
            user_id=user_id,
            store_name=store_name,
            store_inn=store_inn,
            receipt_date=receipt_date,
            total_amount=total_amount,
            currency=currency,
            base_currency=base_currency,
            converted_amount=converted_amount,
            exchange_rate=exchange_rate,
            ocr_confidence=ocr_confidence,
            image_key=image_key,
            raw_ocr_json=raw_ocr_json,
            receipt_hash=receipt_hash,
        )
        self.session.add(receipt)
        await self.session.flush()
        for item in items:
            self.session.add(ReceiptItem(receipt_id=receipt.id, **item))
        await self.session.flush()
        return receipt

    async def latest_for_user(self, user_id: int, limit: int = 10) -> list[Receipt]:
        result = await self.session.execute(
            select(Receipt)
            .where(Receipt.user_id == user_id)
            .options(selectinload(Receipt.items).selectinload(ReceiptItem.category))
            .order_by(Receipt.receipt_date.desc())
            .limit(limit)
        )
        return list(result.scalars().unique().all())

    async def by_id_for_user(self, receipt_id: str, user_id: int) -> Receipt | None:
        result = await self.session.execute(
            select(Receipt)
            .where(Receipt.id == receipt_id, Receipt.user_id == user_id)
            .options(selectinload(Receipt.items).selectinload(ReceiptItem.category))
        )
        return result.scalar_one_or_none()

    async def list_for_period(
        self,
        user_id: int,
        starts_at: datetime,
        ends_at: datetime,
    ) -> list[Receipt]:
        result = await self.session.execute(
            select(Receipt)
            .where(
                Receipt.user_id == user_id,
                Receipt.receipt_date >= starts_at,
                Receipt.receipt_date <= ends_at,
            )
            .options(selectinload(Receipt.items).selectinload(ReceiptItem.category))
            .order_by(Receipt.receipt_date.desc())
        )
        return list(result.scalars().unique().all())


class BudgetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: int,
        period: str,
        amount: Decimal,
        starts_at: date,
        ends_at: date,
        category_id: int | None = None,
        notify_at_percent: int = 80,
        carryover_enabled: bool = False,
    ) -> Budget:
        budget = Budget(
            user_id=user_id,
            period=period,
            amount=amount,
            starts_at=starts_at,
            ends_at=ends_at,
            category_id=category_id,
            notify_at_percent=notify_at_percent,
            carryover_enabled=carryover_enabled,
        )
        self.session.add(budget)
        await self.session.flush()
        return budget

    async def list_active(self, user_id: int, current_date: date) -> list[Budget]:
        result = await self.session.execute(
            select(Budget)
            .where(
                Budget.user_id == user_id,
                Budget.starts_at <= current_date,
                Budget.ends_at >= current_date,
            )
            .options(selectinload(Budget.category))
        )
        return list(result.scalars().all())


class CurrencyRateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_rate(
        self,
        from_currency: str,
        to_currency: str,
        rate_date: date,
    ) -> CurrencyRate | None:
        result = await self.session.execute(
            select(CurrencyRate).where(
                CurrencyRate.from_currency == from_currency,
                CurrencyRate.to_currency == to_currency,
                CurrencyRate.rate_date == rate_date,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_rate(
        self,
        *,
        from_currency: str,
        to_currency: str,
        rate: Decimal,
        rate_date: date,
        source: str,
    ) -> CurrencyRate:
        existing = await self.get_rate(from_currency, to_currency, rate_date)
        if existing:
            existing.rate = rate
            existing.source = source
            return existing
        entity = CurrencyRate(
            from_currency=from_currency,
            to_currency=to_currency,
            rate=rate,
            rate_date=rate_date,
            source=source,
        )
        self.session.add(entity)
        await self.session.flush()
        return entity


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(
        self,
        user_id: int,
        notification_type: str,
        payload: dict,
    ) -> Notification:
        entity = Notification(user_id=user_id, type=notification_type, payload=payload)
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def purge_user(self, user_id: int) -> None:
        await self.session.execute(delete(Notification).where(Notification.user_id == user_id))
