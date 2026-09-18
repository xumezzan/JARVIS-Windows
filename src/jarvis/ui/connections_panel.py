"""One place that answers "what is connected", and connects what is not.

Until now every service but Outlook was connected by opening a terminal and running a
module by hand. That is a fine way to hand a key to a program and a poor way to ask
somebody to start using one: the owner has to leave the application to set it up, and
nothing in the application ever said which services were waiting.

The key still never enters this process. Asking, saving and forgetting each go to the same
short-lived killable helper the rest of the code uses, and it answers with a word - "1",
"0", "ok" - rather than with a secret. The field here is write-only by construction:
nothing reads a stored key back, because nothing here has any use for one.
"""

import asyncio
from typing import Literal

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.planner.contracts import ProviderError
from jarvis.security.credentials import (
    SERVICES,
    forget_api_key,
    has_api_key,
    store_api_key,
    vendor_of,
)
from jarvis.ui import workers

Action = Literal["ask", "save", "forget"]

# Connected some other way, each for its own reason, each with its own screen. Naming them
# here is the point: a list that showed only what this panel can do would read as the whole
# list of what Jarvis can reach.
ELSEWHERE = (
    ("Outlook", "Вход Microsoft во вкладке «Outlook» — почта открывается там же."),
    (
        "Teams и OneDrive",
        "Тот же аккаунт Microsoft, но отдельные согласия: кнопки во вкладке «Outlook».",
    ),
    ("Серверы MCP", "Ключ на каждый сервер во вкладке «Серверы MCP», после проверки набора."),
    ("ElevenLabs", "Голос: кнопка микрофона → «Настроить голос ElevenLabs Free…»."),
)

WHAT = {
    "openai": "Сильная модель для многослойных задач.",
    "deepseek": "Быстрая модель для обычной работы.",
    "fireflies": "Записи и расшифровки встреч.",
    "notion": "Страницы: поиск, чтение, создание.",
    "asana": "Проекты и задачи.",
}


def plain(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class KeyWorker(QThread):
    """One errand to the credential store. It never carries a key back."""

    def __init__(self, action: Action, provider: str, secret: str = "") -> None:
        super().__init__()
        self.action = action
        self.provider = provider
        self.secret = secret
        self.connected = False
        self.error = ""

    def run(self) -> None:
        try:
            if self.action == "save":
                asyncio.run(store_api_key(self.provider, self.secret))
            elif self.action == "forget":
                asyncio.run(forget_api_key(self.provider))
            self.connected = asyncio.run(has_api_key(self.provider))
        except ProviderError:
            self.error = "credentials"
        except Exception:
            self.error = "credentials"
        finally:
            # The secret leaves this object the moment it is no longer needed, so a worker
            # sitting in the finished list is not a worker holding a key.
            self.secret = ""


class ConnectionsPanel(QWidget):
    """The services Jarvis can reach, what is connected, and one button to change that."""

    busy_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("connectionsPanel")
        self.worker: KeyWorker | None = None
        self.queue: list[tuple[Action, str, str]] = []
        self.closed = False
        self.asked = False
        self.fields: dict[str, QLineEdit] = {}
        self.states: dict[str, QLabel] = {}
        body = QVBoxLayout(self)
        body.addWidget(
            plain(
                "Ключ вводится здесь и уходит прямо в хранилище Windows — приложение его "
                "не видит и прочитать обратно не может. Показывается только, подключён "
                "сервис или нет."
            )
        )
        grid = QGridLayout()
        grid.setColumnStretch(3, 1)
        for row, provider in enumerate(sorted(SERVICES)):
            grid.addWidget(plain(vendor_of(provider)), row, 0)
            grid.addWidget(plain(WHAT.get(provider, "")), row, 1)
            state = plain("не проверено")
            state.setObjectName(f"state-{provider}")
            self.states[provider] = state
            grid.addWidget(state, row, 2)
            field = QLineEdit()
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText("ключ")
            field.setAccessibleName(f"Ключ {vendor_of(provider)}")
            self.fields[provider] = field
            grid.addWidget(field, row, 3)
            save = QPushButton("Подключить")
            save.clicked.connect(lambda _, name=provider: self.save(name))
            grid.addWidget(save, row, 4)
            forget = QPushButton("Забыть")
            forget.clicked.connect(lambda _, name=provider: self.forget(name))
            grid.addWidget(forget, row, 5)
        body.addLayout(grid)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        body.addWidget(divider)
        body.addWidget(plain("Подключаются иначе:"))
        for name, where in ELSEWHERE:
            body.addWidget(plain(f"    {name} — {where}"))

        self.status = plain("")
        body.addWidget(self.status)
        self.recheck = QPushButton("Проверить заново")
        self.recheck.clicked.connect(self.refresh)
        body.addWidget(self.recheck)
        body.addStretch(1)

    @property
    def busy(self) -> bool:
        return self.worker is not None

    def showEvent(self, event: QShowEvent) -> None:
        """The first look at this tab is when the answer is wanted — and not before.

        A window builds this panel behind a tab. Asking the credential store about every
        service at construction spends one short-lived process per service on a screen
        nobody has opened yet, every time a window appears; on a slow machine that is
        seconds of work, and it competes with whatever the owner actually asked for.
        """
        super().showEvent(event)
        if not self.asked and not self.closed:
            self.asked = True
            self.refresh()

    def refresh(self) -> None:
        """Ask about every service, one helper at a time rather than one per service."""
        self.asked = True
        for state in self.states.values():
            state.setText("проверяю…")
        self.queue.extend(("ask", provider, "") for provider in sorted(SERVICES))
        self._pump()

    def save(self, provider: str) -> None:
        key = self.fields[provider].text().strip()
        if not key:
            self.status.setText("Введите ключ, потом «Подключить».")
            return
        self.queue.append(("save", provider, key))
        self._pump()

    def forget(self, provider: str) -> None:
        self.queue.append(("forget", provider, ""))
        self._pump()

    def _pump(self) -> None:
        if self.worker is not None or self.closed or not self.queue:
            return
        action, provider, secret = self.queue.pop(0)
        self.worker = KeyWorker(action, provider, secret)
        self.worker.finished.connect(self._done)
        self.busy_changed.emit(True)
        if action != "ask":
            self.status.setText("Обращаюсь к хранилищу…")
        workers.start(self.worker)

    def _done(self) -> None:
        worker = self.worker
        self.worker = None
        self.busy_changed.emit(False)
        if worker is None or self.closed:
            return
        # The field is cleared whatever happened: a key left on screen after a failed save
        # is a key waiting to be read over somebody's shoulder.
        self.fields[worker.provider].clear()
        self.states[worker.provider].setText("подключён" if worker.connected else "не подключён")
        if worker.error:
            self.status.setText("Хранилище недоступно или ключ не подошёл. Попробуйте ещё раз.")
        elif worker.action == "save":
            self.status.setText(f"{vendor_of(worker.provider)}: ключ сохранён.")
        elif worker.action == "forget":
            self.status.setText(f"{vendor_of(worker.provider)}: ключ удалён из хранилища.")
        self._pump()

    def shutdown(self) -> None:
        self.closed = True
        self.queue.clear()
        worker = self.worker
        if worker is not None:
            worker.wait(5000)
