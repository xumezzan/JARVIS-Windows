"""Connecting a service in the real window, without a terminal and without a real key."""

from contextlib import closing
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QLineEdit
from pytestqt.qtbot import QtBot

from jarvis.core.planner.contracts import ProviderError
from jarvis.mail.session import MailSession
from jarvis.observability.audit import AuditLog
from jarvis.security.credentials import SERVICES
from jarvis.ui.connections_panel import ConnectionsPanel


class FakeStore:
    """Stands in for the killable helper, so no child process and no real vault is used."""

    def __init__(self, stored: set[str] | None = None) -> None:
        self.stored = set(stored or ())
        self.saved: list[tuple[str, str]] = []
        self.forgotten: list[str] = []
        self.fail = False

    async def has(self, provider: str) -> bool:
        return provider in self.stored

    async def store(self, provider: str, key: str) -> None:
        if self.fail:
            raise ProviderError("credentials")
        self.saved.append((provider, key))
        self.stored.add(provider)

    async def forget(self, provider: str) -> None:
        self.forgotten.append(provider)
        self.stored.discard(provider)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    fake = FakeStore({"deepseek"})
    monkeypatch.setattr("jarvis.ui.connections_panel.has_api_key", fake.has)
    monkeypatch.setattr("jarvis.ui.connections_panel.store_api_key", fake.store)
    monkeypatch.setattr("jarvis.ui.connections_panel.forget_api_key", fake.forget)
    return fake


def settled(qtbot: QtBot, panel: ConnectionsPanel) -> None:
    qtbot.waitUntil(lambda: not panel.busy and not panel.queue, timeout=10000)


def opened(qtbot: QtBot) -> ConnectionsPanel:
    """The panel as the owner meets it: built behind a tab, then opened."""
    panel = ConnectionsPanel()
    qtbot.addWidget(panel)
    panel.show()
    settled(qtbot, panel)
    return panel


def test_nothing_is_asked_of_the_store_until_the_tab_is_opened(
    qtbot: QtBot, store: FakeStore
) -> None:
    panel = ConnectionsPanel()
    qtbot.addWidget(panel)
    # A window builds this panel behind a tab. Asking about every service at construction
    # spends one short-lived process per service on a screen nobody has opened, every time
    # a window appears - and on a slow machine that is seconds taken from the actual task.
    assert not panel.busy and not panel.queue and not panel.asked
    assert panel.states["deepseek"].text() == "не проверено"
    panel.show()
    settled(qtbot, panel)
    assert panel.states["deepseek"].text() == "подключён"
    panel.shutdown()


def test_the_panel_says_which_services_are_connected(qtbot: QtBot, store: FakeStore) -> None:
    panel = opened(qtbot)
    assert panel.states["deepseek"].text() == "подключён"
    for provider in SERVICES:
        if provider != "deepseek":
            assert panel.states[provider].text() == "не подключён"
    panel.shutdown()


def test_a_key_typed_here_reaches_the_store_and_leaves_the_screen(
    qtbot: QtBot, store: FakeStore
) -> None:
    panel = opened(qtbot)
    panel.fields["openai"].setText("synthetic-noncredential")
    panel.save("openai")
    settled(qtbot, panel)
    assert store.saved == [("openai", "synthetic-noncredential")]
    # Nothing is left behind to be read over a shoulder, and the status never quotes it.
    assert panel.fields["openai"].text() == ""
    assert "synthetic" not in panel.status.text()
    assert panel.states["openai"].text() == "подключён"
    panel.shutdown()


def test_the_field_never_shows_what_is_typed_into_it(qtbot: QtBot, store: FakeStore) -> None:
    panel = opened(qtbot)
    for field in panel.fields.values():
        assert field.echoMode() is QLineEdit.EchoMode.Password
    panel.shutdown()


def test_an_empty_field_asks_for_a_key_instead_of_calling_the_store(
    qtbot: QtBot, store: FakeStore
) -> None:
    panel = opened(qtbot)
    panel.save("fireflies")
    settled(qtbot, panel)
    assert store.saved == []
    assert "Введите ключ" in panel.status.text()
    panel.shutdown()


def test_forgetting_a_key_removes_it_and_says_so(qtbot: QtBot, store: FakeStore) -> None:
    panel = opened(qtbot)
    panel.forget("deepseek")
    settled(qtbot, panel)
    assert store.forgotten == ["deepseek"]
    assert panel.states["deepseek"].text() == "не подключён"
    assert "удалён" in panel.status.text()
    panel.shutdown()


def test_a_store_that_refuses_says_so_and_still_clears_the_field(
    qtbot: QtBot, store: FakeStore
) -> None:
    panel = opened(qtbot)
    store.fail = True
    panel.fields["fireflies"].setText("synthetic-noncredential")
    panel.save("fireflies")
    settled(qtbot, panel)
    assert store.saved == [] and panel.fields["fireflies"].text() == ""
    assert "не подошёл" in panel.status.text()
    panel.shutdown()


def test_rechecking_asks_about_every_service_one_helper_at_a_time(
    qtbot: QtBot, store: FakeStore
) -> None:
    panel = opened(qtbot)
    store.stored.add("fireflies")
    QTest.mouseClick(panel.recheck, Qt.MouseButton.LeftButton)
    # One errand runs at a time: several helpers at once would be several vault calls at once.
    assert panel.busy and len(panel.queue) == len(SERVICES) - 1
    settled(qtbot, panel)
    assert panel.states["fireflies"].text() == "подключён"
    panel.shutdown()


def test_the_screen_says_excel_needs_a_work_account_before_the_consent(
    qtbot: QtBot, store: FakeStore, tmp_path: Path
) -> None:
    """A platform boundary the owner cannot discover from the refusal it produces.

    Microsoft does not serve the Excel API on a personal OneDrive, so there the workbook is
    found and every read of its cells is refused. Said next to the button that asks for the
    consent, it is a choice of account; said afterwards, it looks like a broken connector.
    """
    # The Microsoft section exists only where the session does, and so does its consent.
    # The log is closed here rather than left to the collector: an audit that outlives its
    # test is an open handle, and this suite turns every ResourceWarning into a failure -
    # of whichever test happens to be running when the collector reaches it.
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        panel = ConnectionsPanel(session=MailSession(), audit=audit)
        qtbot.addWidget(panel)
        panel.show()
        settled(qtbot, panel)
        labels = [
            widget.text()
            for widget in panel.findChildren(QLabel)
            if "Excel" in widget.text() or "OneDrive" in widget.text()
        ]
        said = [text for text in labels if "рабочем или учебном" in text]
        assert said, labels
        assert any("личном OneDrive" in text for text in said)
