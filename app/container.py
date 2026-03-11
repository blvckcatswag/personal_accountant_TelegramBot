from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.repositories import (
    BudgetRepository,
    CategoryRepository,
    CurrencyRateRepository,
    NotificationRepository,
    ReceiptRepository,
    UserCategoryRuleRepository,
    UserRepository,
)
from app.services.analytics import AnalyticsService
from app.services.budgets import BudgetService
from app.services.categories import CategoryService
from app.services.currency import CurrencyService, ExchangeRateApiProvider, NBURateProvider, StaticRateProvider
from app.services.ocr import GoogleVisionOCREngine, MockOCREngine, ReceiptParser
from app.services.receipts import ReceiptProcessingService
from app.services.storage import LocalStorageService, S3StorageService


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    analytics: AnalyticsService
    budgets: BudgetService

    @classmethod
    def build(cls) -> "ServiceContainer":
        return cls(settings=get_settings(), analytics=AnalyticsService(), budgets=BudgetService())

    def user_repo(self, session: AsyncSession) -> UserRepository:
        return UserRepository(session)

    def category_repo(self, session: AsyncSession) -> CategoryRepository:
        return CategoryRepository(session)

    def category_service(self, session: AsyncSession) -> CategoryService:
        return CategoryService(self.category_repo(session), UserCategoryRuleRepository(session))

    def budget_repo(self, session: AsyncSession) -> BudgetRepository:
        return BudgetRepository(session)

    def notification_repo(self, session: AsyncSession) -> NotificationRepository:
        return NotificationRepository(session)

    def currency_service(self, session: AsyncSession) -> CurrencyService:
        providers = [
            NBURateProvider(),
            ExchangeRateApiProvider(self.settings.exchangerate_api_key),
            StaticRateProvider(),
        ]
        return CurrencyService(CurrencyRateRepository(session), providers)

    def receipt_service(self, session: AsyncSession) -> ReceiptProcessingService:
        storage = (
            LocalStorageService(self.settings.local_storage_path)
            if self.settings.storage_backend == "local"
            else S3StorageService(self.settings)
        )
        if self.settings.ocr_engine == "google_vision":
            ocr_engine = GoogleVisionOCREngine(self.settings.google_application_credentials or None)
        else:
            ocr_engine = MockOCREngine()
        return ReceiptProcessingService(
            receipt_repo=ReceiptRepository(session),
            category_service=self.category_service(session),
            currency_service=self.currency_service(session),
            storage_service=storage,
            ocr_engine=ocr_engine,
            receipt_parser=ReceiptParser(),
        )
