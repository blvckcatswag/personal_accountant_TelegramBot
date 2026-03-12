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
