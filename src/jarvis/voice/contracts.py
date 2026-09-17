"""Bounded local audio contracts. Audio and transcripts are ephemeral, untrusted data."""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event
from typing import Protocol

from jarvis.core.planner.contracts import PlanResult
from jarvis.core.report import spoken

SAMPLE_RATE = 16000
MAX_SECONDS = 30
MAX_AUDIO_BYTES = SAMPLE_RATE * 2 * MAX_SECONDS


class VoiceError(Exception):
    """Finite public error codes; never expose native exception text."""

    def __init__(self, code: str) -> None:
        self.code = code if code in ERROR_TEXT else "voice_failed"
        super().__init__(self.code)


ERROR_TEXT = {
    "eleven_credentials": "Сохраните API-ключ ElevenLabs в настройках голоса, не в чате.",
    "eleven_free": "Озвучивание остановлено: нужен тариф Free с отключённой оплатой сверх лимита.",
    "eleven_quota": "Бесплатный лимит закончился или слишком мал. Выберите системный голос.",
    "eleven_voice": "Голос недоступен для этого Free-аккаунта. Обновите список голосов.",
    "eleven_network": "ElevenLabs недоступен. Запрос не повторяется; выберите системный голос.",
    "eleven_consent": "Для одной озвучки ElevenLabs отметьте согласие на передачу текста.",
    "eleven_changed": "Ключ аккаунта изменился. Обновите список голосов и согласие.",
    "voice_failed": "Голос недоступен. Проверьте установку голосовых зависимостей.",
    "device": "Микрофон недоступен: проверьте устройство и разрешение ОС для Python.",
    "overflow": "Запись прервана: переполнение аудиобуфера. Повторите запись вручную.",
    "silence": "Речь не обнаружена. Удерживайте кнопку и произнесите команду.",
    "model": "Выберите распакованную локальную русскую модель Vosk.",
    "recognition": "Не удалось распознать речь. Повторите запись или введите текст.",
    "speech": "Озвучивание недоступно. Проверьте установленный русский системный голос.",
    "timeout": "Истекло время голосовой операции. Микрофон и озвучивание остановлены.",
    "invalid_audio": "Неподдерживаемая или слишком длинная аудиозапись.",
}


@dataclass(frozen=True)
class AudioClip:
    pcm: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not self.pcm or len(self.pcm) > MAX_AUDIO_BYTES or len(self.pcm) % 2:
            raise VoiceError("invalid_audio")


@dataclass(frozen=True)
class Transcript:
    text: str = field(repr=False)
    confidence: float

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise VoiceError("silence")
        if len(self.text) > 4000 or not math.isfinite(self.confidence):
            raise VoiceError("recognition")
        if not 0 <= self.confidence <= 1:
            raise VoiceError("recognition")

    @property
    def uncertain(self) -> bool:
        return self.confidence < 0.75

    @property
    def is_cancel(self) -> bool:
        words = self.text.strip().lower().strip(" .,!?:;…")
        return not self.uncertain and words in {
            "стоп",
            "отмена",
            "отмени",
            "остановись",
            "остановить",
            "отмени задачу",
        }


class Recorder(Protocol):
    # listen=True lets the adapter end the phrase on silence instead of on a held control.
    async def record(
        self, released: Event, ready: Callable[[], None], listen: bool = False
    ) -> AudioClip: ...


class Recognizer(Protocol):
    # Cloud adapters must add a separate explicit disclosure/consent path before use.
    # Stage 6 composition accepts only local implementations.
    local_only: bool

    async def transcribe(self, clip: AudioClip) -> Transcript: ...


class Speaker(Protocol):
    local_only: bool

    async def speak(self, text: str) -> None: ...


def spoken_result(result: PlanResult) -> str:
    """Speak what was done, assembled by trusted code in `core/report.py`.

    The rule it keeps is the one this module always kept: nothing a model wrote and nothing
    a service answered is ever spoken. What is new is that the sentence is about the owner's
    task rather than about the planner's own statuses.
    """
    return spoken(result)
