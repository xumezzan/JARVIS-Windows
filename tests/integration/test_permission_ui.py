"""Click-driven approval boundary, stop/close races, and existing shell integration."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from jarvis.config import AppConfig
from jarvis.observability.logging import ShellLog
from jarvis.ui.main_window import MainWindow
from jarvis.ui.permission_workbench import PermissionWorkbench

pytestmark = pytest.mark.integration


@pytest.fixture
def workbench(qtbot: QtBot, tmp_path: Path) -> Iterator[PermissionWorkbench]:
    window = PermissionWorkbench(tmp_path)
    qtbot.addWidget(window)
    window.show()
    yield window
    window.shutdown()
    window.close()


def click_prepare(window: PermissionWorkbench) -> None:
    QTest.mouseClick(window.run_button, Qt.MouseButton.LeftButton)


def test_default_safe_simulation(qtbot: QtBot, workbench: PermissionWorkbench) -> None:
    with qtbot.waitSignal(workbench.task_finished):
        click_prepare(workbench)
    assert workbench.status_label.text().startswith("SIMULATED")
    assert workbench.outbox.count == 0


@pytest.mark.parametrize("simulation", [False, True])
def test_approve_exact_message(
    qtbot: QtBot,
    workbench: PermissionWorkbench,
    simulation: bool,
) -> None:
    workbench.tool_choice.setCurrentIndex(1)
    workbench.simulation.setChecked(simulation)
    workbench.body.setPlainText("PRIVATE_UI_SENTINEL <b>literal text</b>")
    click_prepare(workbench)
    dialog = workbench.approval_dialog
    assert dialog is not None and dialog.isVisible()
    assert dialog.token is None
    assert workbench.worker is None and workbench.outbox.count == 0
    preview = dialog.preview.toPlainText()
    for field in (
        "account",
        "recipient",
        "subject",
        "body",
        "attachments",
        "action_type",
        "service",
        "mode",
    ):
        assert field in preview
    assert "PRIVATE_UI_SENTINEL <b>literal text</b>" in preview
    assert not workbench.body.isEnabled()
    with qtbot.waitSignal(workbench.task_finished):
        QTest.mouseClick(dialog.approve_button, Qt.MouseButton.LeftButton)
    assert workbench.outbox.count == (0 if simulation else 1)
    assert workbench.status_label.text().startswith("SIMULATED" if simulation else "SUCCESS")
    assert "PRIVATE_UI_SENTINEL" not in "".join(workbench.audit.recent())


@pytest.mark.parametrize("how", ["reject", "accept_without_click", "stop", "close"])
def test_no_approval_on_cancel_or_programmatic_accept(
    qtbot: QtBot,
    workbench: PermissionWorkbench,
    how: str,
) -> None:
    workbench.tool_choice.setCurrentIndex(1)
    workbench.simulation.setChecked(False)
    click_prepare(workbench)
    dialog = workbench.approval_dialog
    assert dialog is not None
    with qtbot.waitSignal(workbench.task_finished):
        if how == "reject":
            QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
        elif how == "accept_without_click":
            dialog.accept()
        elif how == "stop":
            workbench.stop()
        else:
            workbench.close()
    assert workbench.outbox.count == 0
    assert workbench.worker is None
    assert dialog.token is None


def test_expired_preview_cannot_approve(qtbot: QtBot, workbench: PermissionWorkbench) -> None:
    workbench.tool_choice.setCurrentIndex(1)
    click_prepare(workbench)
    dialog = workbench.approval_dialog
    assert dialog is not None and workbench.action is not None
    workbench.engine.cancel(workbench.action)
    QTest.mouseClick(dialog.approve_button, Qt.MouseButton.LeftButton)
    assert dialog.token is None
    assert "недоступно" in dialog.error_label.text()
    assert workbench.worker is None


@pytest.mark.parametrize("index", [2, 3])
def test_disabled_risks_never_open_approval(
    qtbot: QtBot,
    workbench: PermissionWorkbench,
    index: int,
) -> None:
    workbench.tool_choice.setCurrentIndex(index)
    with qtbot.waitSignal(workbench.task_finished):
        click_prepare(workbench)
    assert workbench.status_label.text().startswith("DENIED")
    assert workbench.approval_dialog is None and workbench.worker is None


@pytest.mark.parametrize("close", [False, True])
def test_stop_or_close_immediately_after_start(
    qtbot: QtBot,
    workbench: PermissionWorkbench,
    close: bool,
) -> None:
    workbench.simulation.setChecked(False)
    with qtbot.waitSignal(workbench.task_finished):
        click_prepare(workbench)
        if close:
            workbench.close()
        else:
            workbench.stop()
    assert workbench.worker is None
    assert "CANCELLED" in workbench.status_label.text()


def test_parent_shutdown_closes_pending_approval(qtbot: QtBot, tmp_path: Path) -> None:
    log = ShellLog(tmp_path / "logs")
    window = MainWindow(AppConfig(tmp_path), log)
    qtbot.addWidget(window)
    window.show()
    QTest.mouseClick(window.permissions_button, Qt.MouseButton.LeftButton)
    child = window.permission_workbench
    assert child is not None
    child.tool_choice.setCurrentIndex(1)
    click_prepare(child)
    assert child.approval_dialog is not None
    window.shutdown()
    qtbot.waitUntil(lambda: child.worker is None and child.approval_dialog is None)
    assert child.outbox.count == 0
    window.close()
