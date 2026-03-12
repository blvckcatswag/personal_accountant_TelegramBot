from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SpeechPayload:
    text: str
    confidence: float
    language: str


class SpeechEngine(Protocol):
    async def recognize(self, content: bytes, language: str = "ru-RU") -> SpeechPayload:
        ...


class MockSpeechEngine:
    async def recognize(self, content: bytes, language: str = "ru-RU") -> SpeechPayload:
        return SpeechPayload(text="", confidence=0.0, language=language)


class GoogleSpeechEngine:
    def __init__(
        self,
        credentials_file: str | None = None,
        credentials_json: str | None = None,
    ) -> None:
        self.credentials_file = credentials_file
        self.credentials_json = credentials_json

    async def recognize(self, content: bytes, language: str = "ru-RU") -> SpeechPayload:
        return await asyncio.to_thread(self._recognize_sync, content, language)

    def _recognize_sync(self, content: bytes, language: str) -> SpeechPayload:
        try:
            from google.cloud import speech
            from google.oauth2 import service_account
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "google-cloud-speech is not installed. "
                "Run `pip install google-cloud-speech`."
            ) from exc

        credentials = self._build_credentials(service_account)
        client = speech.SpeechClient(credentials=credentials)

        audio = speech.RecognitionAudio(content=content)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.OGG_OPUS,
            sample_rate_hertz=48000,
            language_code=language,
            alternative_language_codes=["uk-UA"],
            enable_automatic_punctuation=True,
        )

        response = client.recognize(config=config, audio=audio)

        if not response.results:
            logger.warning("Google STT returned no results")
            return SpeechPayload(text="", confidence=0.0, language=language)

        texts: list[str] = []
        confidences: list[float] = []
        for result in response.results:
            alternative = result.alternatives[0]
            texts.append(alternative.transcript)
            confidences.append(alternative.confidence)

        text = " ".join(texts)
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        logger.debug("STT result (confidence=%.2f): %s", avg_confidence, text)
        return SpeechPayload(
            text=text,
            confidence=avg_confidence,
            language=language,
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
            return service_account.Credentials.from_service_account_file(
                self.credentials_file,
            )
        return None
