from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import ServiceContainer
from app.db import get_session
from app.repositories import ReceiptRepository
from app.schemas import AnalyticsSummary, BudgetProgress, MyDataExport, ReceiptView
from app.services.analytics import AnalyticsService

SESSION_DEP = Depends(get_session)
PERIOD_QUERY = Query(default="month", pattern="^(week|month)$")


def build_api_router(container: ServiceContainer) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/health")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/users/{telegram_id}/receipts", response_model=list[ReceiptView])
    async def list_receipts(
        telegram_id: int,
        session: AsyncSession = SESSION_DEP,
    ) -> list[ReceiptView]:
        user = await container.user_repo(session).by_telegram_id(telegram_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        receipts = await ReceiptRepository(session).latest_for_user(user.id, limit=100)
        return [container.analytics.receipt_to_view(receipt) for receipt in receipts]

    @router.get("/users/{telegram_id}/analytics", response_model=AnalyticsSummary)
    async def analytics(
        telegram_id: int,
        period: str = PERIOD_QUERY,
        session: AsyncSession = SESSION_DEP,
    ) -> AnalyticsSummary:
        user = await container.user_repo(session).by_telegram_id(telegram_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        starts_at, ends_at = AnalyticsService.parse_period(period)
        receipts = await ReceiptRepository(session).list_for_period(user.id, starts_at, ends_at)
        return container.analytics.build_summary(receipts)

    @router.get("/users/{telegram_id}/budgets", response_model=list[BudgetProgress])
    async def budgets(
        telegram_id: int,
        session: AsyncSession = SESSION_DEP,
    ) -> list[BudgetProgress]:
        user = await container.user_repo(session).by_telegram_id(telegram_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        budgets = await container.budget_repo(session).list_active(user.id, date.today())
        receipts = await ReceiptRepository(session).list_for_period(
            user.id,
            datetime.combine(date.today().replace(day=1), datetime.min.time()),
            datetime.utcnow(),
        )
        return [container.budgets.calculate_progress(item, receipts) for item in budgets]

    @router.get("/users/{telegram_id}/mydata", response_model=MyDataExport)
    async def mydata(
        telegram_id: int,
        session: AsyncSession = SESSION_DEP,
    ) -> MyDataExport:
        user = await container.user_repo(session).by_telegram_id(telegram_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        receipts = await ReceiptRepository(session).latest_for_user(user.id, limit=1000)
        budgets = await container.budget_repo(session).list_active(user.id, date.today())
        progress = [container.budgets.calculate_progress(item, receipts) for item in budgets]
        return MyDataExport(
            user_id=user.id,
            telegram_id=user.telegram_id,
            base_currency=user.base_currency,
            receipts=[container.analytics.receipt_to_view(receipt) for receipt in receipts],
            budgets=progress,
        )

    return router
