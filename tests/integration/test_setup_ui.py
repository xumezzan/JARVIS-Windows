"""The setup screen: what is connected, what is not, and the buttons that change it."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot
from tests.mail_support import FakeCredentials, FakeGraph

from jarvis.config import AppConfig
from jarvis.core.planner.contracts import ProviderError
from jarvis.mail.session import MailSession
from jarvis.observability.audit import AuditLog
from jarvis.security.credentials import SERVICES
from jarvis.ui.connections_panel import ConnectionsPanel
from jarvis.ui.setup_window import SetupWindow, first_run

CLIENT = "00000000-0000-0000-0000-000000000001"


class FakeStore:
    """Stands in for the killable helper, so no child process and no real vault is used."""

    def __init__(self, stored: set[str] | None = None) -> None:
        self.stored = set(stored or ())

    async def has(self, provider: str) -> bool:
        return provider in self.stored

    async def store(self, provider: str, key: str) -> None:
        self.stored.add(provider)

    async def forget(self, provider: str) -> None:
        self.stored.discard(provider)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    fake = FakeStore({"deepseek"})
    monkeypatch.setattr("jarvis.ui.connections_panel.has_api_key", fake.has)
    monkeypatch.setattr("jarvis.ui.connections_panel.store_api_key", fake.store)
    monkeypatch.setattr("jarvis.ui.connections_panel.forget_api_key", fake.forget)
    return fake


@pytest.fixture
def bench(qtbot: QtBot, tmp_path: Path, store: FakeStore) -> Iterator[tuple[SetupWindow, AuditLog]]:
    session = MailSession(FakeCredentials(), FakeGraph())
    audit = AuditLog(tmp_path / "audit.sqlite3")
    window = SetupWindow(session, audit, tmp_path)
    qtbot.addWidget(window)
    window.show()
    settled(qtbot, window.panel)
    yield window, audit
    window.shutdown()
    audit.close()


def settled(qtbot: QtBot, panel: ConnectionsPanel) -> None:
    qtbot.waitUntil(lambda: not panel.busy and not panel.queue, timeout=15000)


def test_the_screen_shows_every_service_and_the_account_in_one_place(
    bench: tuple[SetupWindow, AuditLog],
) -> None:
    window, _ = bench
    panel = window.panel
    # Everything Jarvis can reach, and the state of each: that is the whole point of it.
    assert set(panel.states) == set(SERVICES)
    assert panel.states["deepseek"].text() == "подключён"
    assert panel.states["openai"].text() == "не подключён"
    assert "не подключён" in panel.account_state.text()
    # A sign-in that has not happened cannot be given more surfaces.
    assert not panel.surface_buttons["teams"].isEnabled()


def test_a_sign_in_needs_the_consent_and_a_real_client_id(
    bench: tuple[SetupWindow, AuditLog],
) -> None:
    window, _ = bench
    panel = window.panel
    panel.client.setText(CLIENT)
    QTest.mouseClick(panel.sign_in, Qt.MouseButton.LeftButton)
    # No consent ticked: nothing was started, and the screen says what is missing.
    assert panel.account_worker is None and "согласие" in panel.status.text()
    panel.account_consent.setChecked(True)
    panel.client.setText("не uuid")
    QTest.mouseClick(panel.sign_in, Qt.MouseButton.LeftButton)
    assert panel.account_worker is None and "UUID" in panel.status.text()


def test_signing_in_connects_the_account_and_opens_its_surfaces(
    qtbot: QtBot, bench: tuple[SetupWindow, AuditLog]
) -> None:
    window, audit = bench
    panel = window.panel
    panel.client.setText(CLIENT)
    panel.account_consent.setChecked(True)
    QTest.mouseClick(panel.sign_in, Qt.MouseButton.LeftButton)
    settled(qtbot, panel)
    assert panel.connected_account == "owner@example.test"
    assert "подключён · owner@example.test" in panel.account_state.text()
    # Teams and OneDrive become possible only once there is an account to add them to.
    assert panel.surface_buttons["teams"].isEnabled()
    # The consent is not left ticked for the next press.
    assert not panel.account_consent.isChecked()
    # And the operation is in the audit log, from the one place that writes those records.
    # Newest first, as the log returns them.
    records = [json.loads(record) for record in audit.recent(100)]
    connection = [row for row in records if row.get("actor") == "user_ui_connection"]
    assert [row["event"] for row in connection] == ["finished", "started"]
    assert {row["tool"] for row in connection} == {"outlook.account"}


def test_each_surface_is_consented_on_its_own(
    qtbot: QtBot, bench: tuple[SetupWindow, AuditLog]
) -> None:
    window, _ = bench
    panel = window.panel
    panel.client.setText(CLIENT)
    panel.account_consent.setChecked(True)
    QTest.mouseClick(panel.sign_in, Qt.MouseButton.LeftButton)
    settled(qtbot, panel)
    credentials = panel.session.credentials if panel.session is not None else None
    assert isinstance(credentials, FakeCredentials)
    QTest.mouseClick(panel.surface_buttons["files"], Qt.MouseButton.LeftButton)
    settled(qtbot, panel)
    # The surface asked for is the one pressed, and it is asked for by name.
    assert credentials.calls[-1] == "consent" and credentials.surfaces[-1] == "files"
    assert "OneDrive разрешён" in panel.status.text()


def test_disconnecting_says_what_it_did_and_leaves_nothing_signed_in(
    qtbot: QtBot, bench: tuple[SetupWindow, AuditLog]
) -> None:
    window, _ = bench
    panel = window.panel
    panel.client.setText(CLIENT)
    panel.account_consent.setChecked(True)
    QTest.mouseClick(panel.sign_in, Qt.MouseButton.LeftButton)
    settled(qtbot, panel)
    QTest.mouseClick(panel.sign_out, Qt.MouseButton.LeftButton)
    settled(qtbot, panel)
    assert panel.connected_account == ""
    assert "не подключён" in panel.account_state.text()
    assert not panel.surface_buttons["teams"].isEnabled()


def test_a_key_still_goes_to_the_store_and_leaves_the_screen(
    qtbot: QtBot, bench: tuple[SetupWindow, AuditLog], store: FakeStore
) -> None:
    window, _ = bench
    panel = window.panel
    panel.fields["notion"].setText("synthetic-noncredential")
    panel.save("notion")
    settled(qtbot, panel)
    assert "notion" in store.stored
    assert panel.fields["notion"].text() == "" and panel.states["notion"].text() == "подключён"
    assert "synthetic" not in panel.status.text()


def test_a_screen_without_an_account_offers_no_sign_in(qtbot: QtBot, store: FakeStore) -> None:
    panel = ConnectionsPanel()
    qtbot.addWidget(panel)
    panel.show()
    settled(qtbot, panel)
    # Offering a button that cannot do anything is worse than not offering it.
    assert panel.session is None and panel.surface_buttons == {}
    panel.shutdown()


def test_the_screen_opens_by_itself_once_and_then_stays_a_button(
    qtbot: QtBot, tmp_path: Path
) -> None:
    assert first_run(tmp_path)
    session = MailSession(FakeCredentials(), FakeGraph())
    audit = AuditLog(tmp_path / "audit.sqlite3")
    try:
        window = SetupWindow(session, audit, tmp_path)
        qtbot.addWidget(window)
        # Shown once: the next start comes up on the dashboard, not on the setup screen.
        assert not first_run(tmp_path)
        window.shutdown()
    finally:
        audit.close()


def test_the_shell_opens_the_setup_screen_from_its_own_button(
    qtbot: QtBot, tmp_path: Path, store: FakeStore
) -> None:
    from jarvis.observability.logging import ShellLog
    from jarvis.ui.main_window import MainWindow

    log = ShellLog(tmp_path / "logs")
    window = MainWindow(AppConfig(data_dir=tmp_path), log)
    qtbot.addWidget(window)
    try:
        QTest.mouseClick(window.setup_button, Qt.MouseButton.LeftButton)
        assert window.setup_window is not None
        # The screen borrows the planner's session: signing in twice in one application
        # would leave two accounts and one of them unaudited.
        assert window.planner_window is not None
        assert window.setup_window.panel.session is window.planner_window.mail_session
        settled(qtbot, window.setup_window.panel)
    finally:
        window.shutdown()


def test_a_key_the_store_refuses_says_so(
    qtbot: QtBot, bench: tuple[SetupWindow, AuditLog], monkeypatch: pytest.MonkeyPatch
) -> None:
    window, _ = bench
    panel = window.panel

    async def refuse(provider: str, key: str) -> None:
        raise ProviderError("credentials")

    monkeypatch.setattr("jarvis.ui.connections_panel.store_api_key", refuse)
    panel.fields["asana"].setText("synthetic-noncredential")
    panel.save("asana")
    settled(qtbot, panel)
    assert panel.fields["asana"].text() == "" and "не подошёл" in panel.status.text()
