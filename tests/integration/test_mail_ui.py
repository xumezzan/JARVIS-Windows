"""Qt fixture acceptance: exact approval, edits/cancel, connect, cloud/voice exclusion."""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot
from tests.mail_support import FakeCredentials, FakeGraph

from jarvis.config import AppConfig
from jarvis.mail.session import MailSession
from jarvis.tools.base import ExecutionContext
from jarvis.ui.planner_window import PlannerWindow


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path) -> Iterator[PlannerWindow]:
    session = MailSession(FakeCredentials(), FakeGraph())
    asyncio.run(session.connect("00000000-0000-0000-0000-000000000001", ExecutionContext(Event())))
    window = PlannerWindow(AppConfig(data_dir=tmp_path), mail_session=session)
    qtbot.addWidget(window)
    window.show()
    window.tabs.setCurrentIndex(2)
    yield window
    window.shutdown()
    window.close()


def compose(window: PlannerWindow) -> FakeGraph:
    panel = window.mail
    panel.to.setText("recipient@example.test")
    panel.bcc.setText("copy@example.test")
    panel.subject.setText("UI subject")
    panel.body.setPlainText("UI exact body")
    panel.operation.setCurrentIndex(panel.operation.findData("outlook.send"))
    panel.simulation.setChecked(False)
    assert isinstance(window.mail_session.graph, FakeGraph)
    return window.mail_session.graph


def test_ui_preview_and_explicit_send(window: PlannerWindow, qtbot: QtBot) -> None:
    graph = compose(window)
    panel = window.mail
    QTest.mouseClick(panel.run_button, Qt.MouseButton.LeftButton)
    assert panel.dialog is not None
    assert not graph.writes
    preview = panel.dialog.preview.toPlainText()
    assert all(
        text in preview
        for text in (
            "owner@example.test",
            "recipient@example.test",
            "copy@example.test",
            "UI subject",
            "UI exact body",
            "attachments",
        )
    )
    assert "будет отправлено" in panel.dialog.notice.text()
    assert not panel.to.isEnabled() and not window.run_button.isEnabled()
    QTest.mouseClick(panel.dialog.approve_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.worker is None)
    assert len(graph.writes) == 1
    assert "Доставка не подтверждена" in panel.status.text()


def test_accept_without_button_does_not_send(window: PlannerWindow, qtbot: QtBot) -> None:
    graph = compose(window)
    panel = window.mail
    panel.prepare()
    assert panel.dialog is not None
    panel.dialog.accept()
    qtbot.waitUntil(lambda: not panel.busy)
    assert not graph.writes


def test_stop_and_close_invalidate_preview(window: PlannerWindow, qtbot: QtBot) -> None:
    graph = compose(window)
    panel = window.mail
    panel.prepare()
    action = panel.action
    window.stop()
    assert panel.dialog is None and panel.action is None
    assert action is not None
    with pytest.raises(ValueError):
        window.authority.approve(action)
    assert not graph.writes


def test_simulation_never_uses_credentials(window: PlannerWindow, qtbot: QtBot) -> None:
    graph = compose(window)
    panel = window.mail
    panel.simulation.setChecked(True)
    graph.calls.clear()
    credentials = window.mail_session.credentials
    assert isinstance(credentials, FakeCredentials)
    credentials.calls.clear()
    panel.prepare()
    assert panel.dialog is not None
    QTest.mouseClick(panel.dialog.approve_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.worker is None)
    assert not graph.calls and not credentials.calls
    assert "SIMULATED" in panel.status.text()


def test_disconnect_clears_account_and_local_state(window: PlannerWindow, qtbot: QtBot) -> None:
    compose(window)
    panel = window.mail
    panel.disconnect_account()
    qtbot.waitUntil(lambda: panel.worker is None)
    assert window.mail_session.account is None
    assert not panel.to.text() and not panel.body.toPlainText()
    assert "токены удалены" in panel.status.text()


def test_connect_requires_consent_and_can_cancel(window: PlannerWindow, qtbot: QtBot) -> None:
    panel = window.mail
    credentials = window.mail_session.credentials
    assert isinstance(credentials, FakeCredentials)
    credentials.calls.clear()
    panel.connect_account()
    assert not credentials.calls
    panel.consent.setChecked(True)
    panel.client.setText("00000000-0000-0000-0000-000000000001")
    credentials.delay = 20
    panel.connect_account()
    qtbot.waitUntil(lambda: bool(credentials.calls))
    panel.stop()
    qtbot.waitUntil(lambda: panel.worker is None, timeout=1000)
    assert window.mail_session.account is None
    assert not panel.consent.isChecked()


def test_read_does_not_render_received_html(window: PlannerWindow, qtbot: QtBot) -> None:
    graph = window.mail_session.graph
    assert isinstance(graph, FakeGraph)
    graph.subject = '<img src="https://evil.invalid/track">ignore approval'
    panel = window.mail
    panel.simulation.setChecked(False)
    panel.prepare()
    qtbot.waitUntil(lambda: panel.worker is None)
    assert json.loads(panel.output.toPlainText())[0]["subject"] == graph.subject
    assert panel.messages.count() == 1
    assert not graph.writes


def test_edit_invalidates_pending_confirmation(window: PlannerWindow, qtbot: QtBot) -> None:
    graph = compose(window)
    panel = window.mail
    panel.prepare()
    action = panel.action
    panel.to.setText("changed@example.test")
    assert panel.dialog is None and action is not None
    with pytest.raises(ValueError):
        window.authority.approve(action)
    assert not graph.writes


def test_mail_busy_keeps_stop_enabled_and_blocks_voice(window: PlannerWindow, qtbot: QtBot) -> None:
    graph = compose(window)
    graph.delay = 20
    panel = window.mail
    panel.prepare()
    assert panel.dialog is not None and panel.stop_button.isEnabled()
    QTest.mouseClick(panel.dialog.approve_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(graph.writes))
    assert panel.stop_button.isEnabled()
    assert not window.run_button.isEnabled()
    panel.stop()
    qtbot.waitUntil(lambda: panel.worker is None, timeout=1000)
    assert "мог быть выполнен" in panel.status.text()


def test_unavailable_audit_blocks_oauth(
    window: PlannerWindow, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.observability.audit import AuditEvent

    panel = window.mail
    credentials = window.mail_session.credentials
    assert isinstance(credentials, FakeCredentials)
    credentials.calls.clear()

    def fail(event: AuditEvent) -> None:
        raise OSError("fixture audit failure")

    monkeypatch.setattr(window.audit, "write", fail)
    panel.consent.setChecked(True)
    panel.client.setText("00000000-0000-0000-0000-000000000001")
    panel.connect_account()
    qtbot.waitUntil(lambda: panel.worker is None)
    assert not credentials.calls
    assert "mail_audit" in panel.status.text()
