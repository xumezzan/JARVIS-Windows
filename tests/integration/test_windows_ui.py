"""Window selection and exact click approval with a portable backend probe."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot
from tests.windows_support import WindowsProbe

from jarvis.ui.permission_workbench import PermissionWorkbench


def choose(window: PermissionWorkbench, tool: str) -> None:
    window.tool_choice.setCurrentIndex(window.tool_choice.findData(tool))


def test_window_selection_preview_and_readback(qtbot: QtBot, tmp_path: Path) -> None:
    probe = WindowsProbe()
    window = PermissionWorkbench(tmp_path, windows_backend=probe)
    qtbot.addWidget(window)
    window.show()
    try:
        window.simulation.setChecked(False)
        choose(window, "windows.get_open_windows")
        with qtbot.waitSignal(window.task_finished):
            QTest.mouseClick(window.run_button, Qt.MouseButton.LeftButton)
        assert window.target_choice.count() == 1
        choose(window, "windows.type_text")
        window.body.setPlainText("Jarvis integration test")
        assert probe.text == ""
        # Typing is ROUTINE: it runs straight away, into the window just observed.
        with qtbot.waitSignal(window.task_finished):
            QTest.mouseClick(window.run_button, Qt.MouseButton.LeftButton)
        assert window.approval_dialog is None
        assert "прочитан обратно" in window.status_label.text()
        assert probe.text == "Jarvis integration test"
        assert window.target_choice.count() == 0
    finally:
        window.shutdown()
        window.close()


def test_typing_without_observed_target_does_not_prepare(qtbot: QtBot, tmp_path: Path) -> None:
    probe = WindowsProbe()
    window = PermissionWorkbench(tmp_path, windows_backend=probe)
    qtbot.addWidget(window)
    try:
        choose(window, "windows.type_text")
        window.prepare()
        assert "Сначала" in window.status_label.text()
        assert window.action is None and window.approval_dialog is None
        assert not probe.calls
    finally:
        window.shutdown()
        window.close()
