from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import User
from app.repositories import ReceiptRepository
from app.schemas import ReceiptView
from app.services.categories import CategoryService
from app.services.currency import CurrencyService
from app.services.ocr import OCREngine, ReceiptParser
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
            match = await self.category_service.categorize(
                user_id=user.id, normalized_name=item.normalized_name
            )
            item.category_name = match.category.name
            item.confidence = max(item.confidence, match.confidence)
            items_payload.append(
                {
                    "name": item.name,
                    "normalized_name": item.normalized_name,
                    "category_id": match.category.id,
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
        await session.commit()
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
