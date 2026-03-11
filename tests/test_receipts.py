from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.schemas import ParsedReceipt, ReceiptItemPayload
from app.services.ocr import OCRPayload
from app.services.receipts import ReceiptProcessingService


class FakeReceiptRepo:
    def __init__(self) -> None:
        self.items_payload: list[dict] | None = None

    async def find_duplicate(self, user_id: int, receipt_hash: str):
        return None

    async def create_with_items(self, **kwargs):
        self.items_payload = kwargs["items"]
        return SimpleNamespace(
            id="receipt-1",
            store_name=kwargs["store_name"],
            receipt_date=kwargs["receipt_date"],
            total_amount=kwargs["total_amount"],
            currency=kwargs["currency"],
            converted_amount=kwargs["converted_amount"],
            base_currency=kwargs["base_currency"],
            ocr_confidence=kwargs["ocr_confidence"],
        )


class FakeCategoryService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def categorize(self, *, user_id: int, normalized_name: str):
        self.calls.append(normalized_name)
        return SimpleNamespace(
            category=SimpleNamespace(id=10, name="Мясо и птица"),
            confidence=0.93,
        )


class FakeCurrencyService:
    def detect_currency(self, text: str) -> str:
        return "UAH"

    async def convert(self, amount: Decimal, from_currency: str, to_currency: str, rate_date):
        return amount, Decimal("1")


class FakeStorageService:
    async def save(self, content: bytes, filename: str) -> str:
        return "receipt.jpg"


class FakeOCREngine:
    async def extract(self, content: bytes, filename: str | None = None) -> OCRPayload:
        return OCRPayload(text="mock text", confidence=0.95, meta={"engine": "test"})


class FakeReceiptParser:
    def parse(self, raw_text: str, *, default_currency: str) -> ParsedReceipt:
        return ParsedReceipt(
            store_name="Тестовый магазин",
            store_inn=None,
            receipt_date=datetime(2026, 3, 11, 12, 0),
            total_amount=Decimal("79.50"),
            currency=default_currency,
            confidence=0.7,
            items=[
                ReceiptItemPayload(
                    name="Шампунь для волос",
                    normalized_name="шампунь для волос",
                    quantity=Decimal("1"),
                    unit="pcs",
                    price_per_unit=Decimal("19.90"),
                    total_price=Decimal("19.90"),
                    discount=Decimal("0"),
                    currency=default_currency,
                    category_name="Прочее",
                    confidence=0.65,
                )
            ],
            raw_text=raw_text,
            receipt_hash="receipt-hash",
        )


class FakeSession:
    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_process_upload_does_not_autocategorize_low_confidence_items() -> None:
    receipt_repo = FakeReceiptRepo()
    category_service = FakeCategoryService()
    service = ReceiptProcessingService(
        receipt_repo=receipt_repo,
        category_service=category_service,
        currency_service=FakeCurrencyService(),
        storage_service=FakeStorageService(),
        ocr_engine=FakeOCREngine(),
        receipt_parser=FakeReceiptParser(),
    )

    receipt = await service.process_upload(
        session=FakeSession(),
        user=SimpleNamespace(id=1, base_currency="UAH"),
        content=b"fake-image",
        filename="receipt.jpg",
    )

    assert category_service.calls == []
    assert receipt_repo.items_payload == [
        {
            "name": "Шампунь для волос",
            "normalized_name": "шампунь для волос",
            "category_id": None,
            "quantity": Decimal("1"),
            "unit": "pcs",
            "price_per_unit": Decimal("19.90"),
            "total_price": Decimal("19.90"),
            "discount": Decimal("0"),
            "currency": "UAH",
            "confidence": 0.65,
        }
    ]
    assert receipt.items[0].category_name == "Прочее"
