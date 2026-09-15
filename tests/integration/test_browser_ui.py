"""Real Chromium with verified Qt button events and full browser action previews."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot
from tests.browser_support import FixtureSite

from jarvis.browser.host import BrowserHost
from jarvis.security.browser_policy import NetworkPolicy
from jarvis.ui.permission_workbench import PermissionWorkbench


def choose(window: PermissionWorkbench, tool: str) -> None:
    window.tool_choice.setCurrentIndex(window.tool_choice.findData("browser." + tool))


def approved_run(window: PermissionWorkbench, qtbot: QtBot) -> None:
    QTest.mouseClick(window.run_button, Qt.MouseButton.LeftButton)
    dialog = window.approval_dialog
    assert dialog is not None
    with qtbot.waitSignal(window.task_finished, timeout=15000):
        QTest.mouseClick(dialog.approve_button, Qt.MouseButton.LeftButton)
    assert "SUCCESS" in window.status_label.text(), window.status_label.text()


def test_browser_manual_preview_and_single_submit(qtbot: QtBot, tmp_path: Path) -> None:
    site = FixtureSite()
    host = BrowserHost(NetworkPolicy(fixture_origin=site.origin))
    window = PermissionWorkbench(tmp_path, browser_host=host)
    qtbot.addWidget(window)
    window.show()
    try:
        window.simulation.setChecked(False)
        choose(window, "open")
        window.browser_controls.url.setText(site.origin + "/")
        approved_run(window, qtbot)
        assert "Browser fixture" in window.browser_controls.observation.toPlainText()
        choose(window, "type")
        window.browser_controls.elements.setCurrentIndex(1)
        window.browser_controls.text.setPlainText("UI literal & сообщение")
        approved_run(window, qtbot)
        choose(window, "click")
        elements = window.browser_controls.elements
        elements.setCurrentIndex(elements.findText("button: Send"))
        QTest.mouseClick(window.run_button, Qt.MouseButton.LeftButton)
        dialog = window.approval_dialog
        assert dialog is not None and dialog.token is None
        assert "передаст запрос" in dialog.notice.text()
        preview = dialog.preview.toPlainText()
        for field in (
            "tab_id",
            "document_id",
            "origin",
            "frame",
            "fingerprint",
            "POST",
            "fields",
            "UI literal & сообщение",
        ):
            assert field in preview
        assert not any(row[0] == "POST" for row in site.seen)
        assert not window.browser_controls.isEnabled()
        with qtbot.waitSignal(window.task_finished, timeout=15000):
            QTest.mouseClick(dialog.approve_button, Qt.MouseButton.LeftButton)
        assert "SUCCESS" in window.status_label.text()
        assert len([row for row in site.seen if row[0] == "POST"]) == 1
        assert "Received" in window.browser_controls.observation.toPlainText()
    finally:
        window.shutdown()
        window.close()
        site.close()
    assert host._thread is not None and not host._thread.is_alive()


def test_browser_ui_simulation_and_unobserved_target(qtbot: QtBot, tmp_path: Path) -> None:
    host = BrowserHost()
    window = PermissionWorkbench(tmp_path, browser_host=host)
    qtbot.addWidget(window)
    try:
        choose(window, "open")
        QTest.mouseClick(window.run_button, Qt.MouseButton.LeftButton)
        dialog = window.approval_dialog
        assert dialog is not None
        with qtbot.waitSignal(window.task_finished):
            QTest.mouseClick(dialog.approve_button, Qt.MouseButton.LeftButton)
        assert "SIMULATED" in window.status_label.text()
        assert host._thread is None
        choose(window, "click")
        QTest.mouseClick(window.run_button, Qt.MouseButton.LeftButton)
        assert "Сначала" in window.status_label.text()
        assert window.approval_dialog is None
        assert host._thread is None
    finally:
        window.shutdown()
        window.close()
