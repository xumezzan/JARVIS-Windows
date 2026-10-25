"""Real Qt controls for explicit saving, selection, cloud disclosure and session cleanup."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot
from tests.unit.test_planner import Scripted

from jarvis.config import AppConfig
from jarvis.memory.derived import DerivedStore
from jarvis.memory.models import Hint
from jarvis.ui.memory_panel import MemoryPanel
from jarvis.ui.planner_window import PlannerWindow


def ready(panel: MemoryPanel, qtbot: QtBot) -> None:
    qtbot.waitUntil(lambda: panel.available and panel.worker is None)


def save(panel: MemoryPanel, qtbot: QtBot, value: str = "Альфа") -> None:
    panel.kind.setCurrentIndex(panel.kind.findData("project"))
    panel.label.setText("Проект")
    panel.value.setText(value)
    QTest.mouseClick(panel.save_button, Qt.MouseButton.LeftButton)
    ready(panel, qtbot)


def test_ui_persistence_edit_delete_and_clear(qtbot: QtBot, tmp_path: Path) -> None:
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    window.show()
    panel = window.memory
    window.tabs.setCurrentIndex(1)
    ready(panel, qtbot)
    try:
        save(panel, qtbot)
        assert panel.entries[0].value == "Альфа"
        panel.profile_list.setCurrentRow(0)
        save(panel, qtbot, "Бета")
        assert panel.profile_list.count() == 1 and panel.entries[0].value == "Бета"
        panel.profile_list.setCurrentRow(0)
        QTest.mouseClick(panel.delete_button, Qt.MouseButton.LeftButton)
        ready(panel, qtbot)
        assert panel.profile_list.count() == 0
        save(panel, qtbot)
    finally:
        window.shutdown()
    reopened = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(reopened)
    reopened.show()
    reopened.tabs.setCurrentIndex(1)
    try:
        ready(reopened.memory, qtbot)
        assert reopened.memory.entries[0].value == "Альфа"
        QTest.mouseClick(reopened.memory.clear_button, Qt.MouseButton.LeftButton)
        ready(reopened.memory, qtbot)
        assert reopened.memory.store.read() == ()
    finally:
        reopened.shutdown()


@pytest.mark.parametrize("cloud", [False, True])
def test_selected_context_cloud_gate_one_task_only(
    qtbot: QtBot, tmp_path: Path, cloud: bool
) -> None:
    provider = Scripted([])
    window = PlannerWindow(AppConfig(tmp_path), provider=provider)
    qtbot.addWidget(window)
    window.show()
    panel = window.memory
    ready(panel, qtbot)
    try:
        window.tabs.setCurrentIndex(1)
        save(panel, qtbot)
        panel.profile_list.item(0).setCheckState(Qt.CheckState.Checked)
        assert "Альфа" in panel.preview.toPlainText()
        window.command.setPlainText("проверь систему")
        if cloud:
            window.provider_choice.setCurrentIndex(1)
            window.cloud_consent.setChecked(True)
            window.start()
            assert window.worker is None and not provider.inputs
            panel.cloud_consent.setChecked(True)
        window.tabs.setCurrentIndex(0)
        with qtbot.waitSignal(window.task_finished):
            window.start()
        assert provider.inputs[0].memory.profile[0].value == "Альфа"
        assert not panel.cloud_consent.isChecked() and panel.snapshot().empty
        assert window.simulation.isChecked()
        with qtbot.waitSignal(window.task_finished):
            window.start()
        assert provider.inputs[-1].memory.empty
        assert not panel.session.read()
    finally:
        window.shutdown()


def test_no_automatic_ingestion_and_session_shutdown(qtbot: QtBot, tmp_path: Path) -> None:
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    window.show()
    panel = window.memory
    ready(panel, qtbot)
    try:
        window.command.setPlainText("проверь систему дважды")
        with qtbot.waitSignal(window.task_finished):
            window.start()
        window.voice.transcript_ready.emit("Личный текст для просмотра")
        assert panel.store.read() == () and panel.session.read() == ()
        panel.session.add(Hint(kind="application", label="Редактор", value="notepad"))
        panel._render_session()
        panel.session_list.item(0).setCheckState(Qt.CheckState.Checked)
        assert panel.snapshot().session
        panel.cloud_consent.setChecked(True)
        panel.reset()
        assert panel.snapshot().empty and not panel.cloud_consent.isChecked()
        panel.session.add(Hint(kind="project", label="Проект", value="Бета"))
    finally:
        window.shutdown()
    assert not panel.session.read() and not panel.label.text()
    assert panel.store.read() == ()


def test_secret_rejected_and_consent_invalidated_by_edit(qtbot: QtBot, tmp_path: Path) -> None:
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    window.show()
    panel = window.memory
    window.tabs.setCurrentIndex(1)
    ready(panel, qtbot)
    try:
        save(panel, qtbot)
        panel.profile_list.item(0).setCheckState(Qt.CheckState.Checked)
        panel.cloud_consent.setChecked(True)
        panel.profile_list.setCurrentRow(0)
        save(panel, qtbot, "Бета")
        assert not panel.cloud_consent.isChecked() and panel.snapshot().empty
        panel.value.setText("password fixture")
        QTest.mouseClick(panel.save_button, Qt.MouseButton.LeftButton)
        assert panel.entries[0].value == "Бета"
        assert b"password fixture" not in panel.store.path.read_bytes()
    finally:
        window.shutdown()


def learned_ready(panel: MemoryPanel, qtbot: QtBot) -> None:
    qtbot.waitUntil(lambda: panel.derived_worker is None)


def test_learned_memory_is_shown_switched_on_and_removed(qtbot: QtBot, tmp_path: Path) -> None:
    learnt = DerivedStore(tmp_path / "derived.sqlite3")
    learnt.record("tool", "почту", "outlook.list")
    learnt.record("entity", "джон", "a" * 32)
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    window.show()
    panel = window.memory
    window.tabs.setCurrentIndex(1)
    try:
        qtbot.waitUntil(lambda: panel.derived_worker is None and bool(panel.learned))
        rows = [panel.learned_list.item(index).text() for index in range(len(panel.learned))]
        assert any("почту" in row and "outlook.list" in row for row in rows)
        # A confirmed name is shown as a name; the identifier it carries stays in the store.
        assert any("джон" in row for row in rows) and not any("a" * 32 in row for row in rows)
        assert not panel.learning_box.isChecked() and not learnt.learning()
        panel.learning_box.setChecked(True)
        learned_ready(panel, qtbot)
        assert learnt.learning()
        panel.learned_list.setCurrentRow(0)
        QTest.mouseClick(panel.forget_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: panel.derived_worker is None and len(panel.learned) == 1)
        QTest.mouseClick(panel.clear_learned_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: panel.derived_worker is None and not panel.learned)
        assert learnt.read() == () and learnt.learning()
    finally:
        window.shutdown()


def test_learning_stays_off_until_the_owner_turns_it_on(qtbot: QtBot, tmp_path: Path) -> None:
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    window.show()
    panel = window.memory
    window.tabs.setCurrentIndex(1)
    try:
        learned_ready(panel, qtbot)
        assert not panel.learning_box.isChecked() and not panel.learned
        with qtbot.waitSignal(window.task_finished):
            window.command.setPlainText("проверь систему")
            window.simulation.setChecked(False)
            window.start()
        learned_ready(panel, qtbot)
        panel._reload_learned()
        learned_ready(panel, qtbot)
        # A finished run with learning off leaves nothing behind.
        assert panel.learned == () and DerivedStore(tmp_path / "derived.sqlite3").read() == ()
    finally:
        window.shutdown()
