"""The surface where background work is switched on, and where its suggestions wait.

Nothing here notifies. A routine that found no reason changes nothing on screen, because an
assistant that interrupts every five minutes is switched off within a day, and then it
notices nothing at all. What it found waits in this panel until the owner looks.

Acting on a suggestion is an ordinary action of the owner's: it is prepared through the same
engine, confirmed in the same dialog - by button or by repeating the control detail - and
audited under the same rules. The background never confirms anything itself.
"""

import asyncio
import json

from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.routines.runner import Cycle, RoutineRunner
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalAuthority, ApprovalToken
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.ui.approval_dialog import ApprovalDialog
from jarvis.ui.tool_worker import ToolWorker
from jarvis.ui.voice_panel import VoicePanel

TICK_MS = 30000


def plain(text: str) -> QLabel:
    """Everything a service said is shown as text, never as markup."""
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class RoutineWorker(QThread):
    """One tick of every due routine, off the event loop."""

    def __init__(self, runner: RoutineRunner) -> None:
        super().__init__()
        self.runner = runner
        self.cycles: tuple[Cycle, ...] = ()

    def run(self) -> None:
        try:
            self.cycles = asyncio.run(self.runner.tick())
        except Exception:
            self.cycles = ()


class RoutinePanel(QWidget):
    message = Signal(str)
    busy_changed = Signal(bool)

    def __init__(
        self,
        runner: RoutineRunner,
        engine: PermissionEngine,
        authority: ApprovalAuthority,
        audit: AuditLog,
        *,
        voice: VoicePanel | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.runner = runner
        self.engine = engine
        self.authority = authority
        self.audit = audit
        self.voice = voice
        self.worker: RoutineWorker | None = None
        self.tool_worker: ToolWorker | None = None
        self.dialog: ApprovalDialog | None = None
        self.action: Action | None = None
        self.acting_key = ""
        self.closed = False
        self.enabled_boxes: dict[str, QCheckBox] = {}
        self.acting_boxes: dict[str, QCheckBox] = {}
        body = QVBoxLayout(self)
        body.addWidget(
            plain(
                "Ассистент сам смотрит, что происходит, и оставляет здесь повод, если он "
                "нашёлся. Наблюдение только читает. Обратимую работу рутина выполняет лишь "
                "с отдельного разрешения; всё, что требует подтверждения, ждёт вас здесь."
            )
        )
        self.active = QCheckBox("Рутины работают")
        self.active.setChecked(self.runner.state.active)
        self.active.toggled.connect(self._set_active)
        body.addWidget(self.active)
        for routine in self.runner.routines:
            body.addWidget(self._routine_row(routine.name, routine.title, routine.description))
        self.check_now = QPushButton("Проверить сейчас")
        self.check_now.setAutoDefault(False)
        self.check_now.clicked.connect(self._check_now)
        body.addWidget(self.check_now)
        body.addWidget(plain("Предложения"))
        self.suggestions = QVBoxLayout()
        body.addLayout(self.suggestions)
        self.status = plain("")
        self.status.setAccessibleName("Состояние рутин")
        body.addWidget(self.status)
        self.message.connect(self.status.setText)
        body.addStretch(1)
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)
        if self.runner.state.active:
            self.timer.start()
        self.refresh()

    def _routine_row(self, name: str, title: str, description: str) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        enabled = QCheckBox(title)
        enabled.setChecked(self.runner.state.settings(name).enabled)
        enabled.toggled.connect(lambda value, key=name: self._enable(key, value))
        row.addWidget(enabled)
        acting = QCheckBox("может выполнять обратимую работу сам")
        acting.setChecked(self.runner.state.settings(name).acts)
        acting.toggled.connect(lambda value, key=name: self.runner.state.allow_acting(key, value))
        row.addWidget(acting)
        layout.addLayout(row)
        layout.addWidget(plain(description))
        self.enabled_boxes[name] = enabled
        self.acting_boxes[name] = acting
        return box

    @Slot(bool)
    def _set_active(self, value: bool) -> None:
        """The kill switch: it stops the schedule and the cycle that is running right now."""
        self.runner.state.set_active(value)
        if value:
            self.runner.resume()
            self.timer.start()
            self.message.emit("Рутины включены. Наблюдение идёт в фоне и молчит без повода.")
        else:
            self.timer.stop()
            self.runner.cancel()
            self.message.emit("Рутины выключены. Ничего не запускается и не наблюдается.")

    def _enable(self, name: str, value: bool) -> None:
        self.runner.state.enable(name, value)
        if value:
            self.runner.state.run_soon(name)

    @Slot()
    def _check_now(self) -> None:
        for name in self.enabled_boxes:
            self.runner.state.run_soon(name)
        self._tick()

    @Slot()
    def _tick(self) -> None:
        if self.closed or self.worker is not None or not self.runner.state.active:
            return
        if self.runner.state.exhausted:
            self.message.emit("Дневной предел запусков исчерпан. Рутины продолжат завтра.")
            return
        worker = RoutineWorker(self.runner)
        self.worker = worker
        self.busy_changed.emit(True)
        worker.finished.connect(self._ticked)
        worker.start()

    @Slot()
    def _ticked(self) -> None:
        worker = self.worker
        if worker is None:
            return
        worker.wait()
        self.worker = None
        self.busy_changed.emit(False)
        worker.deleteLater()
        if self.closed:
            return
        self.refresh()

    def refresh(self, note: str = "") -> None:
        """Redraw what is waiting. A panel with nothing in it is the ordinary case."""
        while self.suggestions.count():
            item = self.suggestions.takeAt(0)
            widget = None if item is None else item.widget()
            if widget is not None:
                widget.deleteLater()
        pending = self.runner.queue.pending()
        for suggestion in pending:
            self.suggestions.addWidget(
                self._card(suggestion.key, suggestion.title, suggestion.detail, suggestion.tool)
            )
        waiting = f"Предложений: {len(pending)}." if pending else "Предложений нет."
        counted = f"Запусков сегодня: {self.runner.state.spent} из {self.runner.state.budget}."
        storage = " Настройки не сохраняются на диск." if self.runner.state.unsaved else ""
        self.message.emit(" ".join(part for part in (note, waiting, counted) if part) + storage)

    def _card(self, key: str, title: str, detail: str, tool: str) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.addWidget(plain(title))
        if detail:
            layout.addWidget(plain(detail))
        row = QHBoxLayout()
        if tool:
            act = QPushButton("Выполнить")
            act.setAutoDefault(False)
            act.clicked.connect(lambda _=False, item=key: self._act(item))
            row.addWidget(act)
        hide = QPushButton("Скрыть")
        hide.setAutoDefault(False)
        hide.clicked.connect(lambda _=False, item=key: self._dismiss(item))
        row.addWidget(hide)
        row.addStretch(1)
        layout.addLayout(row)
        return card

    def _dismiss(self, key: str) -> None:
        self.runner.queue.dismiss(key)
        self.refresh()

    def _act(self, key: str) -> None:
        """The owner chose to do it. From here it is an ordinary action of theirs."""
        if self.tool_worker is not None or self.closed:
            return
        suggestion = self.runner.queue.get(key)
        if suggestion is None or not suggestion.actionable:
            self.refresh()
            return
        prepared = self.engine.prepare(
            suggestion.tool, json.loads(suggestion.arguments), Mode.EXECUTE
        )
        if isinstance(prepared, Outcome):
            self.message.emit("Действие отклонено: " + prepared.error.value)
            return
        self.action = prepared
        self.acting_key = key
        self.busy_changed.emit(True)
        if prepared.risk is Risk.CONFIRM:
            dialog = ApprovalDialog(prepared, self.authority, self, voice=self.voice)
            self.dialog = dialog

            def resolved(code: int) -> None:
                self.dialog = None
                if code == QDialog.DialogCode.Accepted and dialog.token is not None:
                    self._execute(prepared, dialog.token)
                else:
                    self.engine.cancel(prepared)
                    self.action = None
                    self.acting_key = ""
                    self.busy_changed.emit(False)
                    self.refresh("Предложение не подтверждено. Ничего не выполнено.")
                dialog.deleteLater()

            dialog.finished.connect(resolved)
            dialog.open()
        else:
            self._execute(prepared, None)

    def _execute(self, action: Action, token: ApprovalToken | None) -> None:
        worker = ToolWorker(self.engine, action, token)
        self.tool_worker = worker
        worker.finished.connect(self._executed)
        worker.start()

    @Slot()
    def _executed(self) -> None:
        worker = self.tool_worker
        if worker is None:
            return
        worker.wait()
        self.tool_worker = None
        self.action = None
        outcome = worker.outcome
        worker.deleteLater()
        self.busy_changed.emit(False)
        done = outcome.status is Status.SUCCESS
        if done:
            # It happened, so the reason is spent and stops waiting.
            self.runner.queue.dismiss(self.acting_key)
        self.acting_key = ""
        if not self.closed:
            self.refresh("Выполнено." if done else "Не выполнено: " + outcome.error.value)

    def stop(self) -> None:
        if self.dialog is not None:
            self.dialog.reject()
        if self.tool_worker is not None:
            self.tool_worker.cancel()
        if self.action is not None:
            self.engine.cancel(self.action)
        self.runner.cancel()

    def shutdown(self) -> None:
        self.closed = True
        self.timer.stop()
        self.stop()
        for worker in (self.worker, self.tool_worker):
            if worker is not None:
                worker.wait()
        self.worker = None
        self.tool_worker = None
