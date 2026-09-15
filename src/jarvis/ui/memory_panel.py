"""Explicit profile management and task-specific selection; I/O stays off the Qt loop."""

from collections.abc import Callable
from threading import Event
from time import time

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.memory.models import APPS, KINDS, PROFILE_TTL, Entry, Hint, MemoryContext
from jarvis.memory.session import SessionContext
from jarvis.memory.store import MemoryFailure, MemoryStore


def note(text: str) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    return widget


class MemoryWorker(QThread):
    def __init__(self, operation: Callable[[Event], tuple[Entry, ...]]):
        super().__init__()
        self.operation = operation
        self.cancelled = Event()
        self.entries: tuple[Entry, ...] = ()
        self._session_rows: tuple[Hint, ...] = ()
        self.error = ""

    def run(self) -> None:
        try:
            self.entries = self.operation(self.cancelled)
        except MemoryFailure as error:
            self.error = error.code
        except Exception:
            self.error = "storage"


class MemoryPanel(QWidget):
    changed = Signal()
    busy_changed = Signal(bool)

    def __init__(self, store: MemoryStore):
        super().__init__()
        self.setObjectName("memoryPanel")
        self.setMinimumSize(800, 820)
        self.store = store
        self.session = SessionContext()
        self.entries: tuple[Entry, ...] = ()
        self._session_rows: tuple[Hint, ...] = ()
        self.worker: MemoryWorker | None = None
        self.previous: Entry | None = None
        self.closed = False
        self.available = False
        layout = QVBoxLayout(self)
        layout.addWidget(
            note(
                "Память добавляете только вы. Короткие метки: профессия, проекты, имена и роли, "
                "обычные предпочтения. Не вводите пароли, личные документы и адреса получателей. "
                "Команды, речь и страницы автоматически не сохраняются."
            )
        )
        self.profile_list = QListWidget()
        self.profile_list.setAccessibleName("Профиль — отметьте записи для следующей задачи")
        layout.addWidget(note("Профиль · до 32 записей · 30 дней после сохранения"))
        layout.addWidget(self.profile_list)
        self.profile_list.currentRowChanged.connect(self._edit)
        self.profile_list.itemChanged.connect(self._selection)
        form = QFormLayout()
        self.form = form
        self.kind = QComboBox()
        for key, title in KINDS.items():
            self.kind.addItem(title, key)
        self.label = QLineEdit()
        self.label.setMaxLength(80)
        self.value = QLineEdit()
        self.value.setMaxLength(80)
        self.app = QComboBox()
        for key, title in APPS.items():
            self.app.addItem(title, key)
        self.app.hide()
        form.addRow("Категория", self.kind)
        form.addRow("Метка / имя", self.label)
        form.addRow("Значение / роль", self.value)
        form.addRow("Приложение", self.app)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        layout.addLayout(form)
        self._kind_changed()
        buttons = QHBoxLayout()
        self.new_button = QPushButton("Новая запись")
        self.save_button = QPushButton("Сохранить в профиль")
        self.save_button.setObjectName("primary")
        self.delete_button = QPushButton("Удалить запись")
        self.clear_button = QPushButton("Очистить профиль")
        self.reload_button = QPushButton("Перечитать")
        for button, callback in (
            (self.new_button, self._new),
            (self.save_button, self._save),
            (self.delete_button, self._delete),
            (self.clear_button, lambda: self._job(self.store.clear)),
            (self.reload_button, lambda: self._job(self.store.read)),
        ):
            buttons.addWidget(button)
            button.clicked.connect(callback)
        layout.addLayout(buttons)
        layout.addWidget(
            note(
                "Контекст разговора · до 6 меток · 30 минут, до сброса или закрытия окна. "
                "Добавьте метку через поля выше; она останется только в оперативной памяти."
            )
        )
        self.session_list = QListWidget()
        self.session_list.setAccessibleName("Контекст сеанса — выберите записи для задачи")
        self.session_list.itemChanged.connect(self._selection)
        layout.addWidget(self.session_list)
        context_buttons = QHBoxLayout()
        self.add_context = QPushButton("Добавить в контекст")
        self.remove_context = QPushButton("Удалить из контекста")
        self.reset_context = QPushButton("Сбросить контекст")
        for button, callback in (
            (self.add_context, self._add_context),
            (self.remove_context, self._remove_context),
            (self.reset_context, self.reset),
        ):
            context_buttons.addWidget(button)
            button.clicked.connect(callback)
        layout.addLayout(context_buttons)
        layout.addWidget(
            note(
                "Отметьте до 8 записей профиля и нужные метки сеанса. "
                "Выбор действует на один запуск. Имена и роли не определяют точного адресата."
            )
        )
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(100)
        self.preview.setAccessibleName("Точный контекст следующей задачи")
        layout.addWidget(self.preview)
        self.cloud_consent = QCheckBox(
            "Разрешаю передать показанные метки в OpenAI для одной задачи"
        )
        layout.addWidget(self.cloud_consent)
        self.status = note("Загрузка профиля…")
        layout.addWidget(self.status)
        self.cancel_button = QPushButton("Отменить операцию памяти")
        self.cancel_button.clicked.connect(self.cancel)
        layout.addWidget(self.cancel_button)
        self.timer = QTimer(self)
        self.timer.setInterval(60000)
        self.timer.timeout.connect(self._expire)
        self.timer.start()
        QTimer.singleShot(0, lambda: self._job(self.store.read) if not self.closed else None)

    def _kind_changed(self) -> None:
        is_app = self.kind.currentData() == "application"
        self.form.setRowVisible(self.value, not is_app)
        self.form.setRowVisible(self.app, is_app)

    def _hint(self) -> Hint:
        return Hint(
            kind=self.kind.currentData(),
            label=self.label.text(),
            value=self.app.currentData()
            if self.kind.currentData() == "application"
            else self.value.text(),
        )

    def _new(self) -> None:
        self.profile_list.setCurrentRow(-1)
        self.previous = None
        self.label.clear()
        self.value.clear()

    def _edit(self, index: int) -> None:
        if 0 <= index < len(self.entries):
            entry = self.entries[index]
            self.previous = entry
            self.kind.setCurrentIndex(self.kind.findData(entry.kind))
            self.label.setText(entry.label)
            self.value.setText(entry.value)
            if entry.kind == "application":
                self.app.setCurrentIndex(self.app.findData(entry.value))

    def _save(self) -> None:
        try:
            hint = self._hint()
            now = int(time())
            data = dict(hint.model_dump(), updated=now, expires=now + PROFILE_TTL)
            if self.previous is not None:
                data["id"] = self.previous.id
            entry = Entry.model_validate(data)
        except ValueError:
            self.status.setText(
                "Нужны короткие обычные метки до 80 символов, без секретов и адресов."
            )
            return
        previous = self.previous
        self._job(lambda cancel: self.store.save(entry, previous, cancel))

    def _delete(self) -> None:
        previous = self.previous
        if previous is not None:
            self._job(lambda cancel: self.store.delete(previous, cancel))

    def _job(self, operation: Callable[[Event], tuple[Entry, ...]]) -> None:
        if self.worker is not None or self.closed:
            return
        self.cloud_consent.setChecked(False)
        self.busy_changed.emit(True)
        self._controls(False)
        self.status.setText("Обновление памяти…")
        worker = MemoryWorker(operation)
        self.worker = worker
        worker.finished.connect(self._done)
        worker.start()

    def _controls(self, enabled: bool) -> None:
        for widget in (
            self.profile_list,
            self.kind,
            self.label,
            self.value,
            self.app,
            self.new_button,
            self.save_button,
            self.delete_button,
            self.clear_button,
            self.reload_button,
            self.session_list,
            self.add_context,
            self.remove_context,
            self.reset_context,
            self.cloud_consent,
        ):
            widget.setEnabled(enabled)
        self.cancel_button.setEnabled(not enabled)

    def _done(self) -> None:
        worker = self.worker
        if worker is None:
            return
        worker.wait()
        self.worker = None
        self.available = not bool(worker.error)
        self.entries = worker.entries if self.available else ()
        self._render_profile()
        messages = {
            "": "Профиль обновлён. Отметьте записи, которые нужны следующей задаче.",
            "storage": "Профиль недоступен или повреждён. Проверьте локальный файл и перечитайте.",
            "invalid": "Некорректная запись. Очистите профиль или исправьте файл вне приложения.",
            "limit": "Достигнут предел записей. Удалите ненужные записи.",
            "conflict": "Запись изменилась или истекла. Перечитайте профиль перед редактированием.",
            "cancelled": "Операция остановлена. Перечитайте профиль, чтобы проверить результат.",
        }
        self.status.setText(messages.get(worker.error, messages["storage"]))
        self._controls(True)
        self.busy_changed.emit(False)
        worker.operation = lambda cancel: ()
        worker.entries = ()
        worker.deleteLater()

    @staticmethod
    def _row(widget: QListWidget, hint: Hint) -> None:
        item = QListWidgetItem(f"{KINDS[hint.kind]} · {hint.label}: {hint.value}")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
        widget.addItem(item)

    def _render_profile(self) -> None:
        self.profile_list.clear()
        self.previous = None
        for entry in self.entries:
            self._row(
                self.profile_list, Hint(kind=entry.kind, label=entry.label, value=entry.value)
            )
        self._new()
        self._selection()

    def _render_session(self) -> None:
        self.session_list.clear()
        self._session_rows = self.session.read()
        for hint in self._session_rows:
            self._row(self.session_list, hint)
        self._selection()

    def _add_context(self) -> None:
        try:
            self.session.add(self._hint())
        except (ValueError, MemoryFailure):
            self.status.setText("Контекст: максимум 6 обычных коротких меток. Проверьте поля.")
            return
        self._render_session()

    def _remove_context(self) -> None:
        index = self.session_list.currentRow()
        if 0 <= index < len(self._session_rows):
            self.session.delete(self._session_rows[index])
        self._render_session()

    def reset(self) -> None:
        self.session.clear()
        self._new()
        self._render_session()

    def _expire(self) -> None:
        if not self.isEnabled() or self.worker is not None:
            return
        if any(entry.expires <= int(time()) for entry in self.entries):
            self._job(self.store.read)
        if len(self.session.read()) != self.session_list.count():
            self._render_session()

    def snapshot(self) -> MemoryContext:
        if self.worker is not None:
            raise MemoryFailure("storage")
        now = int(time())
        profile = tuple(
            Hint(kind=entry.kind, label=entry.label, value=entry.value)
            for index, entry in enumerate(self.entries)
            if entry.expires > now
            and self.profile_list.item(index).checkState() == Qt.CheckState.Checked
        )
        session = self.session.read()
        # Expiry changes list positions: invalidate selection instead of reassigning it.
        if len(session) != self.session_list.count():
            self._render_session()
        selected = tuple(
            hint
            for index, hint in enumerate(session)
            if self.session_list.item(index).checkState() == Qt.CheckState.Checked
        )
        return MemoryContext(profile=profile, session=selected)

    def _selection(self) -> None:
        self.cloud_consent.setChecked(False)
        try:
            context = self.snapshot()
            self.preview.setPlainText(
                "\n".join(
                    f"{KINDS[hint.kind]} · {hint.label}: {hint.value}"
                    for hint in (*context.profile, *context.session)
                )
                or "Записи для задачи не выбраны."
            )
        except (ValueError, MemoryFailure):
            self.preview.setPlainText("Выберите не более 8 записей профиля; дождитесь загрузки.")
        self.changed.emit()

    def consume_selection(self) -> None:
        self._render_profile()
        self._render_session()

    def cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancelled.set()

    def shutdown(self) -> None:
        self.closed = True
        self.timer.stop()
        self.cancel()
        if self.worker is not None:
            self.worker.wait()
            self._done()
        self.reset()
        self.entries = ()
        self._render_profile()
        self.label.clear()
        self.value.clear()
