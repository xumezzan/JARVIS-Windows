"""Native UI consent lifecycle with synthetic helpers, no network/keychain/audio."""

import asyncio
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot
from tests.voice_support import VoiceFixture

from jarvis.core.planner.contracts import PlanResult
from jarvis.ui import elevenlabs_dialog as ui
from jarvis.ui.voice_panel import VoicePanel
from jarvis.voice import elevenlabs as voice


def test_preview_consent_reset_and_key_cleared(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    async def exchange(request: dict[str, object], **kwargs: Any) -> dict[str, Any]:
        calls.append(dict(request))
        if request["operation"] == "voices":
            return {
                "account": "a" * 64,
                "remaining": 100,
                "voices": [{"id": "default", "name": "Default"}],
            }
        return {"saved": True}

    monkeypatch.setattr(ui, "exchange", exchange)
    parent = VoicePanel()
    qtbot.addWidget(parent)
    dialog = ui.ElevenLabsDialog(parent)
    qtbot.addWidget(dialog)
    dialog.show()
    assert not calls
    dialog.key.setText("synthetic")
    QTest.mouseClick(dialog.save, Qt.MouseButton.LeftButton)
    assert not dialog.key.text()
    qtbot.waitUntil(lambda: dialog.worker is None)
    QTest.mouseClick(dialog.refresh, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: dialog.worker is None)
    QTest.mouseClick(dialog.preview, Qt.MouseButton.LeftButton)
    assert len(calls) == 2
    QTest.mouseClick(dialog.consent, Qt.MouseButton.LeftButton)
    QTest.mouseClick(dialog.preview, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: dialog.worker is None)
    assert calls[-1]["text"] == voice.PREVIEW and not dialog.consent.isChecked()
    QTest.mouseClick(dialog.preview, Qt.MouseButton.LeftButton)
    assert len(calls) == 3
    dialog.consent.setChecked(True)
    dialog.key.setText("replacement")
    assert not dialog.consent.isChecked() and not dialog.account
    dialog.reject()


def test_next_result_only_and_cancel(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    async def exchange(request: dict[str, object], **kwargs: Any) -> dict[str, Any]:
        calls.append(dict(request))
        return {"spoken": True}

    monkeypatch.setattr(voice, "exchange", exchange)
    fixture = VoiceFixture()
    panel = VoicePanel(recorder=fixture, recognizer=fixture, speaker=fixture)
    qtbot.addWidget(panel)
    panel._select_cloud("default", "a" * 64)
    panel.finish_plan(PlanResult("simulated"))
    qtbot.waitUntil(lambda: panel.worker is None)
    assert len(calls) == 1 and panel.cloud_selection is None
    panel.finish_plan(PlanResult("simulated"))
    qtbot.waitUntil(lambda: panel.worker is None)
    assert len(calls) == 1 and len(fixture.spoken) == 1
    panel._select_cloud("default", "a" * 64)
    panel.cancel()
    assert panel.cloud_selection is None
    panel.shutdown()


def test_close_cancels_pending_helper(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from threading import Event

    started, stopped = Event(), Event()

    async def exchange(*args: Any, **kwargs: Any) -> dict[str, Any]:
        started.set()
        try:
            await asyncio.sleep(100)
        finally:
            stopped.set()
        return {}

    monkeypatch.setattr(ui, "exchange", exchange)
    parent = VoicePanel()
    qtbot.addWidget(parent)
    dialog = ui.ElevenLabsDialog(parent)
    qtbot.addWidget(dialog)
    dialog.show()
    QTest.mouseClick(dialog.refresh, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(started.is_set)
    dialog.reject()
    assert stopped.is_set() and dialog.worker is None
