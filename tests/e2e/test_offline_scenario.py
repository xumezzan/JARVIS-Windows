"""MVP command routing through real policy in simulation, with no Windows/network effects."""

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.windows_support import WindowsProbe

from jarvis.config import AppConfig
from jarvis.ui.planner_window import PlannerWindow


@pytest.mark.e2e
def test_chrome_search_recipe_simulates_without_approval(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    probe = WindowsProbe()
    window = PlannerWindow(AppConfig(tmp_path), windows_backend=probe)
    qtbot.addWidget(window)
    window.show()
    try:
        window.command.setPlainText("Открой Chrome и найди OpenAI")
        with qtbot.waitSignal(window.task_finished) as result:
            window.start()
        assert window.approval_dialog is None
        assert result.args == ["simulated"]
        assert not probe.calls and window.host._thread is None
        assert "windows.open_app" not in window.output.toPlainText()
        assert "browser.search: SIMULATED" in window.output.toPlainText()
    finally:
        window.shutdown()
