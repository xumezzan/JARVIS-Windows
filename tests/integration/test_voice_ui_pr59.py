"""Real Qt gestures with deterministic audio; planner effects still require exact UI approval."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot
from tests.unit.test_planner import MESSAGE, Scripted
from tests.voice_support import VoiceFixture

from jarvis.config import AppConfig
from jarvis.core.planner.offline import call
from jarvis.ui.compact_planner import PlannerWindow as CompactPlannerWindow
from jarvis.ui.planner_window import PlannerWindow
from jarvis.ui.voice_panel import HoldButton, VoicePanel


def make_window(
    qtbot: QtBot, tmp_path: Path, fixture: VoiceFixture, compact: bool
) -> PlannerWindow | CompactPlannerWindow:
    panel = VoicePanel(recorder=fixture, recognizer=fixture, speaker=fixture)
    window = (CompactPlannerWindow if compact else PlannerWindow)(
        AppConfig(tmp_path), voice_panel=panel
    )
    qtbot.addWidget(window)
    window.show()
    return window


def record(qtbot: QtBot, panel: VoicePanel, button: HoldButton | None = None) -> None:
    hold = button or panel.hold
    QTest.mousePress(hold, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Идёт запись" in panel.status.text())
    QTest.mouseRelease(hold, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.worker is None)


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize(
    "confidence,enabled,started", [(0.75, True, True), (0.749, True, False), (0.99, False, False)]
)
def test_optional_autostart_keeps_manual_review_below_threshold(
    qtbot: QtBot,
    tmp_path: Path,
    confidence: float,
    enabled: bool,
    started: bool,
    compact: bool,
) -> None:
    fixture = VoiceFixture("проверь систему", confidence=confidence)
    window = make_window(qtbot, tmp_path, fixture, compact)
    try:
        qtbot.waitUntil(lambda: window.memory.available and window.memory.worker is None)
        window.voice.auto_start.setChecked(enabled)
        record(qtbot, window.voice)
        qtbot.waitUntil(lambda: window.worker is None and window.voice.worker is None)
        assert bool(window.output.toPlainText()) == started, (
            window.status.text(),
            window.voice.status.text(),
            window.command.toPlainText(),
        )
        assert fixture.spoken == (["Принял, выполняю"] if started else [])
        assert window.command.toPlainText() == fixture.text
    finally:
        window.shutdown()


def test_autostart_still_waits_for_exact_action_approval(qtbot: QtBot, tmp_path: Path) -> None:
    fixture = VoiceFixture("отправь сообщение")
    panel = VoicePanel(recorder=fixture, recognizer=fixture, speaker=fixture)
    window = CompactPlannerWindow(
        AppConfig(tmp_path),
        voice_panel=panel,
        provider=Scripted([call("local.append_message", MESSAGE)]),
    )
    qtbot.addWidget(window)
    window.show()
    try:
        qtbot.waitUntil(lambda: window.memory.available and window.memory.worker is None)
        window.voice.auto_start.setChecked(True)
        window.cloud_consent.setChecked(True)
        window.simulation.setChecked(False)
        record(qtbot, panel)
        qtbot.waitUntil(lambda: window.approval_dialog is not None)
        assert window.outbox.count == 0
        assert not window.cloud_consent.isChecked()
        window.stop()
        qtbot.waitUntil(lambda: window.worker is None)
        assert window.outbox.count == 0
    finally:
        window.shutdown()
