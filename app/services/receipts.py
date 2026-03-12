from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from hashlib import sha256

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import User
from app.repositories import ReceiptRepository
from app.schemas import DEFAULT_CATEGORY_NAME, ReceiptItemPayload, ReceiptView
from app.services.categories import CategoryService
from app.services.currency import CurrencyService
from app.services.ocr import OCREngine, ReceiptParser, normalize_item_name
from app.services.storage import StorageService, validate_upload


class DuplicateReceiptError(ValueError):
    pass



class ReceiptProcessingService:
    def __init__(
        self,
        *,
        receipt_repo: ReceiptRepository,
        category_service: CategoryService,
        currency_service: CurrencyService,
        storage_service: StorageService,
        ocr_engine: OCREngine,
        receipt_parser: ReceiptParser,
    ) -> None:
        self.receipt_repo = receipt_repo
        self.category_service = category_service
        self.currency_service = currency_service
        self.storage_service = storage_service
        self.ocr_engine = ocr_engine
        self.receipt_parser = receipt_parser

    async def create_manual_expense(
        self,
        *,
        session: AsyncSession,
        user: User,
        amount: Decimal | None,
        description: str | None,
        currency: str,
        items: list[ReceiptItemPayload] | None = None,
    ) -> ReceiptView:
        description = (description or "").strip()
        manual_items = items or []
        if not manual_items and not description:
            raise ValueError("Укажите описание расхода.")

        receipt_date = datetime.utcnow()
        if not manual_items:
            if amount is None:
                raise ValueError("Укажите сумму расхода.")
            normalized_name = normalize_item_name(description)
            manual_items = [
                ReceiptItemPayload(
                    name=description,
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
            ]

        total_amount = amount if amount is not None else sum(
            (item.total_price for item in manual_items),
            Decimal("0"),
        )
        if total_amount <= 0:
            raise ValueError("Сумма расхода должна быть больше нуля.")

        items_payload: list[dict] = []
        response_items: list[ReceiptItemPayload] = []
        for item in manual_items:
            normalized_name = item.normalized_name or normalize_item_name(item.name)
            category_match = await self.category_service.categorize(
                user_id=user.id,
                normalized_name=normalized_name,
            )
            item_payload = ReceiptItemPayload(
                name=item.name,
                normalized_name=normalized_name,
                quantity=item.quantity,
                unit=item.unit,
                price_per_unit=item.price_per_unit,
                total_price=item.total_price,
                discount=item.discount,
                currency=item.currency,
                category_name=category_match.category.name,
                confidence=max(item.confidence, category_match.confidence),
            )
            response_items.append(item_payload)
            items_payload.append(
                {
                    "name": item_payload.name,
                    "normalized_name": item_payload.normalized_name,
                    "category_id": category_match.category.id,
                    "quantity": item_payload.quantity,
                    "unit": item_payload.unit,
                    "price_per_unit": item_payload.price_per_unit,
                    "total_price": item_payload.total_price,
                    "discount": item_payload.discount,
                    "currency": item_payload.currency,
                    "confidence": item_payload.confidence,
                }
            )

        converted_amount, rate = await self.currency_service.convert(
            total_amount,
            currency,
            user.base_currency,
            receipt_date.date(),
        )
        receipt_hash = sha256(
            f"manual|{user.id}|{receipt_date.isoformat()}|{total_amount}|"
            f"{description or '|'.join(item.name for item in response_items)}".encode()
        ).hexdigest()
        receipt = await self.receipt_repo.create_with_items(
            user_id=user.id,
            store_name="Ручной расход",
            store_inn=None,
            receipt_date=receipt_date,
            total_amount=total_amount,
            currency=currency,
            base_currency=user.base_currency,
            converted_amount=converted_amount,
            exchange_rate=rate,
            ocr_confidence=1.0,
            image_key=None,
            raw_ocr_json={
                "source": "manual",
                "description": description,
                "items": [item.model_dump(mode="json") for item in response_items],
            },
            receipt_hash=receipt_hash,
            items=items_payload,
        )
        return ReceiptView(
            id=receipt.id,
            store_name=receipt.store_name,
            receipt_date=receipt.receipt_date,
            total_amount=receipt.total_amount,
            currency=receipt.currency,
            converted_amount=receipt.converted_amount,
            base_currency=receipt.base_currency,
            ocr_confidence=receipt.ocr_confidence,
            items=response_items,
        )

    async def process_upload(
        self,
        *,
        session: AsyncSession,
        user: User,
        content: bytes,
        filename: str,
    ):
        validate_upload(content, filename)
        image_key = await self.storage_service.save(content, filename)
        ocr_payload = await self.ocr_engine.extract(content, filename)
        detected_currency = self.currency_service.detect_currency(ocr_payload.text)
        parsed = self.receipt_parser.parse(ocr_payload.text, default_currency=detected_currency)
        duplicate = await self.receipt_repo.find_duplicate(user.id, parsed.receipt_hash)
        if duplicate is not None:
            raise DuplicateReceiptError("Этот чек уже был добавлен ранее")
        converted_amount, rate = await self.currency_service.convert(
            parsed.total_amount,
            parsed.currency,
            user.base_currency,
            parsed.receipt_date.date(),
        )
        items_payload: list[dict] = []
        for item in parsed.items:
            items_payload.append(
                {
                    "name": item.name,
                    "normalized_name": item.normalized_name,
                    "category_id": None,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "price_per_unit": item.price_per_unit,
                    "total_price": item.total_price,
                    "discount": item.discount,
                    "currency": item.currency,
                    "confidence": item.confidence,
                }
            )
        receipt = await self.receipt_repo.create_with_items(
            user_id=user.id,
            store_name=parsed.store_name,
            store_inn=parsed.store_inn,
            receipt_date=parsed.receipt_date,
            total_amount=parsed.total_amount,
            currency=parsed.currency,
            base_currency=user.base_currency,
            converted_amount=converted_amount,
            exchange_rate=rate,
            ocr_confidence=max(ocr_payload.confidence, parsed.confidence),
            image_key=image_key,
            raw_ocr_json={"text": ocr_payload.text, "meta": ocr_payload.meta},
            receipt_hash=parsed.receipt_hash,
            items=items_payload,
        )
        return ReceiptView(
            id=receipt.id,
            store_name=receipt.store_name,
            receipt_date=receipt.receipt_date,
            total_amount=receipt.total_amount,
            currency=receipt.currency,
            converted_amount=receipt.converted_amount,
            base_currency=receipt.base_currency,
            ocr_confidence=receipt.ocr_confidence,
            items=parsed.items,
        )
