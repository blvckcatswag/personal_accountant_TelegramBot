from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from app.schemas import BudgetPeriod, BudgetProgress

if TYPE_CHECKING:
    from app.db import Budget, Receipt


def render_progress_bar(percentage: float, width: int = 10) -> str:
    filled = min(width, int(round(width * min(percentage, 100) / 100)))
    return f"[{'#' * filled}{'-' * (width - filled)}] {percentage:.1f}%"


@dataclass(slots=True)
class BudgetAlert:
    threshold: int
    spent: Decimal
    budget_amount: Decimal
    percentage: float


class BudgetService:
    @staticmethod
    def period_bounds(period: str, reference: date | None = None) -> tuple[date, date]:
        current = reference or date.today()
        if period == BudgetPeriod.WEEK.value:
            start = current - timedelta(days=current.weekday())
            end = start + timedelta(days=6)
            return start, end
        start = current.replace(day=1)
        if start.month == 12:
            next_month = start.replace(year=start.year + 1, month=1, day=1)
        else:
            next_month = start.replace(month=start.month + 1, day=1)
        end = next_month - timedelta(days=1)
        return start, end

    @staticmethod
    def calculate_progress(budget: Budget, receipts: list[Receipt]) -> BudgetProgress:
        spent = Decimal("0")
        for receipt in receipts:
            if budget.category_id is None:
                spent += receipt.converted_amount
                continue
            for item in receipt.items:
                if item.category_id == budget.category_id:
                    spent += item.total_price
        percentage = float((spent / budget.amount) * 100) if budget.amount else 0.0
        return BudgetProgress(
            budget_id=budget.id,
            amount=budget.amount,
            spent=spent.quantize(Decimal("0.01")),
            percentage=percentage,
            exceeded=spent > budget.amount,
            starts_at=budget.starts_at,
            ends_at=budget.ends_at,
            category_name=budget.category.name if budget.category else None,
            render_bar=render_progress_bar(percentage),
        )

    @staticmethod
    def check_thresholds(progress: BudgetProgress, notify_at_percent: int) -> list[BudgetAlert]:
        alerts: list[BudgetAlert] = []
        for threshold in (notify_at_percent, 100):
            if progress.percentage >= threshold:
                alerts.append(
                    BudgetAlert(
                        threshold=threshold,
                        spent=progress.spent,
                        budget_amount=progress.amount,
                        percentage=progress.percentage,
                    )
                )
        return alerts
