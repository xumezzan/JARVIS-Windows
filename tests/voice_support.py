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
        self.listens = 0
        self.speech_ends = Event()
        self.spoken: list[str] = []

    async def record(
        self, released: Event, ready: Callable[[], None], listen: bool = False
    ) -> AudioClip:
        self.captures += 1
        self.listens += int(listen)
        self.recording.set()
        ready()
        try:
            # Hands-free capture ends by itself; a held control ends on release.
            while not released.is_set() and not (listen and self.speech_ends.is_set()):
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


class WakeFixture:
    """A standing listener under the test's control: it hears the name only when told to."""

    local_only = True

    def __init__(self) -> None:
        self.cycles = 0
        self.armed = Event()
        self.heard = Event()
        self.silence = Event()
        self.error = ""

    async def listen(self, released: Event, ready: Callable[[], None]) -> bool:
        self.cycles += 1
        self.armed.set()
        ready()
        try:
            while not released.is_set() and not self.heard.is_set() and not self.silence.is_set():
                await asyncio.sleep(0.005)
            if self.error:
                raise VoiceError(self.error)
            return self.heard.is_set()
        finally:
            self.armed.clear()
