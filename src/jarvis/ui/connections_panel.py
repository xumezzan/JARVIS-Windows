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
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal
from uuid import UUID

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
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
from jarvis.ui.components import icon_label
from jarvis.ui.connection_task import AccountWorker, audited
from jarvis.ui.dashboard import line_icon
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

# One outline icon and one colour per service, so a row is recognised before it is read.
ICONS = {
    "deepseek": ("spark", "#7fd6c0"),
    "openai": ("orb", "#b9a8f0"),
    "asana": ("tasks", "#f2857f"),
    "fireflies": ("chat", "#e5c07b"),
    "notion": ("document", "#c3ccd8"),
}
# Said before the consent, not after the first refusal. Microsoft does not serve the Excel
# API on a personal OneDrive at all (docs/ONEDRIVE.md), so on such an account the search
# finds the workbook and every read of its cells is refused - which looks like a defect and
# is a platform boundary.
FILES_ACCOUNT = (
    "Содержимое книг Excel читается только на рабочем или учебном аккаунте. "
    "На личном OneDrive файлы найдутся, а их ячейки — нет."
)
SURFACES = {
    TEAMS_SURFACE: ("Microsoft Teams", "Доступ к чатам и командам.", "chat", "#7fb2f5"),
    FILES_SURFACE: (
        "OneDrive",
        "Доступ к файлам и таблицам. " + FILES_ACCOUNT,
        "document",
        "#7fd6c0",
    ),
}
SURFACE_BUTTONS = ((TEAMS_SURFACE, "Разрешить Teams"), (FILES_SURFACE, "Разрешить OneDrive"))
SURFACE_DONE = {
    TEAMS_SURFACE: "Teams разрешён: чаты читаются, отправка спрашивает.",
    FILES_SURFACE: "OneDrive разрешён: файлы читаются, запись спрашивает.",
}


