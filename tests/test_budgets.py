from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.budgets import BudgetService


def test_budget_progress_for_total_budget() -> None:
    budget = SimpleNamespace(
        id="budget-1",
        amount=Decimal("1000"),
        category_id=None,
        starts_at=date(2026, 3, 1),
        ends_at=date(2026, 3, 31),
        category=None,
    )
    receipts = [
        SimpleNamespace(converted_amount=Decimal("400"), items=[]),
        SimpleNamespace(converted_amount=Decimal("250"), items=[]),
    ]

    progress = BudgetService.calculate_progress(budget, receipts)

    assert progress.spent == Decimal("650.00")
    assert progress.percentage == 65.0
    assert progress.render_bar.startswith("[######")

