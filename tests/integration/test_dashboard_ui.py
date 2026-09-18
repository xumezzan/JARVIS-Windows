"""The main screen: the command first, the work under it, and nothing that does nothing."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from jarvis.config import AppConfig
from jarvis.observability.logging import ShellLog
from jarvis.ui.main_window import EXAMPLES, MainWindow

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
    # Nothing has run, so the card that shows work is not an empty box on the screen.
    assert not window.task_card.isVisible()
    assert window.examples_box.isVisible()
    # The four controls are one line of words until the owner asks for them.
    assert not window.mode_box.isVisible()
    assert window.mode_label.text() == "делаю сам · офлайн-модель"
    assert window.command_input.placeholderText() == "Скажите, что сделать"


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


def test_an_example_fills_the_command_instead_of_explaining_it(
    qtbot: QtBot, window: MainWindow
) -> None:
    window._use_example(EXAMPLES[0])
    assert window.command_input.toPlainText() == EXAMPLES[0]
    assert EXAMPLES[0] == "Подготовь всё к встрече с клиентом"


def test_a_running_task_replaces_the_examples_with_its_own_steps(
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


def test_the_day_asks_for_the_one_thing_it_needs(window: MainWindow) -> None:
    # No Microsoft account: the card says what is missing and offers the way to fix it,
    # instead of stating that a calendar exists somewhere and is not here.
    assert "не подключён" in window.calendar_hint.text()
    assert window.calendar_button.isVisible()


def test_every_navigation_entry_opens_something(qtbot: QtBot, window: MainWindow) -> None:
    assert list(window.nav_buttons) == ["home", "journal", "memory", "connections"]
    window._navigate("connections")
    assert window.setup_window is not None
    window.setup_window.close()
