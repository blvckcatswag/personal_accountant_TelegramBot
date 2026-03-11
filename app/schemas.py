from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class UserPlan(StrEnum):
    FREE = "FREE"
    PREMIUM = "PREMIUM"


class BudgetPeriod(StrEnum):
    WEEK = "WEEK"
    MONTH = "MONTH"


class NotificationType(StrEnum):
    BUDGET_80 = "BUDGET_80"
    BUDGET_100 = "BUDGET_100"
    WEEKLY_DIGEST = "WEEKLY_DIGEST"
    MONTHLY_REPORT = "MONTHLY_REPORT"
    ANOMALY = "ANOMALY"
    RECEIPT_REMINDER = "RECEIPT_REMINDER"


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class ReceiptItemPayload(BaseModel):
    name: str
    normalized_name: str
    quantity: Decimal = Decimal("1")
    unit: str = "pcs"
    price_per_unit: Decimal = Decimal("0")
    total_price: Decimal = Decimal("0")
    discount: Decimal = Decimal("0")
    currency: str = "UAH"
    category_name: str = "Прочее"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ParsedReceipt(BaseModel):
    store_name: str
    store_inn: str | None = None
    receipt_date: datetime
    total_amount: Decimal
    currency: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    items: list[ReceiptItemPayload]
    raw_text: str
    receipt_hash: str


class ReceiptView(BaseModel):
    id: UUID | str
    store_name: str
    receipt_date: datetime
    total_amount: Decimal
    currency: str
    converted_amount: Decimal
    base_currency: str
    ocr_confidence: float
    items: list[ReceiptItemPayload]


class CategoryBreakdown(BaseModel):
    category: str
    total: Decimal
    percentage: float


class AnalyticsSummary(BaseModel):
    total_amount: Decimal
    receipt_count: int
    by_category: list[CategoryBreakdown]
    by_store: list[dict[str, str | Decimal | int]]


class BudgetProgress(BaseModel):
    budget_id: UUID | str
    amount: Decimal
    spent: Decimal
    percentage: float
    exceeded: bool
    starts_at: date
    ends_at: date
    category_name: str | None = None
    render_bar: str


class MyDataExport(BaseModel):
    user_id: int
    telegram_id: int
    base_currency: str
    receipts: list[ReceiptView]
    budgets: list[BudgetProgress]
