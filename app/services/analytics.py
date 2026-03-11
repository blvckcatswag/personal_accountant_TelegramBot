from __future__ import annotations

from collections import defaultdict
from csv import DictWriter
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from typing import TYPE_CHECKING

from app.schemas import AnalyticsSummary, CategoryBreakdown, ReceiptItemPayload, ReceiptView

if TYPE_CHECKING:
    from app.db import Receipt


class AnalyticsService:
    @staticmethod
    def build_summary(receipts: list[Receipt]) -> AnalyticsSummary:
        total_amount = sum((receipt.converted_amount for receipt in receipts), Decimal("0"))
        by_category_raw: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        by_store_raw: dict[str, dict[str, str | Decimal | int]] = {}

        for receipt in receipts:
            by_store_raw.setdefault(
                receipt.store_name,
                {"store": receipt.store_name, "total": Decimal("0"), "receipts": 0},
            )
            by_store_raw[receipt.store_name]["total"] += receipt.converted_amount
            by_store_raw[receipt.store_name]["receipts"] += 1
            for item in receipt.items:
                category_name = item.category.name if item.category else "Прочее"
                by_category_raw[category_name] += item.total_price

        by_category = [
            CategoryBreakdown(
                category=category,
                total=amount,
                percentage=float((amount / total_amount) * 100) if total_amount else 0.0,
            )
            for category, amount in sorted(by_category_raw.items(), key=lambda entry: entry[1], reverse=True)
        ]
        by_store = sorted(by_store_raw.values(), key=lambda entry: entry["total"], reverse=True)
        return AnalyticsSummary(
            total_amount=total_amount,
            receipt_count=len(receipts),
            by_category=by_category,
            by_store=by_store,
        )

    @staticmethod
    def export_csv(receipts: list[Receipt]) -> str:
        buffer = StringIO()
        writer = DictWriter(
            buffer,
            fieldnames=[
                "receipt_id",
                "store_name",
                "receipt_date",
                "total_amount",
                "currency",
                "converted_amount",
                "base_currency",
                "item_name",
                "category",
                "quantity",
                "unit",
                "item_total",
            ],
        )
        writer.writeheader()
        for receipt in receipts:
            for item in receipt.items:
                writer.writerow(
                    {
                        "receipt_id": receipt.id,
                        "store_name": receipt.store_name,
                        "receipt_date": receipt.receipt_date.isoformat(),
                        "total_amount": str(receipt.total_amount),
                        "currency": receipt.currency,
                        "converted_amount": str(receipt.converted_amount),
                        "base_currency": receipt.base_currency,
                        "item_name": item.name,
                        "category": item.category.name if item.category else "Прочее",
                        "quantity": str(item.quantity),
                        "unit": item.unit,
                        "item_total": str(item.total_price),
                    }
                )
        return buffer.getvalue()

    @staticmethod
    def receipt_to_view(receipt: Receipt) -> ReceiptView:
        return ReceiptView(
            id=receipt.id,
            store_name=receipt.store_name,
            receipt_date=receipt.receipt_date,
            total_amount=receipt.total_amount,
            currency=receipt.currency,
            converted_amount=receipt.converted_amount,
            base_currency=receipt.base_currency,
            ocr_confidence=receipt.ocr_confidence,
            items=[
                ReceiptItemPayload(
                    name=item.name,
                    normalized_name=item.normalized_name,
                    quantity=item.quantity,
                    unit=item.unit,
                    price_per_unit=item.price_per_unit,
                    total_price=item.total_price,
                    discount=item.discount,
                    currency=item.currency,
                    category_name=item.category.name if item.category else "Прочее",
                    confidence=item.confidence,
                )
                for item in receipt.items
            ],
        )

    @staticmethod
    def parse_period(period: str) -> tuple[datetime, datetime]:
        now = datetime.utcnow()
        if period == "week":
            start = now - timedelta(days=now.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            return start, now
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, now
