from app.services.currency import CurrencyService


def test_currency_detection_handles_pln_symbol() -> None:
    text = "TOTAL 45.50 zł"
    assert CurrencyService.detect_currency(text) == "PLN"

