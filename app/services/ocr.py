from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Protocol

from app.schemas import ParsedReceipt, ReceiptItemPayload


def normalize_item_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яІіЇїЄєҐґ\s]", " ", value, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


@dataclass(slots=True)
class OCRPayload:
    text: str
    confidence: float
    meta: dict


class OCREngine(Protocol):
    async def extract(self, content: bytes, filename: str | None = None) -> OCRPayload:
        ...


class MockOCREngine:
    async def extract(self, content: bytes, filename: str | None = None) -> OCRPayload:
        text = content.decode("utf-8", errors="ignore")
        confidence = 0.95 if text.strip() else 0.4
        return OCRPayload(text=text, confidence=confidence, meta={"engine": "mock"})


class GoogleVisionOCREngine:
    def __init__(
        self,
        credentials_file: str | None = None,
        credentials_json: str | None = None,
    ) -> None:
        self.credentials_file = credentials_file
        self.credentials_json = credentials_json

    async def extract(self, content: bytes, filename: str | None = None) -> OCRPayload:
        return await asyncio.to_thread(self._extract_sync, content)

    def _extract_sync(self, content: bytes) -> OCRPayload:
        try:
            from google.cloud import vision
            from google.oauth2 import service_account
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Google Vision dependencies are missing. "
                "Run `pip install .[dev]` or `pip install .`."
            ) from exc

        credentials = self._build_credentials(service_account)
        client = vision.ImageAnnotatorClient(credentials=credentials)
        image = vision.Image(content=content)
        response = client.document_text_detection(image=image)
        if response.error.message:
            raise RuntimeError(response.error.message)

        text = response.full_text_annotation.text or ""
        confidence_values: list[float] = []
        for page in response.full_text_annotation.pages:
            for block in page.blocks:
                block_confidence = getattr(block, "confidence", None)
                if block_confidence is not None:
                    confidence_values.append(float(block_confidence))
        confidence = (
            sum(confidence_values) / len(confidence_values)
            if confidence_values
            else (0.9 if text else 0.0)
        )
        return OCRPayload(
            text=text,
            confidence=confidence,
            meta={"engine": "google_vision", "blocks": len(confidence_values)},
        )

    def _build_credentials(self, service_account):
        if self.credentials_json:
            try:
                credentials_info = json.loads(self.credentials_json)
            except json.JSONDecodeError as exc:
                raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON содержит невалидный JSON.") from exc
            return service_account.Credentials.from_service_account_info(credentials_info)
        if self.credentials_file:
            return service_account.Credentials.from_service_account_file(self.credentials_file)
        return None


