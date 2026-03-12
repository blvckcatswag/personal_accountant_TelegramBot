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


def test_receipt_parser_handles_two_column_multiline_format() -> None:
    """Ukrainian receipt: item name + price on one line, qty x unit_price on next."""
    raw_text = """
Касір Кассир №2
Каса 2
1.000 X 36.00
Хліб "Бородинський" 0,4кг п
ол.наріз.                    36.00
0.612 X 247.00
Ребро                       151.16
4 X 22.00
Плавлений сир Голландський
Ферма 70г                    88.00
1 X 39.00
Банан, кг                    80.03
1 X 183.00
Сир нарізка 400г асорт. Mle
kpol                        183.00
2.396 X 247.00
Задок                       591.81
0.840 X 90.00
Мандарин                     75.60
1 X 10.00
Приправа до Супу (Дари прир
оди) Ямуна, 25г              10.00
0.264 X 248.00
Цукерка глаз. Сливки-Ленівк
и, кг                        65.47
0.136 X 250.00
Цукерки "Джек", кг К         34.00
0.226 X 196.00
Круасанчики згущ/молокоLука
с ф/п                        44.30
Готівка                    1500.30
РЕШТА                      -125.00
СУМА                       1398.37
00010002024D00C0
11.03.2026 17:39:04
    """
    receipt = ReceiptParser().parse(raw_text, default_currency="UAH")

    assert receipt.total_amount == Decimal("1398.37")
    assert receipt.receipt_date == datetime(2026, 3, 11, 17, 39, 4)

    item_names = [item.name for item in receipt.items]
    assert len(receipt.items) >= 10, f"Expected >=10 items, got {len(receipt.items)}: {item_names}"

    # Multi-line names should be joined
    assert any("Хліб" in name and "наріз" in name for name in item_names), (
        f"Expected joined bread item, got: {item_names}"
    )
    assert any("Плавлений" in name for name in item_names), (
        f"Expected cheese item, got: {item_names}"
    )

    # Qty lines should NOT appear as items
    assert all("X" not in name.split() for name in item_names), (
        f"Qty lines leaked into items: {item_names}"
    )

    # Service lines should not be items
    assert all("СУМА" not in name for name in item_names)
    assert all("Готівка" not in name for name in item_names)
