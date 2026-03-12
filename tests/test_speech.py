from app.bot import normalize_voice_text
from app.services.speech import MockSpeechEngine, SpeechPayload


async def test_mock_speech_engine_returns_empty() -> None:
    engine = MockSpeechEngine()
    result = await engine.recognize(b"fake-audio-bytes")
    assert isinstance(result, SpeechPayload)
    assert result.text == ""
    assert result.confidence == 0.0


def test_normalize_voice_text_splits_items() -> None:
    assert normalize_voice_text("молоко 80 хлеб 30 гречка 70") == (
        "молоко 80, хлеб 30, гречка 70"
    )


def test_normalize_voice_text_preserves_commas() -> None:
    assert normalize_voice_text("молоко 80, хлеб 30") == "молоко 80, хлеб 30"


def test_normalize_voice_text_handles_decimals() -> None:
    assert normalize_voice_text("молоко 80.50 хлеб 30") == "молоко 80.50, хлеб 30"


def test_normalize_voice_text_multiword_names() -> None:
    result = normalize_voice_text("молоко від фермера 80 хліб білий 30")
    assert result == "молоко від фермера 80, хліб білий 30"


def test_normalize_voice_text_strips_currency_words() -> None:
    result = normalize_voice_text(
        "молоко 40 гривен хлеб 35 гривен мясо 274 гривны "
        "чипсы 80 гривен сухарики 50 гривен гречка 60 гривен"
    )
    assert result == "молоко 40, хлеб 35, мясо 274, чипсы 80, сухарики 50, гречка 60"


def test_normalize_voice_text_strips_griven_transliteration() -> None:
    result = normalize_voice_text("молоко 40 griven хлеб 35 griven")
    assert result == "молоко 40, хлеб 35"


def test_normalize_voice_text_strips_grn() -> None:
    result = normalize_voice_text("молоко 40 грн хлеб 30 грн")
    assert result == "молоко 40, хлеб 30"


def test_normalize_voice_text_strips_grn_glued_to_number() -> None:
    """Google STT sometimes outputs '120грн' without space."""
    result = normalize_voice_text(
        "пиво 120грн сухарики 80 грн чипси 100 грн "
        "мясо 456 грн гречка 70грн молоко 55 грн"
    )
    assert result == "пиво 120, сухарики 80, чипси 100, мясо 456, гречка 70, молоко 55"


def test_normalize_voice_text_strips_conjunction_and() -> None:
    """Handles 'и' before the last item."""
    result = normalize_voice_text(
        "пиво 120 гривен, сухарики 80 гривен, "
        "гречка 70 гривен и молоко 55 гривен"
    )
    assert result == "пиво 120, сухарики 80, гречка 70, молоко 55"
