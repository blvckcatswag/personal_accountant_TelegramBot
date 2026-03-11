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

