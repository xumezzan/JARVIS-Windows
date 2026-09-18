"""The steps of a task as they happen: сделано, идёт, ждёт.

A progress bar answers "is anything happening", which stops being the interesting question
once a task can run for an hour. The interesting question is what it is doing and where it
stopped, and a task that waits should say which of the two waits it is in: an approval is
the owner's authority over one exact action, an input is data the task is missing.

The rows are built from the runner's own `notify()` events and from the outcomes it
reports, so nothing here is a guess about progress: a step is finished when its outcome
arrived, with the status that arrived, and a failed step stays on screen saying so.

Steps restored from an earlier process are listed apart and marked as such, because what
they did is known while what they saw is not (`core/planner/resume.py`).
"""

from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from jarvis.core.planner.contracts import Step
from jarvis.permissions.policies import Status

Wait = Literal["approval", "clarification"]

MAX_HEIGHT = 170
WAITING: dict[Wait, str] = {
    "approval": "ждёт подтверждения",
    "clarification": "ждёт ответа",
}
DONE = {
    Status.SUCCESS: "сделано",
    Status.SIMULATED: "симуляция",
    Status.CANCELLED: "отменено",
    Status.TIMEOUT: "истекло время",
    Status.DENIED: "отклонено политикой",
    Status.INVALID: "неверный запрос",
    Status.ERROR: "ошибка",
}


class Checklist(QWidget):
    """One row per step, in the order the task did them."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.rows = QListWidget()
        self.rows.setObjectName("checklist")
        self.rows.setAccessibleName("Шаги задачи")
        self.rows.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.rows.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.rows.setMaximumHeight(MAX_HEIGHT)
        layout.addWidget(self.rows)
        self.current: QListWidgetItem | None = None
        self.waiting: Wait | None = None
        self._fit()

    def _fit(self) -> None:
        """Take the height the steps need and no more; an empty list is not a box."""
        count = self.rows.count()
        self.rows.setVisible(bool(count))
        if not count:
            return
        row = self.rows.sizeHintForRow(0)
        self.rows.setFixedHeight(min(row * count + 2 * self.rows.frameWidth() + 4, MAX_HEIGHT))

    def reset(self) -> None:
        self.rows.clear()
        self.current = None
        self.waiting = None
        self._fit()

    def _row(self, text: str) -> QListWidgetItem:
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.rows.addItem(item)
        self._fit()
        self.rows.scrollToItem(item)
        return item

    def restore(self, steps: tuple[Step, ...]) -> None:
        """Show what an earlier process already did, before this one adds anything."""
        for index, step in enumerate(steps, 1):
            state = (
                "возможно выполнено до перезапуска"
                if step.outcome.may_have_effects and step.outcome.status is Status.TIMEOUT
                else DONE.get(step.outcome.status, step.outcome.status.value) + " до перезапуска"
            )
            self._row(f"{index}. {step.tool} — {state}")

    def record(self, kind: str, value: object) -> None:
        """Consume one runner event. An unknown kind is ignored rather than guessed at."""
        if kind == "thinking":
            self.waiting = None
            self.current = self._row(f"{value}. планирование шага")
        elif kind == "executing" and self.current is not None:
            self.waiting = None
            self.current.setText(f"{self.rows.row(self.current) + 1}. {value} — идёт")
        elif kind == "tool" and isinstance(value, Step) and self.current is not None:
            self.waiting = None
            state = DONE.get(value.outcome.status, value.outcome.status.value)
            if value.outcome.error.value != "none":
                state += f" ({value.outcome.error.value})"
            self.current.setText(f"{self.rows.row(self.current) + 1}. {value.tool} — {state}")
            self.current = None

    def settle(self) -> None:
        """The task stopped. A step that was only being planned never happened, so it goes."""
        if self.current is not None:
            self.rows.takeItem(self.rows.row(self.current))
            self.current = None
            self._fit()
        self.waiting = None

    def wait(self, kind: Wait | None) -> None:
        """Say which wait the task is in, on the row it is waiting at."""
        self.waiting = kind
        if self.current is None:
            return
        step = self.current.text().split(" — ")[0]
        self.current.setText(step if kind is None else f"{step} — {WAITING[kind]}")
