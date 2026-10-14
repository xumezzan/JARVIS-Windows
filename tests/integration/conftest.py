"""Ordinary UI tests use synthetic connection status, never the OS credential store."""

import pytest


@pytest.fixture(autouse=True)
def offline_connection_status(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unconnected(provider: str) -> bool:
        return False

    monkeypatch.setattr("jarvis.ui.connection_probe.has_api_key", unconnected)
