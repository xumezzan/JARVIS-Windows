"""Deterministic offline voice fixtures: no devices, files, credentials, or network."""

import asyncio
from collections.abc import Callable
from threading import Event

from jarvis.voice.contracts import AudioClip, Transcript, VoiceError


class VoiceFixture:
    local_only = True

    def __init__(self, text: str = "проверь систему дважды", *, confidence: float = 0.99) -> None:
        self.text = text
        self.confidence = confidence
        self.recording = Event()
        self.recognizing = Event()
        self.speaking = Event()
        self.record_closed = Event()
        self.speech_closed = Event()
        self.recognize_delay = 0.0
        self.speech_delay = 0.0
        self.error = ""
        self.captures = 0
        self.spoken: list[str] = []

    async def record(self, released: Event, ready: Callable[[], None]) -> AudioClip:
        self.captures += 1
        self.recording.set()
        ready()
        try:
            while not released.is_set():
                await asyncio.sleep(0.005)
            if self.error:
                raise VoiceError(self.error)
            return AudioClip(b"\x01\x00" * 3200)
        finally:
            self.record_closed.set()

    async def transcribe(self, clip: AudioClip) -> Transcript:
        self.recognizing.set()
        await asyncio.sleep(self.recognize_delay)
        return Transcript(self.text, self.confidence)

    async def speak(self, text: str) -> None:
        self.speaking.set()
        self.spoken.append(text)
        try:
            await asyncio.sleep(self.speech_delay)
        finally:
            self.speech_closed.set()
