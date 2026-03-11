from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

try:
    import httpx
except ModuleNotFoundError:
    httpx = None

if TYPE_CHECKING:
    from app.repositories import CurrencyRateRepository


STATIC_RATES: dict[tuple[str, str], Decimal] = {
    ("UAH", "USD"): Decimal("0.024"),
    ("USD", "UAH"): Decimal("41.500000"),
    ("UAH", "EUR"): Decimal("0.022"),
    ("EUR", "UAH"): Decimal("45.600000"),
    ("UAH", "PLN"): Decimal("0.095"),
    ("PLN", "UAH"): Decimal("10.500000"),
    ("UAH", "RUB"): Decimal("2.250000"),
    ("RUB", "UAH"): Decimal("0.444444"),
}


class RateProvider(Protocol):
    source_name: str

    async def get_rate(self, from_currency: str, to_currency: str, rate_date: date) -> Decimal | None:
        ...


class StaticRateProvider:
    source_name = "STATIC"

    async def get_rate(self, from_currency: str, to_currency: str, rate_date: date) -> Decimal | None:
        if from_currency == to_currency:
            return Decimal("1")
        return STATIC_RATES.get((from_currency, to_currency))


class ExchangeRateApiProvider:
    source_name = "ExchangeRate-API"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def get_rate(self, from_currency: str, to_currency: str, rate_date: date) -> Decimal | None:
        if not self.api_key or httpx is None:
            return None
        url = f"https://v6.exchangerate-api.com/v6/{self.api_key}/history/{from_currency}/{rate_date.isoformat()}"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
        rates = data.get("conversion_rates", {})
        if to_currency not in rates:
            return None
        return Decimal(str(rates[to_currency]))


class NBURateProvider:
    source_name = "NBU"

    async def get_rate(self, from_currency: str, to_currency: str, rate_date: date) -> Decimal | None:
        if "UAH" not in {from_currency, to_currency} or httpx is None:
            return None
        target = to_currency if from_currency == "UAH" else from_currency
        url = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange"
        params = {"valcode": target, "date": rate_date.strftime("%Y%m%d"), "json": ""}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        if not payload:
            return None
        rate = Decimal(str(payload[0]["rate"]))
        if from_currency == "UAH":
            return Decimal("1") / rate
        return rate


class CurrencyService:
    def __init__(self, rate_repo: "CurrencyRateRepository", providers: list[RateProvider]) -> None:
        self.rate_repo = rate_repo
        self.providers = providers

    async def get_rate(self, from_currency: str, to_currency: str, rate_date: date) -> Decimal:
        if from_currency == to_currency:
            return Decimal("1")
        cached = await self.rate_repo.get_rate(from_currency, to_currency, rate_date)
        if cached is not None:
            return cached.rate
        for provider in self.providers:
            rate = await provider.get_rate(from_currency, to_currency, rate_date)
            if rate is None:
                continue
            await self.rate_repo.upsert_rate(
                from_currency=from_currency,
                to_currency=to_currency,
                rate=rate,
                rate_date=rate_date,
                source=provider.source_name,
            )
            return rate
        fallback = await StaticRateProvider().get_rate(from_currency, to_currency, rate_date)
        if fallback is None:
            raise ValueError(f"Rate {from_currency}->{to_currency} is unavailable")
        await self.rate_repo.upsert_rate(
            from_currency=from_currency,
            to_currency=to_currency,
            rate=fallback,
            rate_date=rate_date,
            source=StaticRateProvider.source_name,
        )
        return fallback

    async def convert(
        self, amount: Decimal, from_currency: str, to_currency: str, rate_date: date
    ) -> tuple[Decimal, Decimal]:
        rate = await self.get_rate(from_currency, to_currency, rate_date)
        converted = (amount * rate).quantize(Decimal("0.01"))
        return converted, rate

    @staticmethod
    def detect_currency(text: str) -> str:
        normalized = text.upper()
        if "ZL" in normalized or "PLN" in normalized or "ZŁ" in normalized:
            return "PLN"
        if "€" in text or "EUR" in normalized:
            return "EUR"
        if "$" in text or "USD" in normalized:
            return "USD"
        if "₽" in text or "RUB" in normalized:
            return "RUB"
        return "UAH"
