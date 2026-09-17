"""Opt-in acceptance on a real Windows desktop. Never closes or discards user documents."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from jarvis.ui.permission_workbench import PermissionWorkbench

pytestmark = [pytest.mark.windows, pytest.mark.e2e]


def run_tool(qtbot: QtBot, window: PermissionWorkbench, tool: str) -> None:
    window.tool_choice.setCurrentIndex(window.tool_choice.findData(tool))
    with qtbot.waitSignal(window.task_finished, timeout=35000):
        QTest.mouseClick(window.run_button, Qt.MouseButton.LeftButton)


def test_notepad_open_focus_literal_text_and_readback(qtbot: QtBot, tmp_path: Path) -> None:
    assert QApplication.platformName() == "windows", "Use QT_QPA_PLATFORM=windows"
    window = PermissionWorkbench(tmp_path)
    qtbot.addWidget(window)
    window.show()
    try:
        window.simulation.setChecked(False)
        run_tool(qtbot, window, "windows.get_open_windows")
        assert window.status_label.text().startswith("SUCCESS")
        if window.target_choice.count():
            pytest.skip("Save and close existing Notepad windows manually before this test.")
        run_tool(qtbot, window, "windows.open_app")
        assert window.status_label.text().startswith("SUCCESS")
        target = window.target_choice.currentData()
        assert target is not None and target.empty and target.editor is not None, (
            "Notepad restored content or this editor is unsupported; nothing is overwritten."
        )
        run_tool(qtbot, window, "windows.focus_app")
        assert window.status_label.text().startswith("SUCCESS")
        window.tool_choice.setCurrentIndex(window.tool_choice.findData("windows.type_text"))
        window.body.setPlainText("Jarvis test successful")
        QTest.mouseClick(window.run_button, Qt.MouseButton.LeftButton)
        dialog = window.approval_dialog
        assert dialog is not None and dialog.token is None and window.worker is None
        with qtbot.waitSignal(window.task_finished, timeout=35000):
            QTest.mouseClick(dialog.approve_button, Qt.MouseButton.LeftButton)
        assert "SUCCESS" in window.status_label.text()
        assert "прочитан обратно" in window.status_label.text()
        # Refresh observes nonempty; a second preparation must not overwrite it.
        run_tool(qtbot, window, "windows.get_open_windows")
        assert not window.target_choice.currentData().empty
        run_tool(qtbot, window, "windows.type_text")
        assert window.status_label.text().startswith("INVALID")
    finally:
        window.shutdown()
        window.close()


@pytest.mark.parametrize("app", ["chrome", "code"])
def test_supported_app_open_focus_or_missing(qtbot: QtBot, tmp_path: Path, app: str) -> None:
    assert QApplication.platformName() == "windows", "Use QT_QPA_PLATFORM=windows"
    window = PermissionWorkbench(tmp_path)
    qtbot.addWidget(window)
    window.show()
    try:
        window.simulation.setChecked(False)
        shortcut = window.app_choice.findData(app)
        # Say which side is wrong. A missing shortcut leaves the box empty, and an empty
        # name is refused as an argument, so the gate would otherwise report INVALID and
        # look like the application failed to open.
        assert shortcut >= 0, f"The workbench offers no shortcut for {app!r}."
        window.app_choice.setCurrentIndex(shortcut)
        run_tool(qtbot, window, "windows.open_app")
        if "не найдено" in window.status_label.text():
            pytest.skip(f"Install {app} in a supported location to verify opening and focus.")
        assert window.status_label.text().startswith("SUCCESS")
        run_tool(qtbot, window, "windows.focus_app")
        assert window.status_label.text().startswith("SUCCESS")
    finally:
        window.shutdown()
        window.close()
