"""The briefing in the real window: one button, the answer on screen, nothing invented."""

from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from jarvis.config import AppConfig
from jarvis.core.meeting import Briefing, Client, Line, Section
from jarvis.mail.models import Account
from jarvis.ui.planner_window import PlannerWindow

ACCOUNT = Account(user_id="fixture-user", address="owner@example.test", session="a" * 32)
READY = Briefing(
    "ready",
    "Ревью Альфы",
    "2026-09-18T14:00:00Z",
    (Client("john@acme.test", "Джон Смит"),),
    (
        Section("meeting", "found", (Line("calendar.list", "evt1", "«Ревью Альфы»"),)),
        Section("calls", "found", (Line("fireflies.search", "ff1", "«Звонок с Альфой»"),)),
        Section("tasks", "empty"),
        Section("mail", "unavailable"),
        Section("page", "found", (Line("notion.search", "page1", "«Клиент Acme»"),)),
    ),
)


class FakeBriefer:
    """Stands in for the walk itself, so the window is tested and no service is called."""

    def __init__(self, briefing: Briefing = READY) -> None:
        self.briefing = briefing
        self.accounts: list[str] = []

    async def prepare(self, account: dict[str, object], cancelled: Event) -> Briefing:
        self.accounts.append(str(account.get("address")))
        return self.briefing


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path) -> PlannerWindow:
    built = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(built)
    built.show()
    # The window speaks its summary; a test has no speaker and no need of one.
    built.voice.speech_enabled.setChecked(False)
    # The button is switched off while the memory panel reads its store, exactly as the
    # planner's own is. The status line is the honest signal that the read has happened:
    # waiting on the worker alone can catch the moment before it starts.
    qtbot.waitUntil(lambda: "Загрузка" not in built.memory.status.text(), timeout=15000)
    qtbot.waitUntil(lambda: built.briefing_button.isEnabled(), timeout=15000)
    return built


def test_the_briefing_asks_for_an_account_before_it_gathers_anything(
    qtbot: QtBot, window: PlannerWindow
) -> None:
    try:
        QTest.mouseClick(window.briefing_button, Qt.MouseButton.LeftButton)
        # The meeting comes from the calendar, and the calendar comes with the sign-in.
        assert window.briefing_worker is None
        assert "Outlook" in window.status.text()
        assert window.output.toPlainText() == ""
    finally:
        window.shutdown()


def test_one_button_puts_the_whole_briefing_on_screen_with_its_sources(
    qtbot: QtBot, window: PlannerWindow
) -> None:
    briefer = FakeBriefer()
    window.briefer = briefer  # type: ignore[assignment]
    window.mail_session.account = ACCOUNT
    try:
        QTest.mouseClick(window.briefing_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: window.briefing_worker is None, timeout=15000)
        shown = window.output.toPlainText()
        assert briefer.accounts == ["owner@example.test"]
        # Every line names the tool and the record it came from, on the screen itself.
        assert "calendar.list · evt1" in shown and "notion.search · page1" in shown
        # A section nobody could read says so instead of looking like an empty one.
        assert "Открытые задачи: открытых задач не нашлось." in shown
        assert "Письма от этого человека: сервис недоступен" in shown
        assert "источником" in window.status.text()
    finally:
        window.shutdown()


def test_a_briefing_that_broke_says_so_and_leaves_the_window_usable(
    qtbot: QtBot, window: PlannerWindow
) -> None:
    class Broken(FakeBriefer):
        async def prepare(self, account: dict[str, object], cancelled: Event) -> Briefing:
            raise RuntimeError("fixture")

    window.briefer = Broken()  # type: ignore[assignment]
    window.mail_session.account = ACCOUNT
    try:
        QTest.mouseClick(window.briefing_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: window.briefing_worker is None, timeout=15000)
        assert "не удалось" in window.status.text()
        # The window is not left switched off by a failure in one of its buttons.
        assert window.run_button.isEnabled() and window.briefing_button.isEnabled()
    finally:
        window.shutdown()
