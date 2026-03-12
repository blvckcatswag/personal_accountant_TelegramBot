from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from app.services.analytics import AnalyticsService


def test_analytics_summary_aggregates_receipts() -> None:
    dairy = SimpleNamespace(name="Молочные продукты")
    item = SimpleNamespace(
        name="Молоко",
        quantity=Decimal("1"),
        unit="pcs",
        total_price=Decimal("90"),
        price_per_unit=Decimal("90"),
        discount=Decimal("0"),
        currency="UAH",
        normalized_name="молоко",
        category=dairy,
        confidence=0.9,
    )
    receipt = SimpleNamespace(
        id="1",
        store_name="АТБ",
        receipt_date=datetime(2026, 3, 11, 12, 0),
        total_amount=Decimal("90"),
        currency="UAH",
        converted_amount=Decimal("90"),
        base_currency="UAH",
        ocr_confidence=0.9,
        exchange_rate=Decimal("1"),
        items=[item],
    )

    summary = AnalyticsService.build_summary([receipt])

    assert summary.total_amount == Decimal("90")
    assert summary.receipt_count == 1
    assert summary.by_category[0].category == "Молочные продукты"

