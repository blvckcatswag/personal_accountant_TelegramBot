from datetime import datetime
from decimal import Decimal

from app.services.ocr import ReceiptParser


def test_receipt_parser_extracts_basic_fields() -> None:
    raw_text = """
    Магазин: Сільпо
    ИНН: 12345678
    11.03.2026 14:20
    Молоко 2.5%  2 шт  45.50  91.00
    Бананы  1 кг  68.00  68.00
    Итого 159.00
    """
    receipt = ReceiptParser().parse(raw_text, default_currency="UAH")

    assert receipt.store_name == "Сільпо"
    assert receipt.store_inn == "12345678"
    assert receipt.total_amount == Decimal("159.00")
    assert len(receipt.items) == 2
    assert receipt.items[0].normalized_name.startswith("молоко")


def test_receipt_parser_skips_phone_number_when_parsing_date() -> None:
    raw_text = """
    ТОВ "Сільпо-фуд"
    Гаряча лінія: +38(050)95-88-03
    11.03.2026 14:20
    Сир  1 шт  159.00  159.00
    СУМА 159.00
    """
    receipt = ReceiptParser().parse(raw_text, default_currency="UAH")

    assert receipt.receipt_date == datetime(2026, 3, 11, 14, 20)
    assert receipt.total_amount == Decimal("159.00")


def test_receipt_parser_prefers_amount_due_over_cash_and_discount_lines() -> None:
    raw_text = """
    ТОВ <<НУМІС>>
    Всього до знижки 79.60 грн.
    Знижка 0.10 грн.
    До сплати 79.50 грн.
    ГОТІВКА 100.00 грн.
    Сума 79.50
    Решта -20.50 грн.
    """
    receipt = ReceiptParser().parse(raw_text, default_currency="UAH")

    assert receipt.total_amount == Decimal("79.50")


def test_receipt_parser_ignores_service_lines_in_fallback_items() -> None:
    raw_text = """
    ТОВ <<НУМІС>>
    Шампунь La Ferm 19.90
    Знижка 0.10 грн.
    До сплати 79.50 грн.
    ГОТІВКА 100.00 грн.
    Доступно 1.10
    A 19.90
    """
    receipt = ReceiptParser().parse(raw_text, default_currency="UAH")

    assert [item.name for item in receipt.items] == ["Шампунь La Ferm"]
    assert [item.total_price for item in receipt.items] == [Decimal("19.90")]


def test_receipt_parser_reads_amount_due_from_next_line() -> None:
    raw_text = """
    ТОВ "Сільпо-фуд"
    ПІДСУМОК 768.44
    ЗНИЖКА -101.44
    ДО СПЛАТИ:
    667.00 ГРН
    """
    receipt = ReceiptParser().parse(raw_text, default_currency="UAH")

    assert receipt.total_amount == Decimal("667.00")
