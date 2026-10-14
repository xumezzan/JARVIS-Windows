"""Ordinary UI tests use synthetic connection status and audio, without keys/devices."""

import pytest
from tests.voice_support import VoiceFixture


@pytest.fixture(autouse=True)
def offline_connection_status(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unconnected(provider: str) -> bool:
        return False

    monkeypatch.setattr("jarvis.ui.connection_probe.has_api_key", unconnected)
    monkeypatch.setattr("jarvis.ui.voice_panel.LocalRecorder", VoiceFixture)
    monkeypatch.setattr("jarvis.ui.voice_panel.LocalSpeaker", VoiceFixture)
