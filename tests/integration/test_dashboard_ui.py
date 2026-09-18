"""The main screen: the command first, the work under it, and nothing that does nothing."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from jarvis.config import AppConfig
from jarvis.observability.logging import ShellLog
from jarvis.ui.home import ACTIONS, APPS, ConnectedApps, SummaryCard
from jarvis.ui.main_window import MainWindow

PATIENCE = 15000


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path) -> Iterator[MainWindow]:
    built = MainWindow(AppConfig(data_dir=tmp_path), ShellLog(tmp_path / "logs"))
    qtbot.addWidget(built)
    built.show()
    planner = built.planner_window
    assert planner is not None
    qtbot.waitUntil(lambda: planner.memory.profile_read, timeout=PATIENCE)
    yield built
    built.shutdown()


def test_the_command_is_the_screen_and_the_rest_waits_its_turn(window: MainWindow) -> None:
    # Nothing has run, so the card that shows work is not an empty box on the screen, and
    # neither is the control that would stop it.
    assert not window.task_card.isVisible()
    assert window.examples_box.isVisible()
    assert not window.stop_button.isVisible()
    # The four controls are one line of words until the owner asks for them.
    assert not window.mode_box.isVisible()
    assert window.mode_label.text() == "делаю сам · офлайн-модель"
    assert window.command_input.placeholderText() == "Скажите, что сделать"
    # The greeting is by the name the owner gave, and no name was given here.
    assert window.greeting_title.text() == "Привет"


def test_the_mode_line_says_how_it_will_work_and_opens_on_request(
    qtbot: QtBot, window: MainWindow
) -> None:
    QTest.mouseClick(window.mode_button, Qt.MouseButton.LeftButton)
    assert window.mode_box.isVisible() and window.mode_button.text() == "свернуть"
    window.autonomy.setChecked(False)
    window.run_mode.setCurrentIndex(1)
    # Autonomy stays a visible, explicit setting: the line says what it is now.
    assert window.mode_label.text() == "спрашиваю на каждом шаге · офлайн-модель · только симуляция"
    QTest.mouseClick(window.mode_button, Qt.MouseButton.LeftButton)
    assert not window.mode_box.isVisible() and window.mode_button.text() == "изменить"


def test_a_quick_action_fills_the_command_instead_of_running_it(window: MainWindow) -> None:
    # The tile puts a whole sentence in the line; nothing leaves the window until it is sent.
    tile = window.quick_actions.tiles["Календарь"]
    QTest.mouseClick(tile, Qt.MouseButton.LeftButton)
    assert window.command_input.toPlainText() == "Покажи мои встречи на сегодня"
    assert not window.running
    # And the short starters do the same with the beginning of one.
    QTest.mouseClick(window.starter_buttons["Найти в сети"], Qt.MouseButton.LeftButton)
    assert window.command_input.toPlainText() == "Найди в интернете "


def test_the_owner_is_greeted_by_the_name_they_typed(window: MainWindow) -> None:
    window.name_input.setText("Хумоюн")
    window._remember_preferences()
    assert window.greeting_title.text() == "Привет, Хумоюн"
    assert window.avatar.text() == "Х"
    # It survives the next launch, because a name asked for twice is a name asked once too often.
    assert (window.config.data_dir / "home.json").exists()
    # A label that is not a name is not kept, and the greeting goes back to having none.
    window.name_input.setText("token=42")
    window._remember_preferences()
    assert window.greeting_title.text() == "Привет"


def test_a_running_task_replaces_the_actions_with_its_own_steps(
    qtbot: QtBot, window: MainWindow
) -> None:
    window.run_mode.setCurrentIndex(1)
    window.command_input.setPlainText("проверь систему дважды")
    with qtbot.waitSignal(window.task_finished, timeout=PATIENCE):
        window.submit()
    # The steps the planner walked are on the screen the owner was looking at.
    assert window.task_card.isVisible() and not window.examples_box.isVisible()
    steps = [window.checklist.rows.item(row).text() for row in range(window.checklist.rows.count())]
    assert len(steps) == 2 and all("local.check" in step for step in steps)
    assert window.transcript.toPlainText() == "проверь систему дважды"
    # The record counted it, and the day's summary counts the same run.
    assert window.summary_label.text() == "Завершено команд: 1"
    assert "выполнено команд за сеанс: 1" in window.summary_card.text.text()


def test_the_day_asks_for_the_one_thing_it_needs(window: MainWindow) -> None:
    # No Microsoft account: the card says what is missing and offers the way to fix it,
    # instead of stating that a calendar exists somewhere and is not here.
    assert "не подключён" in window.calendar_hint.text()
    assert window.calendar_button.isVisible()
    # And it shows no appointments at all rather than plausible ones.
    assert window.today.count == 0 and window.today.events.count() == 0


def test_the_day_shows_the_appointments_it_was_given(window: MainWindow) -> None:
    window.today.show_account("owner@example.test")
    assert "Читаю календарь" in window.calendar_hint.text()
    window.today.show_events(
        (
            {"subject": "Встреча с клиентом", "start": "2026-09-18T14:00:00Z", "location": "Офис"},
            {"subject": "", "start": "не время", "location": ""},
        ),
        read=True,
    )
    assert window.today.count == 2 and window.today.events.count() == 2
    rendered = [window.today.events.itemAt(index) for index in range(2)]
    shown = [item.widget() for item in rendered if item is not None]
    assert len(shown) == 2 and shown[0] is not None and shown[1] is not None
    assert "Встреча с клиентом" in shown[0].accessibleName()
    # An hour that cannot be read is shown as one that cannot be read, not as a guess.
    assert shown[1].accessibleName().startswith("--:--")
    window._show_summary()
    assert "Ближайшая встреча" in window.summary_card.text.text()
    # A calendar that did not answer says so instead of showing a day with nothing in it.
    window.today.show_events((), read=False)
    assert "не прочитан" in window.calendar_hint.text()


def test_an_unconnected_service_never_looks_connected(qtbot: QtBot, window: MainWindow) -> None:
    # What is connected is a fact about the machine running this, so the grid is asked
    # about a known answer rather than about whatever this computer happens to hold.
    qtbot.waitUntil(lambda: window.probe is None, timeout=PATIENCE)
    panel = window.connected_apps
    assert list(panel.tiles) == [key for key, _, _, _ in APPS]
    panel.apply({"notion": True, "voice": True})
    assert panel.counter.text() == f"2 из {len(APPS)}"
    assert panel.missing == len(APPS) - 2
    assert panel.tiles["notion"].property("connected") == "true"
    assert "не подключено" in panel.tiles["asana"].accessibleName()
    assert panel.tiles["asana"].property("connected") == "false"


def test_every_navigation_entry_opens_something(qtbot: QtBot, window: MainWindow) -> None:
    assert list(window.nav_buttons) == ["home", "journal", "plans", "connections", "settings"]
    window._navigate("settings")
    assert window.mode_box.isVisible()
    window._navigate("connections")
    assert window.setup_window is not None
    window.setup_window.close()
    window._navigate("plans")
    planner = window.planner_window
    assert planner is not None and planner.isVisible()
    assert planner.tabs.tabText(planner.tabs.currentIndex()) == "Рутины"
    planner.hide()


def test_the_quick_actions_are_commands_this_assistant_can_be_given(window: MainWindow) -> None:
    # Every tile is a sentence, not a name of a service the owner then has to translate.
    assert len(ACTIONS) == 6
    assert all(len(command.split()) >= 2 for _, _, _, command, _ in ACTIONS)
    assert set(window.quick_actions.tiles) == {title for _, title, _, _, _ in ACTIONS}


def test_the_summary_does_not_count_connections_before_the_probe_answers(
    qtbot: QtBot,
) -> None:
    """Two lines of the same screen must not disagree about the same fact.

    Every tile starts unconnected, because that is how a tile waits. Counting them while
    the counter beside them still says «проверяю» turns that waiting into a number, and on
    a machine with connections it is the wrong number until the probe comes back.
    """

    def unconnected(panel: ConnectedApps) -> int | None:
        """Read it through a call, so one assertion about it does not narrow the next."""
        return panel.missing

    panel = ConnectedApps()
    card = SummaryCard()
    qtbot.addWidget(panel)
    qtbot.addWidget(card)
    assert panel.counter.text() == "проверяю" and unconnected(panel) is None
    card.summarise(0, "", 0, panel.missing)
    assert "подключения проверяются" in card.text.text()
    assert "не подключено сервисов" not in card.text.text()

    panel.apply({key: True for key, _, _, _ in APPS[:3]})
    card.summarise(0, "", 0, panel.missing)
    assert unconnected(panel) == len(APPS) - 3
    assert f"не подключено сервисов: {len(APPS) - 3}" in card.text.text()
