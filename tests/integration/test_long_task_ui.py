"""The long task as the owner sees it: the checklist, the two waits, and resumption.

The window builds its own composition root, so the journal these tests write to is the one
the window will read — nothing is injected past the surface under test.
"""

import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot
from tests.unit.test_planner import MESSAGE, Scripted

from jarvis.config import AppConfig
from jarvis.core.planner.contracts import Limits
from jarvis.core.planner.offline import call
from jarvis.core.workflow.models import StepRecord, step_key
from jarvis.observability.audit import AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.ui.approval_dialog import ApprovalDialog
from jarvis.ui.planner_window import PlannerWindow


def rows(window: PlannerWindow) -> list[str]:
    listed = window.checklist.rows
    return [listed.item(index).text() for index in range(listed.count())]


def waiting(window: PlannerWindow) -> str | None:
    """Read it through a call, so one assertion about it does not narrow the next."""
    return window.checklist.waiting


def crash(window: PlannerWindow, request: str = "длинная задача") -> str:
    """Leave in the window's own journal what a killed process leaves: a done step, run open."""
    action = window.engine.prepare("local.append_message", MESSAGE, Mode.EXECUTE)
    assert isinstance(action, Action)
    window.engine.cancel(action)
    record = window.bench.workflows.start(request, Mode.EXECUTE)
    window.bench.workflows.append(
        record.id,
        StepRecord(
            index=0,
            tool=action.tool,
            key=step_key(action.tool, action.payload),
            state="finished",
            status=Status.SUCCESS.value,
            error=ErrorCode.NONE.value,
            may_have_effects=True,
            updated=1,
        ),
    )
    return record.id


def test_the_checklist_shows_one_row_per_step_with_its_outcome(
    qtbot: QtBot, tmp_path: Path
) -> None:
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    window.show()
    try:
        window.command.setPlainText("проверь систему дважды")
        with qtbot.waitSignal(window.task_finished):
            QTest.mouseClick(window.run_button, Qt.MouseButton.LeftButton)
        listed = rows(window)
        assert len(listed) == 2
        # Each row names its own tool and the status that actually came back.
        assert all("local.check — симуляция" in row for row in listed)
        assert listed[0].startswith("1.") and listed[1].startswith("2.")
    finally:
        window.shutdown()


def test_a_waiting_task_says_which_wait_it_is_in(qtbot: QtBot, tmp_path: Path) -> None:
    window = PlannerWindow(
        AppConfig(tmp_path), provider=Scripted([call("local.append_message", MESSAGE)])
    )
    qtbot.addWidget(window)
    window.show()
    try:
        window.simulation.setChecked(False)
        window.command.setPlainText("test")
        window.start()
        qtbot.waitUntil(lambda: window.approval_dialog is not None)
        assert waiting(window) == "approval"
        assert rows(window)[-1].endswith("ждёт подтверждения")
        dialog = window.approval_dialog
        assert dialog is not None
        with qtbot.waitSignal(window.task_finished):
            QTest.mouseClick(dialog.approve_button, Qt.MouseButton.LeftButton)
        # The wait is over, and the row now carries the outcome instead of the wait.
        assert waiting(window) is None
        assert rows(window)[-1].endswith("local.append_message — сделано")
    finally:
        window.shutdown()


def test_an_interrupted_task_is_offered_and_resumed_without_repeating_its_effect(
    qtbot: QtBot, tmp_path: Path
) -> None:
    window = PlannerWindow(
        AppConfig(tmp_path), provider=Scripted([call("local.append_message", MESSAGE)])
    )
    qtbot.addWidget(window)
    window.show()
    try:
        assert not window.resume_button.isEnabled()
        run_id = crash(window)
        window._list_unfinished()
        assert window.resume_button.isEnabled()
        assert "длинная задача" in window.unfinished.itemText(0)
        assert "шагов в журнале: 1/1" in window.unfinished.itemText(0)

        with qtbot.waitSignal(window.task_finished) as result:
            QTest.mouseClick(window.resume_button, Qt.MouseButton.LeftButton)
        # The step from the earlier process is shown, and proposing it again is refused
        # rather than sending a second message.
        assert rows(window)[0] == "1. local.append_message — сделано до перезапуска"
        assert result.args == ["error"]
        assert window.outbox.count == 0
        assert window.last_result is not None and window.last_result.error == "duplicate_effect"
        # The run was closed by that failure, so it is no longer offered.
        record = window.bench.workflows.get(run_id)
        assert record is not None and record.phase == "failed"
        assert not window.resume_button.isEnabled()
    finally:
        window.shutdown()


def test_resuming_keeps_the_mode_the_run_started_in(qtbot: QtBot, tmp_path: Path) -> None:
    window = PlannerWindow(AppConfig(tmp_path), provider=Scripted([call("local.check", {})]))
    qtbot.addWidget(window)
    window.show()
    try:
        crash(window, "выполняемая задача")
        window._list_unfinished()
        assert window.simulation.isChecked()
        with qtbot.waitSignal(window.task_finished):
            QTest.mouseClick(window.resume_button, Qt.MouseButton.LeftButton)
        # The run was started in EXECUTE, so the checkbox followed the record, not the other
        # way round: a task that was executing does not quietly continue as a simulation.
        assert not window.simulation.isChecked()
        assert window.command.toPlainText() == "выполняемая задача"
    finally:
        window.shutdown()


def test_the_window_runs_long_tasks_and_says_what_it_spends(qtbot: QtBot, tmp_path: Path) -> None:
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    window.show()
    try:
        assert window.limits == Limits.long()
        window.command.setPlainText("проверь систему")
        with qtbot.waitSignal(window.task_finished):
            QTest.mouseClick(window.run_button, Qt.MouseButton.LeftButton)
        # A durable run was opened for it, and the journal knows how it ended.
        finished = window.bench.workflows.recent(1)
        assert len(finished) == 1 and finished[0].phase == "done"
        assert waiting(window) is None
    finally:
        window.shutdown()


def test_the_approval_window_says_how_long_is_left_and_answers_when_it_closes(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """A task may wait an hour; this snapshot may not. The owner is told which is which."""
    now = [1000.0]
    store = ApprovalStore(clock=lambda: now[0])
    audit = AuditLog(tmp_path / "audit.sqlite3")
    try:
        authority = store.take_authority(audit.approved)
        action = Action(
            uuid4(),
            "files.recycle",
            json.dumps({"path": "D:/Документы/смета.txt"}, ensure_ascii=False),
            Risk.CONFIRM,
            Mode.EXECUTE,
        )
        store.request(action)
        dialog = ApprovalDialog(action, authority)
        qtbot.addWidget(dialog)
        dialog.show()
        assert "ещё 60 с" in dialog.error_label.text()
        assert dialog.countdown.isActive()

        now[0] += 61
        with qtbot.waitSignal(dialog.rejected):
            dialog._tick()
        # Nothing was approved, the dead dialog does not hold the task, and the owner reads
        # why rather than discovering it by pressing a button that refuses them.
        assert dialog.token is None
        assert not dialog.approve_button.isEnabled()
        assert "истекло" in dialog.error_label.text()
        assert not dialog.countdown.isActive()
    finally:
        audit.close()
