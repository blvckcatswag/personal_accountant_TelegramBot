from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Protocol

from app.schemas import DEFAULT_CATEGORY_NAME, ParsedReceipt, ReceiptItemPayload

logger = logging.getLogger(__name__)


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
                raise RuntimeError(
                    "GOOGLE_SERVICE_ACCOUNT_JSON содержит невалидный JSON."
                ) from exc
            return service_account.Credentials.from_service_account_info(credentials_info)
        if self.credentials_file:
            return service_account.Credentials.from_service_account_file(self.credentials_file)
        return None


class ReceiptParser:
    STORE_PATTERN = re.compile(r"(магазин|store|shop)[:\s]+(?P<value>.+)", re.IGNORECASE)
    BUSINESS_PATTERN = re.compile(
        r"\b(фоп|тов|пп|ооо|зат|пат|ип|кфг)\b", re.IGNORECASE,
    )
    INN_PATTERN = re.compile(r"(инн|inn)[:\s]+(?P<value>[\w-]+)", re.IGNORECASE)
    TOTAL_PATTERN = re.compile(
        r"(до\s*сплати|до\s*оплати|сума|сумма|разом|итого|всього|підсумок|пидсумок|total)"
        r"[^\d-]*(?P<value>-?\d+[.,]\d{2})",
        re.IGNORECASE,
    )
    DATE_PATTERN = re.compile(
        r"(?P<value>\d{2}[./-]\d{2}[./-]\d{2,4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)"
    )
    MONEY_PATTERN = re.compile(r"-?\d+[.,]\d{2}")
    ITEM_PATTERN = re.compile(
        r"^(?P<name>.+?)\s{2,}(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>кг|г|л|мл|шт|pcs)?\s+"
        r"(?P<price>\d+(?:[.,]\d{2}))\s+(?P<total>\d+(?:[.,]\d{2}))$",
        re.IGNORECASE,
    )
    QTY_LINE_PATTERN = re.compile(
        r"^\s*\d+[.,]?\d*\s*[XxХх*×]\s*\d+[.,]\d{2}\s*$",
    )
    TWO_COLUMN_PATTERN = re.compile(
        r"^(?P<name>.+?)\s{2,}(?P<total>\d+[.,]\d{2})\s*$",
    )
    TOTAL_PRIORITY_KEYWORDS = (
        ("до сплати", "до оплати", "amount due"),
        ("сума", "сумма", "разом", "итого", "total"),
        ("всього", "підсумок", "пидсумок"),
    )
    NON_FINAL_TOTAL_KEYWORDS = (
        "зниж",
        "скид",
        "решта",
        "сдач",
        "готівк",
        "налич",
        "карта",
        "картка",
        "bonus",
        "бонус",
        "доступно",
        "пдв",
        "ндс",
    )
    SERVICE_LINE_KEYWORDS = (
        "сума",
        "сумма",
        "до сплати",
        "до оплати",
        "разом",
        "итого",
        "всього",
        "підсумок",
        "пидсумок",
        "зниж",
        "скид",
        "готівк",
        "налич",
        "решта",
        "сдач",
        "картка",
        "карта",
        "доступно",
        "бонус",
        "пдв",
        "ндс",
        "чек",
        "касир",
        "каса",
        "номер",
        "вн ут номер",
    )
    CURRENCY_ONLY_NAMES = {"грн", "uah", "usd", "eur", "pln", "rub", "uah грн"}

    def parse(self, raw_text: str, *, default_currency: str) -> ParsedReceipt:
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        logger.debug("OCR text (%d lines):\n%s", len(lines), raw_text)
        store_name = self._parse_store_name(lines)
        store_inn = self._parse_optional(self.INN_PATTERN, lines)
        receipt_date = self._parse_date(lines)
        if receipt_date is None:
            logger.warning("Receipt date not found in text, using current time")
            receipt_date = datetime.utcnow()
        total_amount = self._parse_total(lines)
        items = self._parse_items(lines, default_currency)
        confidence = self._estimate_confidence(lines, items)
        hash_payload = (
            f"{receipt_date.isoformat()}|{store_inn or store_name}|{total_amount}"
        )
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
        for line in lines[:10]:
            if self.BUSINESS_PATTERN.search(line):
                return line[:255]
        return lines[0][:255] if lines else "Unknown Store"

    def _parse_optional(
        self, pattern: re.Pattern[str], lines: list[str],
    ) -> str | None:
        for line in lines:
            match = pattern.search(line)
            if match:
                return match.group("value").strip()
        return None

    def _parse_date(self, lines: list[str]) -> datetime | None:
        for line in lines:
            for match in self.DATE_PATTERN.finditer(line):
                parsed = self._parse_date_value(match.group("value"))
                if parsed is not None:
                    return parsed
        return None

    def _parse_date_value(self, value: str) -> datetime | None:
        for fmt in (
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
            "%d.%m.%Y",
            "%d/%m/%Y",
            "%d-%m-%Y",
        ):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        if re.match(r"\d{2}[./-]\d{2}[./-]\d{2}\b", value):
            normalized = value.replace("/", ".").replace("-", ".")
            try:
                return datetime.strptime(normalized, "%d.%m.%y")
            except ValueError:
                return None
        return None

    def _parse_total(self, lines: list[str]) -> Decimal:
        total_by_keywords = self._parse_total_by_keywords(lines)
        if total_by_keywords is not None:
            return total_by_keywords
        for line in reversed(lines):
            match = self.TOTAL_PATTERN.search(line)
            if match:
                return Decimal(match.group("value").replace(",", "."))
        values: list[Decimal] = []
        for line in lines:
            tokens = self.MONEY_PATTERN.findall(line)
            values.extend(
                Decimal(token.replace(",", "."))
                for token in tokens
                if Decimal(token.replace(",", ".")) > 0
            )
        return max(values, default=Decimal("0"))

    def _parse_items(
        self, lines: list[str], currency: str,
    ) -> list[ReceiptItemPayload]:
        items: list[ReceiptItemPayload] = []

        # Strategy 1: structured pattern (name qty unit price total on one line)
        for line in lines:
            if self._is_service_line(line):
                continue
            match = self.ITEM_PATTERN.match(line)
            if not match:
                continue
            name = match.group("name").strip()
            if not self._is_valid_fallback_name(name):
                continue
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
                    category_name=DEFAULT_CATEGORY_NAME,
                    confidence=0.85,
                )
            )
        if items:
            return items

        # Strategy 2: two-column format with multi-line name joining
        # Common Ukrainian receipt format:
        #   ItemName              TotalPrice
        #     Qty X UnitPrice
        processed = self._join_multiline_items(lines)
        for line in processed:
            if self._is_service_line(line):
                continue
            match = self.TWO_COLUMN_PATTERN.match(line)
            if not match:
                continue
            name = match.group("name").strip()
            if not self._is_valid_fallback_name(name):
                continue
            total = Decimal(match.group("total").replace(",", "."))
            if total <= 0:
                continue
            items.append(
                ReceiptItemPayload(
                    name=name,
                    normalized_name=normalize_item_name(name),
                    total_price=total,
                    price_per_unit=total,
                    quantity=Decimal("1"),
                    unit="pcs",
                    discount=Decimal("0"),
                    currency=currency,
                    category_name=DEFAULT_CATEGORY_NAME,
                    confidence=0.75,
                )
            )
        if items:
            return items

        # Strategy 3: fallback — any line with a money amount
        fallback_items: list[ReceiptItemPayload] = []
        for line in lines:
            if self._is_service_line(line):
                continue
            tokens = self.MONEY_PATTERN.findall(line)
            if len(tokens) < 1 or len(line.split()) < 2:
                continue
            total = Decimal(tokens[-1].replace(",", "."))
            name = re.sub(r"\d+[.,]\d{2}", "", line).strip(" -")
            if total <= 0 or not self._is_valid_fallback_name(name):
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
                    category_name=DEFAULT_CATEGORY_NAME,
                    confidence=0.65,
                )
            )
        return fallback_items

    def _join_multiline_items(self, lines: list[str]) -> list[str]:
        """Pre-process OCR lines: join wrapped item names and skip qty lines.

        Handles the common receipt format where item name may wrap to the
        next line and a quantity detail line follows:

            Хліб "Бородинський" 0,4кг п
            ол.наріз.                    36.00
              0.612 X 247.00
        """
        result: list[str] = []
        pending_name: str | None = None
        for line in lines:
            if self.QTY_LINE_PATTERN.match(line):
                pending_name = None
                continue
            has_price = bool(self.MONEY_PATTERN.search(line))
            if has_price:
                if (
                    pending_name is not None
                    and not self._is_service_line(pending_name)
                ):
                    result.append(f"{pending_name} {line}")
                else:
                    result.append(line)
                pending_name = None
            else:
                if pending_name is not None:
                    pending_name = f"{pending_name} {line}"
                else:
                    pending_name = line
        return result

    def _estimate_confidence(
        self, lines: list[str], items: list[ReceiptItemPayload],
    ) -> float:
        score = 0.35
        raw_text = "\n".join(lines)
        if self._parse_optional(self.STORE_PATTERN, lines):
            score += 0.2
        if self._parse_optional(self.INN_PATTERN, lines):
            score += 0.1
        if any(
            self._parse_date_value(match.group("value")) is not None
            for match in self.DATE_PATTERN.finditer(raw_text)
        ):
            score += 0.15
        if items:
            score += min(0.2, len(items) * 0.03)
        if self.TOTAL_PATTERN.search(raw_text):
            score += 0.1
        return min(score, 0.98)

    def _parse_total_by_keywords(self, lines: list[str]) -> Decimal | None:
        for keywords in self.TOTAL_PRIORITY_KEYWORDS:
            for index in range(len(lines) - 1, -1, -1):
                line = lines[index]
                normalized = self._normalize_search_text(line)
                if not any(keyword in normalized for keyword in keywords):
                    continue
                if any(
                    keyword in normalized
                    for keyword in self.NON_FINAL_TOTAL_KEYWORDS
                ):
                    continue
                amount = self._extract_amount_from_line(line)
                if amount is None:
                    amount = self._extract_amount_from_neighbor_lines(
                        lines, index,
                    )
                if amount is not None and amount > 0:
                    return amount
        return None

    def _extract_amount_from_line(self, line: str) -> Decimal | None:
        tokens = self.MONEY_PATTERN.findall(line)
        if not tokens:
            return None
        return Decimal(tokens[-1].replace(",", "."))

    def _is_service_line(self, line: str) -> bool:
        normalized = self._normalize_search_text(line)
        for keyword in self.SERVICE_LINE_KEYWORDS:
            pattern = r"(?:^|\s)" + re.escape(keyword)
            if re.search(pattern, normalized):
                return True
        return False

    def _is_valid_fallback_name(self, name: str) -> bool:
        normalized = normalize_item_name(name)
        if normalized in self.CURRENCY_ONLY_NAMES:
            return False
        letters_only = re.sub(
            r"[^A-Za-zА-Яа-яІіЇїЄєҐґ]", "", normalized, flags=re.UNICODE,
        )
        return len(letters_only) >= 3

    def _extract_amount_from_neighbor_lines(
        self, lines: list[str], index: int,
    ) -> Decimal | None:
        for offset in (1, 2):
            next_index = index + offset
            if next_index >= len(lines):
                break
            candidate_line = lines[next_index]
            if self._is_service_line(candidate_line):
                continue
            amount = self._extract_amount_from_line(candidate_line)
            if amount is not None and amount > 0:
                return amount
        return None

    def _normalize_search_text(self, value: str) -> str:
        normalized = re.sub(
            r"[^0-9A-Za-zА-Яа-яІіЇїЄєҐґ\s]", " ", value, flags=re.UNICODE,
        )
        return re.sub(r"\s+", " ", normalized).strip().lower()