def plain(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def heading(text: str) -> QLabel:
    label = plain(text)
    label.setObjectName("sectionHint")
    return label


def muted(text: str) -> QLabel:
    label = plain(text)
    label.setObjectName("rowCaption")
    return label


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    line.setFrameShape(QFrame.Shape.NoFrame)
    line.setFixedHeight(1)
    return line


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
        models: Mapping[str, str] | None = None,
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
        self.gear_buttons: dict[str, QPushButton] = {}
        self._done_text = ""
        self.models = dict(models or {})
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        body.addWidget(
            self._section(
                "Модели ИИ",
                "Выберите модель для задач и введите её ключ.",
                "spark",
                [provider for provider in sorted(SERVICES) if PLACE.get(provider) == GROUPS[0]],
            )
        )
        # The Microsoft section exists only where the account does: a screen without the
        # session would be offering a sign-in it cannot carry out.
        if self.session is not None:
            body.addWidget(self._microsoft())
        body.addWidget(
            self._section(
                "Сервисы",
                "Проекты, страницы и записи встреч — по токену на сервис.",
                "apps",
                [provider for provider in sorted(SERVICES) if PLACE.get(provider) != GROUPS[0]],
            )
        )
        body.addWidget(self._voice())
        body.addWidget(self._elsewhere())
        body.addStretch(1)

    # -- Building blocks ---------------------------------------------------------------

    @staticmethod
    def _card(
        title: str = "", caption: str = "", icon: str = "", chip: QLabel | None = None
    ) -> tuple[QFrame, QVBoxLayout]:
        """One rounded surface with one heading, so every section is read the same way."""
        card = QFrame()
        card.setObjectName("card")
        column = QVBoxLayout(card)
        column.setContentsMargins(20, 18, 20, 18)
        column.setSpacing(14)
        if title:
            head = QHBoxLayout()
            head.setSpacing(12)
            if icon:
                head.addWidget(icon_label(icon, "#7fb2f5", 20), 0, Qt.AlignmentFlag.AlignTop)
            text = QVBoxLayout()
            text.setContentsMargins(0, 0, 0, 0)
            text.setSpacing(2)
            heading_label = plain(title)
            heading_label.setObjectName("cardTitle")
            heading_label.setWordWrap(False)
            text.addWidget(heading_label)
            if caption:
                note = plain(caption)
                note.setObjectName("sectionHint")
                text.addWidget(note)
            head.addLayout(text, 1)
            if chip is not None:
                head.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)
            column.addLayout(head)
        return card, column

    def _chip(self, provider: str) -> QLabel:
        """The one word that says whether this service answers, and nothing technical."""
        chip = plain("не проверено")
        chip.setObjectName(f"state-{provider}")
        chip.setProperty("role", "chip")
        chip.setWordWrap(False)
        chip.setAccessibleName(f"Состояние: {vendor_of(provider)}")
        self.states[provider] = chip
        return chip

    def _section(self, title: str, caption: str, icon: str, providers: list[str]) -> QFrame:
        card, column = self._card(title, caption, icon)
        for index, provider in enumerate(providers):
            if index:
                column.addWidget(divider())
            column.addWidget(self._row(provider))
        return card

    def _row(self, provider: str) -> QWidget:
        """One service: what it is, whether it answers, and the two things you can do to it."""
        row = QFrame()
        row.setObjectName("integration")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 2, 0, 2)
        line.setSpacing(14)
        line.addWidget(icon_label(*ICONS.get(provider, ("apps", "#7fb2f5"))), 0)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        name = plain(vendor_of(provider))
        name.setObjectName("rowTitle")
        name.setWordWrap(False)
        text.addWidget(name)
        # The identifier actually in use, where this screen was told it. It is shown and
        # not edited here: the planner owns it, and two editors would disagree by evening.
        detail = self.models.get(provider)
        said = WHAT.get(provider, "")
        note = plain(f"{said} Модель: {detail}." if detail else said)
        note.setObjectName("rowCaption")
        text.addWidget(note)
        line.addLayout(text, 1)
        line.addWidget(self._chip(provider), 0, Qt.AlignmentFlag.AlignVCenter)
        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText("ключ")
        field.setFixedWidth(132)
        field.setAccessibleName(f"Ключ {vendor_of(provider)}")
        field.returnPressed.connect(lambda name=provider: self.save(name))
        self.fields[provider] = field
        line.addWidget(field, 0)
        save = QPushButton("Подключить")
        save.setObjectName("accent")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.setAccessibleName(f"Подключить {vendor_of(provider)}")
        save.clicked.connect(lambda _, name=provider: self.save(name))
        line.addWidget(save, 0)
        gear = QPushButton()
        gear.setObjectName("gear")
        gear.setIcon(line_icon("settings", "#9fb2c8", 20))
        gear.setIconSize(QSize(16, 16))
        gear.setFixedSize(32, 32)
        gear.setCursor(Qt.CursorShape.PointingHandCursor)
        gear.setAccessibleName(f"Ещё о подключении {vendor_of(provider)}")
        gear.setToolTip("Забыть ключ · проверить заново")
        menu = QMenu(gear)
        forget = menu.addAction("Забыть ключ")
        forget.triggered.connect(lambda _=False, name=provider: self.forget(name))
        recheck = menu.addAction("Проверить заново")
        recheck.triggered.connect(self.refresh)
        gear.setMenu(menu)
        self.gear_buttons[provider] = gear
        line.addWidget(gear, 0)
        return row

    def _microsoft(self) -> QFrame:
        """One account behind four surfaces, and one consent asked for each of them."""
        card, column = self._card("Microsoft", "Почта · календарь · Teams · OneDrive", "apps")
        self.account_chip = plain("не подключён")
        self.account_chip.setProperty("role", "chip")
        self.account_chip.setWordWrap(False)
        self.account_chip.setAccessibleName("Состояние аккаунта Microsoft")
        heading_row = column.itemAt(0)
        layout = heading_row.layout() if heading_row is not None else None
        if layout is not None:
            layout.addWidget(self.account_chip)

        inner = QFrame()
        inner.setObjectName("inset")
        account = QVBoxLayout(inner)
        account.setContentsMargins(16, 14, 16, 14)
        account.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(icon_label("user", "#7fb2f5", 18), 0, Qt.AlignmentFlag.AlignTop)
        said = QVBoxLayout()
        said.setContentsMargins(0, 0, 0, 0)
        said.setSpacing(3)
        self.account_state = plain("Аккаунт Microsoft: не подключён")
        self.account_state.setObjectName("state-microsoft")
        self.account_state.setProperty("role", "rowTitle")
        said.addWidget(self.account_state)
        said.addWidget(
            muted(
                "Вход откроется в системном браузере, пароль вводится только у Microsoft. "
                "Регистрация: Mobile and desktop, redirect http://localhost. "
                "Teams и OneDrive — отдельные согласия на тот же аккаунт."
            )
        )
        top.addLayout(said, 1)
        account.addLayout(top)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.client = QLineEdit()
        self.client.setMaxLength(36)
        self.client.setPlaceholderText("Application (client) ID")
        self.client.setAccessibleName("Application (client) ID")
        row.addWidget(self.client, 1)
        self.sign_in = QPushButton("Войти")
        self.sign_in.setObjectName("accent")
        self.sign_in.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sign_in.clicked.connect(self.connect_account)
        row.addWidget(self.sign_in)
        self.sign_out = QPushButton("Отключить")
        self.sign_out.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sign_out.clicked.connect(self.disconnect_account)
        row.addWidget(self.sign_out)
        account.addLayout(row)
        self.account_consent = QCheckBox("Разрешаю подключить Outlook с указанными правами")
        account.addWidget(self.account_consent)
        column.addWidget(inner)

        surfaces = QHBoxLayout()
        surfaces.setSpacing(14)
        for surface, title in SURFACE_BUTTONS:
            surfaces.addWidget(self._surface(surface, title), 1)
        column.addLayout(surfaces)
        return card

    def _surface(self, surface: str, title: str) -> QFrame:
        """A consent of its own: a tenant that refuses chats still leaves the files working."""
        name, caption, icon, colour = SURFACES[surface]
        card = QFrame()
        card.setObjectName("inset")
        column = QVBoxLayout(card)
        column.setContentsMargins(16, 14, 16, 14)
        column.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(icon_label(icon, colour, 18), 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        label = plain(name)
        label.setObjectName("rowTitle")
        label.setWordWrap(False)
        text.addWidget(label)
        text.addWidget(muted(caption))
        head.addLayout(text, 1)
        column.addLayout(head)
        button = QPushButton(title)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _, chosen=surface: self.allow_surface(chosen))
        self.surface_buttons[surface] = button
        column.addWidget(button, 0, Qt.AlignmentFlag.AlignRight)
        return card

    def _voice(self) -> QFrame:
        card, column = self._card()
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(icon_label("mic", "#7fb2f5", 20), 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        title = plain("Голос")
        title.setObjectName("rowTitle")
        text.addWidget(title)
        installed = bool(installed_model())
        text.addWidget(
            muted(
                "Модель распознавания установлена установщиком."
                if installed
                else "Модель распознавания не найдена — переустановите Jarvis."
            )
        )
        row.addLayout(text, 1)
        self.voice_chip = plain("готов" if installed else "нет модели")
        self.voice_chip.setProperty("role", "chip")
        self.voice_chip.setProperty("state", "on" if installed else "off")
        self.voice_chip.setWordWrap(False)
        self.voice_chip.setAccessibleName("Состояние распознавания речи")
        row.addWidget(self.voice_chip, 0)
        column.addLayout(row)
        return card

    def _elsewhere(self) -> QFrame:
        """Naming what is connected elsewhere, so this list does not read as the whole list."""
        card, column = self._card()
        head = QHBoxLayout()
        head.setSpacing(14)
        head.addWidget(icon_label("info", "#8496ab", 18), 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(4)
        title = plain("Подключаются иначе")
        title.setObjectName("rowTitle")
        text.addWidget(title)
        for name, where in ELSEWHERE:
            text.addWidget(muted(f"{name} — {where}"))
        head.addLayout(text, 1)
        column.addLayout(head)
        column.addWidget(divider())
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        self.status = plain("")
        self.status.setObjectName("sectionHint")
        self.status.setAccessibleName("Что происходит с подключениями")
        bottom.addWidget(self.status, 1)
        self.recheck = QPushButton("Проверить заново")
        self.recheck.setCursor(Qt.CursorShape.PointingHandCursor)
        self.recheck.clicked.connect(self.refresh)
        bottom.addWidget(self.recheck, 0)
        column.addLayout(bottom)
        return card

    @staticmethod
    def _say(chip: QLabel, text: str, state: str) -> None:
        """Set the word and the colour together; a chip that says one and shows the other lies."""
        chip.setText(text)
        chip.setProperty("state", state)
        chip.style().unpolish(chip)
        chip.style().polish(chip)

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
            self._say(state, "проверяю…", "pending")
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
        self._say(
            self.account_chip,
            "подключён" if address else "не подключён",
            "on" if address else "off",
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
        self._say(
            self.states[worker.provider],
            "подключён" if worker.connected else "не подключён",
            "on" if worker.connected else "off",
        )
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
