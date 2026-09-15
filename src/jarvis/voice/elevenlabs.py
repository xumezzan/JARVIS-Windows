"""Explicit, one-use cloud speech authorization; secrets remain in the native helper."""

import re
from dataclasses import dataclass, field

from jarvis.voice.contracts import VoiceError
from jarvis.voice.local import exchange

PREVIEW = "Здравствуйте. Я Джарвис, ваш помощник. Готов к работе."


@dataclass
class ElevenLabsSpeaker:
    voice_id: str
    account: str = field(repr=False)
    authorized_text: str = field(repr=False)
    used: bool = False
    local_only = False

    async def speak(self, text: str) -> None:
        if self.used or text != self.authorized_text or not 1 <= len(text) <= 500:
            raise VoiceError("eleven_consent")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.voice_id):
            raise VoiceError("eleven_voice")
        if not re.fullmatch(r"[0-9a-f]{64}", self.account):
            raise VoiceError("eleven_changed")
        self.used = True
        self.authorized_text = ""
        await exchange(
            {
                "operation": "speak",
                "voice_id": self.voice_id,
                "account": self.account,
                "text": text,
            },
            seconds=55,
            helper="jarvis.platforms.elevenlabs",
        )
