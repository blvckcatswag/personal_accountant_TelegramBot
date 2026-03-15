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


def test_normalize_voice_text_strips_trailing_period() -> None:
    """Google STT adds period at end of sentence."""
    result = normalize_voice_text(
        "молоко 40 гривен хлеб 35 гривен гречка 60 гривен."
    )
    assert result == "молоко 40, хлеб 35, гречка 60"


def test_normalize_voice_text_strips_mid_sentence_period() -> None:
    """Periods after numbers inside text should not break parsing."""
    result = normalize_voice_text("молоко 40. хлеб 30.")
    assert result == "молоко 40, хлеб 30"


def test_normalize_voice_text_handles_kopecks() -> None:
    """'X гривен Y копеек' merged into decimal."""
    result = normalize_voice_text(
        "молоко 40 гривен 50 копеек хлеб 35 гривен 5 копеек"
    )
    assert result == "молоко 40.50, хлеб 35.05"


def test_normalize_voice_text_handles_kopecks_abbreviated() -> None:
    """'X грн Y коп' format."""
    result = normalize_voice_text("молоко 40 грн 50 коп хлеб 35 грн")
    assert result == "молоко 40.50, хлеб 35"


def test_normalize_voice_text_full_stt_output() -> None:
    """Realistic Google STT output with commas, currency, trailing period."""
    result = normalize_voice_text(
        "Молоко 40 гривен, хлеб 35 гривен, мясо 274 гривны, "
        "чипсы 80 гривен, сухарики 50 гривен, гречка 60 гривен."
    )
    assert result == "Молоко 40, хлеб 35, мясо 274, чипсы 80, сухарики 50, гречка 60"


def test_normalize_voice_text_misplaced_commas() -> None:
    """Google STT places commas BEFORE numbers instead of after (Bug #1)."""
    result = normalize_voice_text(
        "картошка 60 пиво, 78 хлеб 35 молоко, 70 кефир, 64, творог, 46"
    )
    assert result == "картошка 60, пиво 78, хлеб 35, молоко 70, кефир 64, творог 46"


def test_normalize_voice_text_misplaced_commas_preserves_decimals() -> None:
    """Commas in decimal numbers (70,50) must not be stripped."""
    result = normalize_voice_text("молоко 70,50 хлеб 30")
    assert result == "молоко 70,50, хлеб 30"
