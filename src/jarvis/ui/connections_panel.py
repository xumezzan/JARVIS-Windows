"""One screen that answers "what is connected", and connects what is not.

Until now every service but Outlook was connected by opening a terminal and running a
module by hand, and Outlook itself lived in a tab of another window. That is a fine way to
hand a key to a program and a poor way to ask somebody to start using one: on a new machine
the owner met an application that could do nothing and said nothing about why.

So this screen lists everything Jarvis can reach, in the order it matters: the model that
thinks, the Microsoft account that carries mail, calendar, Teams and OneDrive, the services
with their own tokens, and the voice. Every row says what it gives, whether it is connected,
and carries the action itself. Nothing here is a wizard: the owner connects what they need,
in any order, and what they skip stays unconnected and says so.

Passwords are not typed here. A key or a token goes straight to the Windows credential store
through the same short-lived killable helper the rest of the code uses, which answers with a
word - "1", "0", "ok" - rather than with a secret; the field is write-only by construction,
because nothing here has any use for a stored key. A Microsoft password is typed at
Microsoft, in their own browser window, and never reaches this process at all.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import UUID

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jarvis.connectors.microsoft.drive.connector import SURFACE as FILES_SURFACE
from jarvis.connectors.teams.connector import SURFACE as TEAMS_SURFACE
from jarvis.core.planner.contracts import ProviderError
from jarvis.mail.session import MailSession
from jarvis.observability.audit import AuditLog
from jarvis.security.credentials import (
    SERVICES,
    forget_api_key,
    has_api_key,
    store_api_key,
    vendor_of,
)
from jarvis.tools.base import ExecutionContext
from jarvis.ui import workers
from jarvis.ui.connection_task import AccountWorker, audited
from jarvis.ui.voice_panel import installed_model

Action = Literal["ask", "save", "forget"]

# Which heading a key belongs under. A service missing from this table is still shown, under
# "Сервисы": a connection that quietly disappeared from the screen is worse than one under
# the wrong heading.
GROUPS = ("Модель для задач", "Сервисы")
PLACE = {"deepseek": GROUPS[0], "openai": GROUPS[0]}

WHAT = {
    "openai": "Сильная модель для многослойных задач.",
    "deepseek": "Быстрая модель для обычной работы.",
    "fireflies": "Записи и расшифровки встреч.",
    "notion": "Страницы: поиск, чтение, создание.",
    "asana": "Проекты и задачи.",
}

# Connected some other way, each for its own reason. Naming them here is the point: a list
# that showed only what this screen can do would read as the whole list of what Jarvis has.
ELSEWHERE = (
    ("Серверы MCP", "Ключ на каждый сервер во вкладке «Серверы MCP», после проверки набора."),
    (
        "Голос ElevenLabs",
        "Необязательно и на один ответ: кнопка микрофона → «Настроить голос ElevenLabs Free…».",
    ),
)

SURFACE_BUTTONS = ((TEAMS_SURFACE, "Разрешить Teams"), (FILES_SURFACE, "Разрешить OneDrive"))
SURFACE_DONE = {
    TEAMS_SURFACE: "Teams разрешён: чаты читаются, отправка спрашивает.",
    FILES_SURFACE: "OneDrive разрешён: файлы читаются, запись спрашивает.",
}
# Said before the consent, not after the first refusal. Microsoft does not serve the Excel
# API on a personal OneDrive at all (docs/ONEDRIVE.md), so on such an account the search
# finds the workbook and every read of its cells is refused - which looks like a defect and
# is a platform boundary.
FILES_ACCOUNT = (
    "Содержимое книг Excel читается только на рабочем или учебном аккаунте. "
    "На личном OneDrive файлы найдутся, а их ячейки — нет."
)


def plain(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def heading(text: str) -> QLabel:
    label = plain(text)
    label.setObjectName("sectionHint")
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
    """The services Jarvis can reach, what is connected, and the buttons that change that."""

    busy_changed = Signal(bool)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        session: MailSession | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("connectionsPanel")
        self.worker: KeyWorker | None = None
        self.queue: list[tuple[Action, str, str]] = []
        self.closed = False
        self.asked = False
        self.fields: dict[str, QLineEdit] = {}
        self.states: dict[str, QLabel] = {}
        # The Microsoft section exists only where the account does: a screen without the
        # session would be offering a sign-in it cannot carry out.
        self.session = session if audit is not None else None
        self.audit = audit
        self.account_worker: AccountWorker | None = None
        self.surface_buttons: dict[str, QPushButton] = {}
        self._done_text = ""
        body = QVBoxLayout(self)
        body.addWidget(
            plain(
                "Ключ вводится здесь и уходит прямо в хранилище Windows — приложение его "
                "не видит и прочитать обратно не может. Показывается только, подключён "
                "сервис или нет. Пароль Microsoft вводится у Microsoft: Jarvis его не видит."
            )
        )
        for group in GROUPS:
            providers = [
                provider for provider in sorted(SERVICES) if PLACE.get(provider, GROUPS[1]) == group
            ]
            if providers:
                body.addWidget(heading(group))
                body.addLayout(self._keys(providers))
            if group == GROUPS[0] and self.session is not None:
                body.addWidget(heading("Microsoft — почта, календарь, Teams, OneDrive"))
                body.addLayout(self._account())
        body.addWidget(heading("Голос"))
        body.addWidget(
            plain(
                "    Модель распознавания — "
                + (
                    "установлена установщиком."
                    if installed_model()
                    else "не найдена; переустановите Jarvis."
                )
            )
        )
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

    def _keys(self, providers: list[str]) -> QGridLayout:
        grid = QGridLayout()
        grid.setColumnStretch(3, 1)
        for row, provider in enumerate(providers):
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
        return grid

    def _account(self) -> QVBoxLayout:
        column = QVBoxLayout()
        self.account_state = plain("Аккаунт Microsoft: не подключён")
        self.account_state.setObjectName("state-microsoft")
        column.addWidget(self.account_state)
        column.addWidget(
            plain(
                "Регистрация Microsoft: Mobile and desktop, redirect http://localhost. "
                "Вход откроется в системном браузере, пароль вводится только у Microsoft. "
                "Teams и OneDrive — отдельные согласия на тот же аккаунт. " + FILES_ACCOUNT
            )
        )
        row = QHBoxLayout()
        self.client = QLineEdit()
        self.client.setMaxLength(36)
        self.client.setPlaceholderText("Application (client) ID")
        self.client.setAccessibleName("Application (client) ID")
        row.addWidget(self.client, 1)
        self.sign_in = QPushButton("Войти")
        self.sign_in.clicked.connect(self.connect_account)
        row.addWidget(self.sign_in)
        self.sign_out = QPushButton("Отключить")
        self.sign_out.clicked.connect(self.disconnect_account)
        row.addWidget(self.sign_out)
        column.addLayout(row)
        self.account_consent = QCheckBox("Разрешаю подключить Outlook с указанными правами")
        column.addWidget(self.account_consent)
        surfaces = QHBoxLayout()
        for surface, title in SURFACE_BUTTONS:
            button = QPushButton(title)
            button.clicked.connect(lambda _, name=surface: self.allow_surface(name))
            self.surface_buttons[surface] = button
            surfaces.addWidget(button)
        column.addLayout(surfaces)
        # Next to the button that asks for it, because that is the moment the owner chooses
        # which account signs the consent.
        column.addWidget(plain(FILES_ACCOUNT))
        return column

    @property
    def busy(self) -> bool:
        return self.worker is not None or self.account_worker is not None

    @property
    def connected_account(self) -> str:
        account = self.session.account if self.session is not None else None
        return account.address if account is not None else ""

    def showEvent(self, event: QShowEvent) -> None:
        """The first look at this screen is when the answer is wanted — and not before.

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
        self._show_account()
        self._pump()

    def _show_account(self) -> None:
        if self.session is None:
            return
        address = self.connected_account
        self.account_state.setText(
            "Аккаунт Microsoft: " + (f"подключён · {address}" if address else "не подключён")
        )
        for button in self.surface_buttons.values():
            button.setEnabled(bool(address) and not self.busy)
        self.sign_out.setEnabled(bool(address) and not self.busy)
        self.sign_in.setEnabled(not self.busy)

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

    def connect_account(self) -> None:
        """A sign-in the owner asked for, with the consent they ticked beside it."""
        session = self.session
        if session is None or self.busy or self.closed:
            return
        if not self.account_consent.isChecked():
            self.status.setText("Отметьте согласие и укажите Application (client) ID.")
            return
        try:
            client_id = str(UUID(self.client.text().strip()))
        except ValueError:
            self.status.setText("Application (client) ID — это UUID из регистрации Microsoft.")
            return
        self.account_consent.setChecked(False)

        async def connect(context: ExecutionContext) -> object:
            return await session.connect(client_id, context)

        self._account_task(connect, "Аккаунт Microsoft подключён.")

    def allow_surface(self, surface: str) -> None:
        """One more consent on the account already signed in, asked for on its own.

        Teams and OneDrive are separate consents rather than part of the Outlook one: a
        tenant that refuses chat scopes then costs Jarvis the chats, and the mailbox and the
        files go on working.
        """
        session = self.session
        account = session.account if session is not None else None
        if session is None or account is None or self.busy or self.closed:
            return

        async def allow(context: ExecutionContext) -> object:
            await session.consent(account, surface, context)
            return None

        self._account_task(allow, SURFACE_DONE[surface])

    def disconnect_account(self) -> None:
        session = self.session
        if session is None or self.busy or self.closed:
            return

        async def disconnect(context: ExecutionContext) -> object:
            await session.disconnect()
            return None

        self._account_task(
            disconnect,
            "Аккаунт отключён, локальные токены удалены. Согласие отзывается у Microsoft.",
        )

    def _account_task(
        self, work: Callable[[ExecutionContext], Awaitable[object]], done: str
    ) -> None:
        session, audit = self.session, self.audit
        if session is None or audit is None:
            return
        self._done_text = done
        self.status.setText("Обращаюсь к Microsoft…")
        worker = AccountWorker(audited(audit, work, session.detach))
        self.account_worker = worker
        worker.finished.connect(self._account_done)
        self.busy_changed.emit(True)
        self._show_account()
        workers.start(worker)

    def _account_done(self) -> None:
        worker = self.account_worker
        if worker is None:
            return
        worker.wait()
        self.account_worker = None
        self.busy_changed.emit(False)
        if not self.closed:
            self.status.setText(
                "Microsoft: не получилось. Проверьте Application (client) ID и вход."
                if worker.error
                else self._done_text
            )
            self._show_account()
        worker.deleteLater()
        self._pump()

    def _pump(self) -> None:
        if self.busy or self.closed or not self.queue:
            return
        action, provider, secret = self.queue.pop(0)
        self.worker = KeyWorker(action, provider, secret)
        self.worker.finished.connect(self._key_done)
        self.busy_changed.emit(True)
        if action != "ask":
            self.status.setText("Обращаюсь к хранилищу…")
        workers.start(self.worker)

    def _key_done(self) -> None:
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
        account = self.account_worker
        if account is not None:
            account.cancel()
            account.wait(5000)
        if self.worker is not None:
            self.worker.wait(5000)