class ReceiptParser:
    STORE_PATTERN = re.compile(r"(магазин|store|shop)[:\s]+(?P<value>.+)", re.IGNORECASE)
    INN_PATTERN = re.compile(r"(инн|inn)[:\s]+(?P<value>[\w-]+)", re.IGNORECASE)
    TOTAL_PATTERN = re.compile(r"(итого|сумма|total)[^\d]*(?P<value>\d+[.,]\d{2})", re.IGNORECASE)
    DATE_PATTERN = re.compile(
        r"(?P<value>\d{2}[./-]\d{2}[./-]\d{2,4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)"
    )
    ITEM_PATTERN = re.compile(
        r"^(?P<name>.+?)\s{2,}(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>кг|г|л|мл|шт|pcs)?\s+"
        r"(?P<price>\d+(?:[.,]\d{2}))\s+(?P<total>\d+(?:[.,]\d{2}))$",
        re.IGNORECASE,
    )

    def parse(self, raw_text: str, *, default_currency: str) -> ParsedReceipt:
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        store_name = self._parse_store_name(lines)
        store_inn = self._parse_optional(self.INN_PATTERN, lines)
        receipt_date = self._parse_date(lines)
        total_amount = self._parse_total(lines)
        items = self._parse_items(lines, default_currency)
        confidence = self._estimate_confidence(lines, items)
        hash_payload = f"{receipt_date.isoformat()}|{store_inn or store_name}|{total_amount}"
        receipt_hash = sha256(hash_payload.encode()).hexdigest()
        return ParsedReceipt(
            store_name=store_name,
            store_inn=store_inn,
            receipt_date=receipt_date,
            total_amount=total_amount,
            currency=default_currency,
            confidence=confidence,
            items=items,
            raw_text=raw_text,
            receipt_hash=receipt_hash,
        )

    def _parse_store_name(self, lines: list[str]) -> str:
        explicit = self._parse_optional(self.STORE_PATTERN, lines)
        if explicit:
            return explicit
        return lines[0][:255] if lines else "Unknown Store"

    def _parse_optional(self, pattern: re.Pattern[str], lines: list[str]) -> str | None:
        for line in lines:
            match = pattern.search(line)
            if match:
                return match.group("value").strip()
        return None

    def _parse_date(self, lines: list[str]) -> datetime:
        for line in lines:
            match = self.DATE_PATTERN.search(line)
            if match:
                parsed = self._parse_date_value(match.group("value"))
                if parsed is not None:
                    return parsed
        return datetime.utcnow()

    def _parse_date_value(self, value: str) -> datetime | None:
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        if re.match(r"\d{2}[./-]\d{2}[./-]\d{2}\b", value):
            normalized = value.replace("/", ".").replace("-", ".")
            return datetime.strptime(normalized, "%d.%m.%y")
        return None

    def _parse_total(self, lines: list[str]) -> Decimal:
        for line in reversed(lines):
            match = self.TOTAL_PATTERN.search(line)
            if match:
                return Decimal(match.group("value").replace(",", "."))
        values: list[Decimal] = []
        for line in lines:
            tokens = re.findall(r"\d+[.,]\d{2}", line)
            values.extend(Decimal(token.replace(",", ".")) for token in tokens)
        return max(values, default=Decimal("0"))

    def _parse_items(self, lines: list[str], currency: str) -> list[ReceiptItemPayload]:
        items: list[ReceiptItemPayload] = []
        for line in lines:
            match = self.ITEM_PATTERN.match(line)
            if not match:
                continue
            name = match.group("name").strip()
            quantity = Decimal(match.group("qty").replace(",", "."))
            unit = match.group("unit") or "pcs"
            price = Decimal(match.group("price").replace(",", "."))
            total = Decimal(match.group("total").replace(",", "."))
            items.append(
                ReceiptItemPayload(
                    name=name,
                    normalized_name=normalize_item_name(name),
                    quantity=quantity,
                    unit=unit,
                    price_per_unit=price,
                    total_price=total,
                    discount=Decimal("0"),
                    currency=currency,
                    category_name="Прочее",
                    confidence=0.85,
                )
            )
        if items:
            return items
        fallback_items: list[ReceiptItemPayload] = []
        for line in lines:
            tokens = re.findall(r"\d+[.,]\d{2}", line)
            if len(tokens) < 1 or len(line.split()) < 2:
                continue
            total = Decimal(tokens[-1].replace(",", "."))
            name = re.sub(r"\d+[.,]\d{2}", "", line).strip(" -")
            if not name:
                continue
            fallback_items.append(
                ReceiptItemPayload(
                    name=name,
                    normalized_name=normalize_item_name(name),
                    total_price=total,
                    price_per_unit=total,
                    quantity=Decimal("1"),
                    unit="pcs",
                    discount=Decimal("0"),
                    currency=currency,
                    category_name="Прочее",
                    confidence=0.65,
                )
            )
        return fallback_items

    def _estimate_confidence(self, lines: list[str], items: list[ReceiptItemPayload]) -> float:
        score = 0.35
        if self._parse_optional(self.STORE_PATTERN, lines):
            score += 0.2
        if self._parse_optional(self.INN_PATTERN, lines):
            score += 0.1
        if self.DATE_PATTERN.search("\n".join(lines)):
            score += 0.15
        if items:
            score += min(0.2, len(items) * 0.03)
        if self.TOTAL_PATTERN.search("\n".join(lines)):
            score += 0.1
        return min(score, 0.98)
