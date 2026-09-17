"""The routine surface: switched off by default, quiet by habit, and never self-approving."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot
from tests.unit.test_routines import NOW, Fake, Tools, suggestion

from jarvis.core.routines.proposals import SuggestionQueue
from jarvis.core.routines.runner import RoutineRunner
from jarvis.core.routines.state import RoutineState
from jarvis.core.workflow.store import WorkflowStore
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import ApprovalStore
from jarvis.permissions.engine import PermissionEngine
from jarvis.ui.routine_panel import RoutinePanel

pytestmark = pytest.mark.integration


class Surface:
    def __init__(self, qtbot: QtBot, tmp_path: Path, routine: Fake) -> None:
        self.tools = Tools()
        registry = self.tools.registry()
        self.audit = AuditLog(tmp_path / "audit.sqlite3")
        approvals = ApprovalStore()
        self.engine = PermissionEngine(registry, approvals, self.audit)
        self.state = RoutineState(tmp_path / "routines.json", clock=lambda: NOW)
        self.queue = SuggestionQueue(clock=lambda: NOW)
        self.runner = RoutineRunner(
            registry,
            self.engine,
            WorkflowStore(tmp_path / "workflows.sqlite3"),
            self.state,
            self.queue,
            [routine],
            clock=lambda: NOW,
        )
        self.panel = RoutinePanel(
            self.runner, self.engine, approvals.take_authority(self.audit.approved), self.audit
        )
        qtbot.addWidget(self.panel)
        self.panel.show()

    def switch_on(self, acts: bool = False) -> None:
        self.panel.active.setChecked(True)
        self.panel.enabled_boxes[Fake.name].setChecked(True)
        self.panel.acting_boxes[Fake.name].setChecked(acts)

    def look(self, qtbot: QtBot) -> None:
        QTest.mouseClick(self.panel.check_now, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: self.panel.worker is None, timeout=15000)

    def button(self, text: str) -> QPushButton | None:
        found = [item for item in self.panel.findChildren(QPushButton) if item.text() == text]
        return found[0] if found else None

    def approvals(self) -> list[str]:
        records = [json.loads(record) for record in self.audit.recent(1000)]
        return [record["actor"] for record in records if record["event"] == "approved"]


@pytest.fixture
def surface(qtbot: QtBot, tmp_path: Path) -> Iterator[Surface]:
    made = Surface(qtbot, tmp_path, Fake(suggestions=(suggestion(),)))
    yield made
    made.panel.shutdown()
    made.audit.close()


def test_the_panel_starts_switched_off(qtbot: QtBot, surface: Surface) -> None:
    assert not surface.panel.active.isChecked() and not surface.panel.timer.isActive()
    assert not surface.panel.enabled_boxes[Fake.name].isChecked()
    assert not surface.panel.acting_boxes[Fake.name].isChecked()
    surface.look(qtbot)
    # Asking for a look while everything is off looks at nothing.
    assert not surface.tools.calls and "Предложений нет" in surface.panel.status.text()


def test_what_it_found_waits_here_and_nowhere_else(qtbot: QtBot, surface: Surface) -> None:
    surface.switch_on()
    surface.look(qtbot)
    assert surface.tools.calls == ["test.read"]
    assert "Предложений: 1" in surface.panel.status.text()
    assert surface.button("Скрыть") is not None and surface.button("Выполнить") is None
    hide = surface.button("Скрыть")
    assert hide is not None
    QTest.mouseClick(hide, Qt.MouseButton.LeftButton)
    assert not surface.queue.pending() and "Предложений нет" in surface.panel.status.text()


def test_a_quiet_look_changes_nothing_on_screen(qtbot: QtBot, tmp_path: Path) -> None:
    made = Surface(qtbot, tmp_path, Fake())
    try:
        made.switch_on()
        made.look(qtbot)
        assert made.tools.calls == ["test.read"] and not made.queue.pending()
        assert "Предложений нет" in made.panel.status.text()
        assert made.button("Скрыть") is None
    finally:
        made.panel.shutdown()
        made.audit.close()


def test_a_suggested_confirm_action_is_confirmed_in_the_usual_dialog(
    qtbot: QtBot, tmp_path: Path
) -> None:
    made = Surface(qtbot, tmp_path, Fake(suggestions=(suggestion("test.send"),)))
    try:
        made.switch_on(acts=True)
        made.look(qtbot)
        # The routine queued it rather than sending it, whatever the acting permission says.
        assert made.tools.calls == ["test.read"] and not made.approvals()
        run = made.button("Выполнить")
        assert run is not None
        QTest.mouseClick(run, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: made.panel.dialog is not None)
        dialog = made.panel.dialog
        assert dialog is not None and made.tools.calls == ["test.read"]
        QTest.mouseClick(dialog.approve_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: made.panel.tool_worker is None, timeout=15000)
        assert made.tools.calls == ["test.read", "test.send"]
        assert made.approvals() == ["user_ui"] and not made.queue.pending()
    finally:
        made.panel.shutdown()
        made.audit.close()


def test_refusing_the_dialog_leaves_the_suggestion_alone(qtbot: QtBot, tmp_path: Path) -> None:
    made = Surface(qtbot, tmp_path, Fake(suggestions=(suggestion("test.send"),)))
    try:
        made.switch_on()
        made.look(qtbot)
        run = made.button("Выполнить")
        assert run is not None
        QTest.mouseClick(run, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: made.panel.dialog is not None)
        dialog = made.panel.dialog
        assert dialog is not None
        dialog.reject()
        qtbot.waitUntil(lambda: made.panel.dialog is None)
        assert made.tools.calls == ["test.read"] and not made.approvals()
        assert len(made.queue.pending()) == 1
    finally:
        made.panel.shutdown()
        made.audit.close()


def test_reversible_work_the_owner_chose_needs_no_dialog(qtbot: QtBot, tmp_path: Path) -> None:
    made = Surface(qtbot, tmp_path, Fake(suggestions=(suggestion("test.write"),)))
    try:
        made.switch_on()
        made.look(qtbot)
        run = made.button("Выполнить")
        assert run is not None
        QTest.mouseClick(run, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: made.panel.tool_worker is None, timeout=15000)
        assert made.panel.dialog is None and not made.approvals()
        assert made.tools.calls == ["test.read", "test.write"]
        assert not made.queue.pending() and "Выполнено" in made.panel.status.text()
    finally:
        made.panel.shutdown()
        made.audit.close()


def test_the_kill_switch_stops_the_schedule(qtbot: QtBot, surface: Surface) -> None:
    surface.switch_on()
    assert surface.panel.timer.isActive() and not surface.runner.cancelled.is_set()
    surface.panel.active.setChecked(False)
    assert not surface.panel.timer.isActive() and surface.runner.cancelled.is_set()
    assert not surface.state.active
    surface.look(qtbot)
    assert not surface.tools.calls
    # Switching back on resumes the schedule without replaying anything.
    surface.panel.active.setChecked(True)
    assert surface.panel.timer.isActive() and not surface.runner.cancelled.is_set()
    surface.look(qtbot)
    assert surface.tools.calls == ["test.read"]
