"""Real Qt event-loop and worker tests; no mocked execution adapters."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from jarvis.config import AppConfig
from jarvis.observability.logging import ShellLog
from jarvis.ui.main_window import MainWindow
from jarvis.ui.states import UiState

pytestmark = pytest.mark.integration


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path) -> Iterator[MainWindow]:
    log = ShellLog(tmp_path / "logs")
    shell = MainWindow(AppConfig(tmp_path, demo_duration_ms=200), log)
    qtbot.addWidget(shell)
    shell.show()
    yield shell
    shell.shutdown()
    shell.close()


def start(qtbot: QtBot, window: MainWindow, text: str = "Проверь интерфейс") -> None:
    window.command_input.setPlainText(text)
    QTest.mouseClick(window.submit_button, Qt.MouseButton.LeftButton)


def test_startup_and_unavailable_capabilities(window: MainWindow) -> None:
    assert window.isVisible()
    assert window.state == UiState.IDLE
    assert window.microphone_button.isEnabled()
    assert window.planner_window is None  # Opening the app never starts microphone capture.
    assert window.permissions_button.isEnabled()
    assert not window.stop_button.isEnabled()


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


def test_text_success_and_ui_responsiveness(qtbot: QtBot, window: MainWindow) -> None:
    ticks: list[int] = []
    timer = QTimer(window)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    states: list[str] = []
    window.state_changed.connect(states.append)
    with qtbot.waitSignal(window.task_finished, timeout=3000):
        start(qtbot, window, "Открой Блокнот")
        assert window.state == UiState.THINKING
        assert not window.submit_button.isEnabled()
    timer.stop()
    assert len(ticks) >= 3
    assert states == ["thinking", "executing", "success"]
    assert window.transcript.toPlainText() == "Открой Блокнот"
    assert "не исполнялась" in window.action_label.text()
    assert window.worker is None
    assert window.submit_button.isEnabled()


def test_failure_then_retry(qtbot: QtBot, window: MainWindow) -> None:
    window.demo_mode.setCurrentIndex(1)
    with qtbot.waitSignal(window.task_finished, timeout=3000):
        start(qtbot, window)
    assert window.state == UiState.ERROR
    window.demo_mode.setCurrentIndex(0)
    with qtbot.waitSignal(window.task_finished, timeout=3000):
        start(qtbot, window)
    qtbot.waitUntil(lambda: window.state == UiState.SUCCESS)


@pytest.mark.parametrize("when", ["thinking", "executing"])
def test_stop_and_no_stale_success(qtbot: QtBot, window: MainWindow, when: str) -> None:
    window.config = AppConfig(window.config.data_dir, demo_duration_ms=1000)
    start(qtbot, window)
    if when == "executing":
        qtbot.waitUntil(lambda: window.state == UiState.EXECUTING)
    with qtbot.waitSignal(window.task_finished, timeout=2000):
        QTest.mouseClick(window.stop_button, Qt.MouseButton.LeftButton)
        window.stop()  # Repeated emergency stop is harmless.
    assert window.state == UiState.CANCELLED
    assert window.worker is None
    rows = window.log.path.read_text()
    assert rows.count('"event": "cancel_requested"') == 1
    assert "demo_succeeded" not in rows


def test_timeout(qtbot: QtBot, window: MainWindow) -> None:
    window.config = AppConfig(window.config.data_dir, demo_duration_ms=500, task_timeout_ms=100)
    with qtbot.waitSignal(window.task_finished, timeout=2000):
        start(qtbot, window)
    assert window.state == UiState.ERROR
    assert "время ожидания" in window.action_label.text()


def test_close_during_work_joins_thread(qtbot: QtBot, window: MainWindow) -> None:
    window.config = AppConfig(window.config.data_dir, demo_duration_ms=30000)
    start(qtbot, window)
    with qtbot.waitSignal(window.task_finished, timeout=2000):
        window.close()
    qtbot.waitUntil(lambda: not window.isVisible())
    assert window.worker is None
    assert "shell_closed" in window.log.path.read_text()


def test_direct_shutdown_during_work(window: MainWindow, qtbot: QtBot) -> None:
    start(qtbot, window)
    window.shutdown()
    assert window.worker is None
    assert window.state == UiState.CANCELLED


@pytest.mark.parametrize("text", ["   ", "x" * 4001])
def test_invalid_input_does_not_start(window: MainWindow, qtbot: QtBot, text: str) -> None:
    start(qtbot, window, text)
    assert window.worker is None
    assert window.state == UiState.IDLE
    assert "от 1 до 4 000" in window.validation_label.text()


def test_busy_submission_cannot_replace_task(qtbot: QtBot, window: MainWindow) -> None:
    with qtbot.waitSignal(window.task_finished, timeout=3000):
        start(qtbot, window, "Первая команда")
        window.submit()
    assert window.log.path.read_text().count('"event": "demo_submitted"') == 1


def test_arbitrary_command_is_plain_text_and_not_logged(qtbot: QtBot, window: MainWindow) -> None:
    text = '<img src="https://invalid.example/pixel">\nPRIVATE_INPUT_SENTINEL'
    with qtbot.waitSignal(window.task_finished, timeout=3000):
        start(qtbot, window, text)
    assert window.transcript.toPlainText() == text
    content = window.log.path.read_text()
    assert "PRIVATE_INPUT_SENTINEL" not in content
    assert "invalid.example" not in content
    rows = [json.loads(line) for line in content.splitlines()]
    requests = {row["request_id"] for row in rows if row["event"].startswith("demo_")}
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
    with qtbot.waitSignal(planner.task_finished):
        QTest.mouseClick(planner.run_button, Qt.MouseButton.LeftButton)
    planner.close()
    qtbot.waitUntil(lambda: window.planner_window is None)
    window.open_planner()
    assert window.planner_window is not None
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
