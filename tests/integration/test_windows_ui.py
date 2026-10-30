"""Window selection and exact click approval with a portable backend probe."""

from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot
from tests.windows_support import WindowsProbe

from jarvis.observability.audit import ErrorCode
from jarvis.observability.diagnostics import Reason, failure_message
from jarvis.permissions.engine import Outcome
from jarvis.permissions.policies import Status
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


@pytest.mark.parametrize(
    "platform,supported", [("win32", True), ("darwin", True), ("linux", False)]
)
def test_desktop_platform_support_and_existing_tool_names(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, supported: bool
) -> None:
    monkeypatch.setattr("jarvis.ui.permission_workbench.sys.platform", platform)
    window = PermissionWorkbench(tmp_path, windows_backend=WindowsProbe())
    qtbot.addWidget(window)
    try:
        labels = " ".join(label.text() for label in window.findChildren(QLabel))
        assert ("только симуляция инструментов рабочего стола" in labels) is not supported
        assert window.tool_choice.findData("windows.get_open_windows") >= 0
        assert window.tool_choice.findData("windows.type_text") >= 0
        if platform == "darwin":
            assert window.app_choice.itemText(window.app_choice.findData("notepad")) == "TextEdit"
    finally:
        window.shutdown()
        window.close()


@pytest.mark.parametrize(
    "reason", [Reason.MACOS_ACCESSIBILITY_REQUIRED, Reason.MACOS_SCREEN_RECORDING_REQUIRED]
)
def test_native_permission_diagnostics_reach_the_workbench(
    qtbot: QtBot, tmp_path: Path, reason: Reason
) -> None:
    window = PermissionWorkbench(tmp_path, windows_backend=WindowsProbe())
    qtbot.addWidget(window)
    try:
        window._result_tool = "windows.type_text"
        window._show_outcome(
            Outcome(uuid4(), Status.ERROR, ErrorCode.CONTROL_UNSUPPORTED, reason=reason)
        )
        assert (
            failure_message(ErrorCode.CONTROL_UNSUPPORTED.value, reason)
            in window.status_label.text()
        )
        assert "Редактор этой версии" not in window.status_label.text()
    finally:
        window.shutdown()
        window.close()


def test_uncertain_native_effect_is_reported_once(qtbot: QtBot, tmp_path: Path) -> None:
    window = PermissionWorkbench(tmp_path, windows_backend=WindowsProbe())
    qtbot.addWidget(window)
    try:
        window._result_tool = "windows.type_text"
        result = Outcome(uuid4(), Status.ERROR, ErrorCode.VERIFICATION, may_have_effects=True)
        window._show_outcome(result)
        assert window.status_label.text() == (
            "ERROR — " + failure_message(result.error.value, result.reason, True)
        )
    finally:
        window.shutdown()
        window.close()
