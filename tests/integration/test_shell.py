"""Real Qt event-loop tests of the command shell; execution runs through PermissionEngine."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QPlainTextEdit,
    QPushButton,
)
from pytestqt.qtbot import QtBot

from jarvis.config import AppConfig
from jarvis.observability.logging import ShellLog
from jarvis.ui.main_window import MainWindow
from jarvis.ui.states import UiState

pytestmark = pytest.mark.integration

SIMULATION = 1
EXECUTE = 0


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path) -> Iterator[MainWindow]:
    log = ShellLog(tmp_path / "logs")
    shell = MainWindow(AppConfig(tmp_path), log)
    qtbot.addWidget(shell)
    shell.show()
    yield shell
    shell.shutdown()
    shell.close()


def start(qtbot: QtBot, window: MainWindow, text: str = "проверь систему") -> None:
    window.command_input.setPlainText(text)
    QTest.mouseClick(window.submit_button, Qt.MouseButton.LeftButton)


def test_startup_and_unavailable_capabilities(window: MainWindow) -> None:
    assert window.isVisible()
    assert window.state == UiState.IDLE
    assert window.microphone_button.isEnabled()
    # The session exists from the start, but opening the app never starts capture.
    assert window.voice is not None and window.voice.worker is None
    assert window.planner_window is not None and not window.planner_window.isVisible()
    assert window.permissions_button.isEnabled()
    assert not window.stop_button.isEnabled()
    assert not window.running
    # Execution is the default; autonomy was requested and is visible, never hidden.
    assert window.run_mode.currentIndex() == EXECUTE
    assert window.provider_mode.currentIndex() == 0
    assert window.autonomy.isChecked()


def test_small_window_scrolls_instead_of_clipping(qtbot: QtBot, window: MainWindow) -> None:
    window.resize(860, 640)
    qtbot.waitUntil(lambda: window.scroll_area.verticalScrollBar().maximum() > 0)
    content = window.scroll_area.widget()
    assert content is not None
    assert content.height() >= 800
    qtbot.waitUntil(lambda: window.scroll_area.horizontalScrollBar().maximum() == 0)
    assert window.dashboard_grid.getItemPosition(
        window.dashboard_grid.indexOf(window.center_column)
    ) == (0, 0, 1, 2)
    window.resize(1480, 940)
    qtbot.waitUntil(lambda: not window._compact_layout)
    assert window.dashboard_grid.getItemPosition(
        window.dashboard_grid.indexOf(window.center_column)
    ) == (0, 1, 1, 1)


def test_simulated_command_reports_facts(qtbot: QtBot, window: MainWindow) -> None:
    states: list[str] = []
    window.state_changed.connect(states.append)
    window.run_mode.setCurrentIndex(SIMULATION)
    with qtbot.waitSignal(window.task_finished, timeout=15000) as result:
        start(qtbot, window, "проверь систему дважды")
        assert window.state == UiState.THINKING
        assert not window.submit_button.isEnabled()
    assert result.args == ["simulated"]
    assert states[0] == "thinking" and states[-1] == "success"
    assert "executing" in states
    assert window.transcript.toPlainText() == "проверь систему дважды"
    # The report comes first, the engine's own account of the steps after it.
    assert window.action_label.text().startswith("Это была симуляция")
    assert "local.check: SIMULATED" in window.action_label.text()
    assert not window.running
    assert window.submit_button.isEnabled()
    assert "command_succeeded" in window.log.path.read_text()


def test_real_execution_runs_the_tool_without_blocking_the_ui(
    qtbot: QtBot, window: MainWindow
) -> None:
    assert window.run_mode.currentIndex() == EXECUTE
    ticks: list[int] = []
    timer = QTimer(window)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    # Each local.check sleeps 100 ms inside the adapter; the UI thread must keep running.
    with qtbot.waitSignal(window.task_finished, timeout=15000) as result:
        start(qtbot, window, "проверь систему дважды")
    timer.stop()
    assert len(ticks) >= 3
    assert result.args == ["finished"]
    assert window.state == UiState.SUCCESS
    # Two checks change nothing in the world, and the assistant says exactly that.
    assert window.action_label.text().startswith(
        "Готово." + "\n" + "Ничего не менял — только посмотрел."
    )
    assert "local.check: SUCCESS" in window.action_label.text()
    assert (window.config.data_dir / "audit.sqlite3").exists()


def test_autonomy_reaches_the_planner_session(qtbot: QtBot, window: MainWindow) -> None:
    window.run_mode.setCurrentIndex(SIMULATION)
    with qtbot.waitSignal(window.task_finished, timeout=15000):
        start(qtbot, window)
    planner = window.planner_window
    assert planner is not None
    assert planner.autonomous.isChecked()
    assert planner.simulation.isChecked()  # Simulation was requested and applied.
    assert planner.provider_choice.currentIndex() == 0
    assert not planner.isVisible()  # The command bar never needs a second window.


def test_spoken_command_runs_without_a_second_step(qtbot: QtBot, window: MainWindow) -> None:
    window.run_mode.setCurrentIndex(SIMULATION)
    assert window.voice is not None
    # The assistant answers out loud, and a finished transcript is the submission itself.
    assert window.voice.speech_enabled.isChecked()
    assert window.microphone_button.wired
    with qtbot.waitSignal(window.task_finished, timeout=15000) as result:
        window.voice.transcript_ready.emit("проверь систему")
    assert result.args == ["simulated"]
    assert window.transcript.toPlainText() == "проверь систему"
    # A cancelled or empty transcript never starts anything.
    window.voice.transcript_ready.emit("   ")
    assert not window.running


def test_unknown_command_asks_in_the_main_window(qtbot: QtBot, window: MainWindow) -> None:
    window.run_mode.setCurrentIndex(SIMULATION)
    start(qtbot, window, "сделай что-нибудь")
    planner = window.planner_window
    assert planner is not None
    qtbot.waitUntil(lambda: planner.question_dialog is not None, timeout=15000)
    question = planner.question_dialog
    assert question is not None and question.parent() is window
    assert planner.answer_input is not None and planner.answer_button is not None
    planner.answer_input.setText("проверь систему")
    with qtbot.waitSignal(window.task_finished, timeout=15000) as result:
        QTest.mouseClick(planner.answer_button, Qt.MouseButton.LeftButton)
    assert result.args == ["simulated"]


@pytest.mark.parametrize("when", ["thinking", "executing"])
def test_stop_and_no_stale_success(qtbot: QtBot, window: MainWindow, when: str) -> None:
    start(qtbot, window, "проверь систему дважды")
    if when == "executing":
        qtbot.waitUntil(lambda: window.state == UiState.EXECUTING, timeout=15000)
    with qtbot.waitSignal(window.task_finished, timeout=15000):
        QTest.mouseClick(window.stop_button, Qt.MouseButton.LeftButton)
        window.stop()  # Repeated emergency stop is harmless.
    assert window.state == UiState.CANCELLED
    assert not window.running
    rows = window.log.path.read_text()
    assert rows.count('"event": "cancel_requested"') == 1
    assert "command_succeeded" not in rows


def test_close_during_work_joins_thread(qtbot: QtBot, window: MainWindow) -> None:
    start(qtbot, window, "проверь систему дважды")
    with qtbot.waitSignal(window.task_finished, timeout=15000):
        window.close()
    qtbot.waitUntil(lambda: not window.isVisible())
    assert not window.running
    assert "shell_closed" in window.log.path.read_text()


def test_direct_shutdown_during_work(window: MainWindow, qtbot: QtBot) -> None:
    start(qtbot, window, "проверь систему дважды")
    window.shutdown()
    assert not window.running
    assert window.state == UiState.CANCELLED


@pytest.mark.parametrize("text", ["   ", "x" * 4001])
def test_invalid_input_does_not_start(window: MainWindow, qtbot: QtBot, text: str) -> None:
    start(qtbot, window, text)
    assert not window.running
    assert window.planner_window is not None and window.planner_window.worker is None
    assert window.state == UiState.IDLE
    assert "от 1 до 4 000" in window.validation_label.text()


def test_busy_submission_cannot_replace_task(qtbot: QtBot, window: MainWindow) -> None:
    window.run_mode.setCurrentIndex(SIMULATION)
    with qtbot.waitSignal(window.task_finished, timeout=15000):
        start(qtbot, window, "проверь систему дважды")
        window.submit()
    assert window.log.path.read_text().count('"event": "command_submitted"') == 1


def test_arbitrary_command_is_plain_text_and_not_logged(qtbot: QtBot, window: MainWindow) -> None:
    text = '<img src="https://invalid.example/pixel">\nPRIVATE_INPUT_SENTINEL'
    window.run_mode.setCurrentIndex(SIMULATION)
    start(qtbot, window, text)
    planner = window.planner_window
    assert planner is not None
    qtbot.waitUntil(lambda: planner.question_dialog is not None, timeout=15000)
    with qtbot.waitSignal(window.task_finished, timeout=15000):
        planner.stop()
    assert window.transcript.toPlainText() == text
    content = window.log.path.read_text()
    assert "PRIVATE_INPUT_SENTINEL" not in content
    assert "invalid.example" not in content
    rows = [json.loads(line) for line in content.splitlines()]
    requests = {row["request_id"] for row in rows if row["event"].startswith("command_")}
    assert len(requests) == 1


def test_activity_is_bounded(window: MainWindow) -> None:
    from jarvis.observability.events import ShellEvent

    for _ in range(window.config.activity_limit + 20):
        window.activity.append_event(ShellEvent.SUBMITTED)
    assert window.activity.count() == window.config.activity_limit


def test_planner_entrypoint_and_close_allows_reopen(qtbot: QtBot, window: MainWindow) -> None:
    window.command_input.setPlainText("проверь систему дважды")
    QTest.mouseClick(window.planner_button, Qt.MouseButton.LeftButton)
    planner = window.planner_window
    assert planner is not None and planner.command.toPlainText() == "проверь систему дважды"
    assert planner.isVisible() and planner.prompt_parent is planner
    # The planner's own run button waits for its memory panel to finish its start-up read.
    qtbot.waitUntil(lambda: planner.memory.worker is None, timeout=15000)
    with qtbot.waitSignal(planner.task_finished, timeout=15000):
        QTest.mouseClick(planner.run_button, Qt.MouseButton.LeftButton)
    # A run started in the planner is mirrored by the shell it belongs to.
    assert window.state == UiState.SUCCESS
    planner.close()
    qtbot.waitUntil(lambda: window.planner_window is None)
    window.open_planner()
    assert window.planner_window is not None and window.planner_window is not planner
    # The rebuilt session takes the microphone back over without doubling the capture.
    assert window.voice is window.planner_window.voice
    window.shutdown()
    assert window.planner_window._closed


def test_theme_and_motion_controls_preserve_task(window: MainWindow, qtbot: QtBot) -> None:
    window.command_input.setPlainText("Сохранить мою команду")
    window.theme_picker.setCurrentText("Graphite")
    assert "#111214" in window.styleSheet()
    window.open_planner()
    assert window.planner_window is not None
    assert window.planner_window.styleSheet() == window.styleSheet()
    window.theme_picker.setCurrentText("Midnight")
    assert window.planner_window.styleSheet() == window.styleSheet()
    window.planner_window.close()
    assert window.command_input.toPlainText() == "Сохранить мою команду"
    QTest.mouseClick(window.motion_button, Qt.MouseButton.LeftButton)
    assert not window.orb._timer.isActive()
    phase = window.orb._phase
    qtbot.wait(100)
    assert window.orb._phase == phase
    QTest.mouseClick(window.motion_button, Qt.MouseButton.LeftButton)
    assert window.orb._timer.isActive()
    window.hide()
    assert not window.orb._timer.isActive()


def cloud_answer(model: str, consent: bool, *, accept: bool = True) -> dict[str, object]:
    """Answer the modal consent dialog the way a person would; reports how it opened."""
    seen: dict[str, object] = {}

    def act() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        field = dialog.findChild(QPlainTextEdit)
        box = dialog.findChild(QCheckBox)
        assert field is not None and box is not None
        confirm = next(b for b in dialog.findChildren(QPushButton) if b.text() == "OK")
        seen["opened_with"] = field.toPlainText()
        seen["opened_ticked"] = box.isChecked()
        field.setPlainText(model)
        box.setChecked(consent)
        seen["can_confirm"] = confirm.isEnabled()
        if accept and confirm.isEnabled():
            dialog.accept()
        else:
            dialog.reject()

    QTimer.singleShot(0, act)
    return seen


def test_cloud_consent_is_asked_once_and_survives_a_restart(qtbot: QtBot, tmp_path: Path) -> None:
    log = ShellLog(tmp_path / "logs")
    window = MainWindow(AppConfig(tmp_path), log)
    qtbot.addWidget(window)
    planner = window.planner_window
    assert planner is not None
    try:
        # Nothing may be confirmed without the tick, and what was typed is not thrown away.
        forgot = cloud_answer("gpt-5.4-mini", False)
        assert not window._cloud_ready(planner)
        assert forgot == {"opened_with": "", "opened_ticked": False, "can_confirm": False}
        assert planner.model.text() == "gpt-5.4-mini"

        # An identifier the provider would refuse is refused here, while it can still be fixed.
        spaced = cloud_answer("gpt 5.4 mini", True)
        assert not window._cloud_ready(planner)
        assert spaced["can_confirm"] is False
        assert planner.model.text() == "gpt-5.4-mini"

        given = cloud_answer("gpt-5.4-mini", True)
        assert window._cloud_ready(planner)
        assert given["opened_with"] == "gpt-5.4-mini" and given["can_confirm"] is True
        # Asked once: a second command never sees the dialog again.
        assert window._cloud_ready(planner)
    finally:
        window.shutdown()
        window.close()

    restarted = MainWindow(AppConfig(tmp_path), log)
    qtbot.addWidget(restarted)
    revived = restarted.planner_window
    assert revived is not None
    try:
        assert revived.cloud_consent.isChecked() and revived.model.text() == "gpt-5.4-mini"
        assert restarted._cloud_ready(revived)
        # Unticking in the planner takes the permission back for good.
        revived.cloud_consent.setChecked(False)
    finally:
        restarted.shutdown()
        restarted.close()

    revoked = MainWindow(AppConfig(tmp_path), log)
    qtbot.addWidget(revoked)
    asks_again = revoked.planner_window
    assert asks_again is not None
    try:
        assert not asks_again.cloud_consent.isChecked()
        # The identifier stays, so taking it back costs one tick rather than a retyped name.
        assert asks_again.model.text() == "gpt-5.4-mini"
        cancelled = cloud_answer("gpt-5.4-mini", True, accept=False)
        assert not revoked._cloud_ready(asks_again)
        assert cancelled["opened_with"] == "gpt-5.4-mini"
        assert "не подтверждён" in revoked.validation_label.text()
    finally:
        revoked.shutdown()
        revoked.close()


def test_declined_cloud_consent_starts_nothing(qtbot: QtBot, window: MainWindow) -> None:
    window.provider_mode.setCurrentIndex(1)
    cloud_answer("gpt-5.4-mini", True, accept=False)
    start(qtbot, window, "проверь систему")
    assert not window.running and window.state == UiState.IDLE
    assert "не подтверждён" in window.validation_label.text()
