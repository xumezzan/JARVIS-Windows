"""MVP command routing through real policy in simulation, with no Windows/network effects."""

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.unit.test_planner import Scripted
from tests.windows_support import WindowsProbe

from jarvis.config import AppConfig
from jarvis.core.planner.offline import call
from jarvis.ui.planner_window import PlannerWindow


@pytest.mark.e2e
@pytest.mark.parametrize("cancel", [False, True])
def test_scripted_browser_search_reaches_no_adapter_in_simulation(
    qtbot: QtBot,
    tmp_path: Path,
    cancel: bool,
) -> None:
    """An explicit synthetic planner chooses search; simulation never launches another Chrome."""
    probe = WindowsProbe()
    window = PlannerWindow(
        AppConfig(tmp_path),
        windows_backend=probe,
        provider=Scripted([call("browser.search", {"query": "OpenAI"})]),
    )
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
            assert "browser.search: SIMULATED" in window.output.toPlainText()
            assert window.last_result is not None
            assert [step.tool for step in window.last_result.steps] == ["browser.search"]
    finally:
        window.shutdown()
