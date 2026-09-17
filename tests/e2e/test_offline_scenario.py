"""MVP command routing through real policy in simulation, with no Windows/network effects."""

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.windows_support import WindowsProbe

from jarvis.config import AppConfig
from jarvis.ui.planner_window import PlannerWindow


@pytest.mark.e2e
@pytest.mark.parametrize("cancel", [False, True])
def test_chrome_search_recipe_reaches_no_adapter_in_simulation(
    qtbot: QtBot,
    tmp_path: Path,
    cancel: bool,
) -> None:
    """Both steps are ROUTINE, so nothing pauses; simulation still touches nothing real."""
    probe = WindowsProbe()
    window = PlannerWindow(AppConfig(tmp_path), windows_backend=probe)
    qtbot.addWidget(window)
    window.show()
    try:
        window.command.setPlainText("Открой Chrome и найди OpenAI")
        with qtbot.waitSignal(window.task_finished, timeout=15000) as result:
            window.start()
            if cancel:
                window.stop()
        assert result.args == ["cancelled" if cancel else "simulated"]
        assert window.approval_dialog is None
        assert not probe.calls and window.host._thread is None
        if not cancel:
            assert "windows.open_app: SIMULATED" in window.output.toPlainText()
            assert "browser.search: SIMULATED" in window.output.toPlainText()
    finally:
        window.shutdown()
