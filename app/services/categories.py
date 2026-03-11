from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from app.repositories import CategoryRepository, UserCategoryRuleRepository

if TYPE_CHECKING:
    from app.db import Category


DEFAULT_CATEGORIES: list[dict[str, str]] = [
    {"name": "Молочные продукты", "icon": "🥛", "color": "#8ecae6"},
    {"name": "Мясо и птица", "icon": "🥩", "color": "#b23a48"},
    {"name": "Рыба и морепродукты", "icon": "🐟", "color": "#4d96ff"},
    {"name": "Фрукты и овощи", "icon": "🥦", "color": "#52b788"},
    {"name": "Бакалея", "icon": "🌾", "color": "#d4a373"},
    {"name": "Хлеб и выпечка", "icon": "🍞", "color": "#f4a261"},
    {"name": "Напитки", "icon": "🥤", "color": "#457b9d"},
    {"name": "Бытовая химия", "icon": "🧴", "color": "#577590"},
    {"name": "Личная гигиена", "icon": "🧼", "color": "#8d99ae"},
    {"name": "Готовая еда", "icon": "🍱", "color": "#e76f51"},
    {"name": "Снеки и сладости", "icon": "🍫", "color": "#9d4edd"},
    {"name": "Прочее", "icon": "📦", "color": "#6c757d"},
]

DEFAULT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Молочные продукты": ("молоко", "кефир", "творог", "йогурт", "сыр", "масло"),
    "Мясо и птица": ("говядина", "свинина", "курица", "индейка", "колбаса", "сосиски"),
    "Рыба и морепродукты": ("лосось", "минтай", "креветки", "тунец", "сардина"),
    "Фрукты и овощи": ("яблок", "банан", "помидор", "огур", "картоф", "морковь"),
    "Бакалея": ("круп", "макарон", "мука", "сахар", "соль", "рис", "греч"),
    "Хлеб и выпечка": ("хлеб", "батон", "булка", "печенье", "круассан"),
    "Напитки": ("вода", "сок", "чай", "кофе", "лимонад", "пиво", "вино"),
    "Бытовая химия": ("порошок", "чистящ", "мытья", "дезинф", "ополаскиватель"),
    "Личная гигиена": ("шампун", "мыло", "паста", "дезодорант", "щетка"),
    "Готовая еда": ("пицца", "суши", "салат", "полуфаб", "шаурм"),
    "Снеки и сладости": ("чипс", "конфет", "шоколад", "морож", "печенье"),
}


@dataclass(slots=True)
class CategoryMatch:
    category: Category
    confidence: float


class CategoryService:
    def __init__(
        self,
        category_repo: CategoryRepository,
        rule_repo: UserCategoryRuleRepository,
    ) -> None:
        self.category_repo = category_repo
        self.rule_repo = rule_repo

    async def seed_defaults(self) -> None:
        await self.category_repo.ensure_many(DEFAULT_CATEGORIES)

    async def categorize(self, *, user_id: int, normalized_name: str) -> CategoryMatch:
        categories = await self.category_repo.list_all()
        category_by_name = {category.name: category for category in categories}

        rules = await self.rule_repo.list_for_user(user_id)
        for rule in rules:
            if rule.pattern in normalized_name:
                category = next((item for item in categories if item.id == rule.category_id), None)
                if category is not None:
                    return CategoryMatch(category=category, confidence=0.99)

        for category_name, keywords in DEFAULT_KEYWORDS.items():
            if any(keyword in normalized_name for keyword in keywords):
                return CategoryMatch(category=category_by_name[category_name], confidence=0.92)

        best_name = "Прочее"
        best_score = 0.0
        for category_name, keywords in DEFAULT_KEYWORDS.items():
            for keyword in keywords:
                score = SequenceMatcher(None, normalized_name, keyword).ratio()
                if score > best_score:
                    best_name = category_name
                    best_score = score
        return CategoryMatch(category=category_by_name[best_name], confidence=max(best_score, 0.55))
