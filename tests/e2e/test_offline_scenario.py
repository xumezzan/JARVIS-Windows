"""MVP command routing through real policy in simulation, with no Windows/network effects."""

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.integration.test_planner_ui import click_approval
from tests.windows_support import WindowsProbe

from jarvis.config import AppConfig
from jarvis.ui.planner_window import PlannerWindow


@pytest.mark.e2e
@pytest.mark.parametrize("cancel", [False, True])
def test_chrome_search_recipe_preserves_browser_confirmation(
    qtbot: QtBot,
    tmp_path: Path,
    cancel: bool,
) -> None:
    probe = WindowsProbe()
    window = PlannerWindow(AppConfig(tmp_path), windows_backend=probe)
    qtbot.addWidget(window)
    window.show()
    try:
        window.command.setPlainText("Открой Chrome и найди OpenAI")
        window.start()
        qtbot.waitUntil(lambda: window.approval_dialog is not None)
        dialog = window.approval_dialog
        assert dialog is not None
        assert "OpenAI" in dialog.preview.toPlainText()
        assert not probe.calls and window.host._thread is None
        with qtbot.waitSignal(window.task_finished) as result:
            if cancel:
                window.stop()
            else:
                click_approval(window, qtbot)
        assert result.args == ["cancelled" if cancel else "simulated"]
        assert not probe.calls and window.host._thread is None
        assert "windows.open_app: SIMULATED" in window.output.toPlainText()
        if not cancel:
            assert "browser.search: SIMULATED" in window.output.toPlainText()
    finally:
        window.shutdown()
