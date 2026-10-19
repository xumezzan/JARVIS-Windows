"""Bounded, in-memory task conversation. A reply supplies data, never approval."""

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.planner.contracts import Clarification


class ReplyChoice(QPushButton):
    """A full-width answer whose height follows wrapped text at the current width."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.setAutoDefault(False)
        self.setObjectName("replyChoice")
        self.setAccessibleName(text)
        self.setMinimumHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        self.caption = QLabel(text)
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.caption.setWordWrap(True)
        self.caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.caption)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return max(44, self.caption.heightForWidth(max(1, width - 24)) + 16)

    def sizeHint(self) -> QSize:
        return QSize(180, self.heightForWidth(max(180, self.width())))

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 44)


class TaskChat(QFrame):
    answered = Signal(str, str)
    cancel_requested = Signal()
    question_shown = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("taskChat")
        self.setAccessibleName("Чат с Jarvis")
        self.pending_id: str | None = None
        self.messages: list[QFrame] = []
        self.choice_buttons: list[QPushButton] = []
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        self.history_scroll = QScrollArea()
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.history_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_scroll.setMinimumHeight(160)
        self.history_scroll.setMaximumHeight(250)
        self.history = QWidget()
        self.history.setObjectName("chatHistory")
        self.history_layout = QVBoxLayout(self.history)
        self.history_layout.setContentsMargins(0, 0, 6, 0)
        self.history_layout.setSpacing(8)
        self.history_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.history_scroll.setWidget(self.history)
        self.history_scroll.verticalScrollBar().rangeChanged.connect(self._scroll_bottom)
        body.addWidget(self.history_scroll)
        self.reply_box = QWidget()
        self.reply_box.setObjectName("chatReply")
        reply = QVBoxLayout(self.reply_box)
        reply.setContentsMargins(0, 0, 0, 0)
        reply.setSpacing(8)
        self.choices = QVBoxLayout()
        self.choices.setSpacing(6)
        reply.addLayout(self.choices)
        row = QHBoxLayout()
        self.answer_input = QLineEdit()
        self.answer_input.setMaxLength(4000)
        self.answer_input.setPlaceholderText("Написать свой ответ…")
        self.answer_input.setAccessibleName("Ответ Jarvis")
        self.answer_input.returnPressed.connect(self.submit)
        row.addWidget(self.answer_input, 1)
        self.answer_button = QPushButton("Отправить")
        self.answer_button.setAutoDefault(False)
        self.answer_button.setEnabled(False)
        self.answer_button.clicked.connect(self.submit)
        self.answer_input.textChanged.connect(self._answer_changed)
        row.addWidget(self.answer_button)
        reply.addLayout(row)
        self.stop_button = QPushButton("Остановить задачу")
        self.stop_button.setObjectName("link")
        self.stop_button.setAutoDefault(False)
        self.stop_button.clicked.connect(self.cancel_requested.emit)
        reply.addWidget(self.stop_button)
        body.addWidget(self.reply_box)
        self.reply_box.hide()

    def start(self, command: str) -> None:
        self.dismiss()
        for message in self.messages:
            self.history_layout.removeWidget(message)
            message.deleteLater()
        self.messages.clear()
        self.append("Вы", command)

    def append(self, author: str, text: str) -> None:
        bubble = QFrame()
        bubble.setObjectName("chatUser" if author == "Вы" else "chatAssistant")
        bubble.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(4)
        name = QLabel(author)
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setObjectName("chatAuthor")
        layout.addWidget(name)
        content = QLabel(text[:16000])
        content.setTextFormat(Qt.TextFormat.PlainText)
        content.setWordWrap(True)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(content)
        self.history_layout.addWidget(bubble)
        self.messages.append(bubble)
        if len(self.messages) > 12:
            oldest = self.messages.pop(0)
            self.history_layout.removeWidget(oldest)
            oldest.deleteLater()
        QTimer.singleShot(0, self._scroll_bottom)

    def _scroll_bottom(self) -> None:
        bar = self.history_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _answer_changed(self, text: str) -> None:
        self.answer_button.setEnabled(self.pending_id is not None and bool(text.strip()))

    def ask(self, prompt_id: str, question: Clarification) -> None:
        self.dismiss()
        self.pending_id = prompt_id
        self.append("Jarvis", question.question)
        for choice in question.choices:
            button = ReplyChoice(choice)
            button.clicked.connect(
                lambda checked=False, value=choice, identity=prompt_id: self.reply(value, identity)
            )
            self.choices.addWidget(button)
            self.choice_buttons.append(button)
        self.reply_box.show()
        self.answer_input.setFocus()
        self.question_shown.emit()

    def submit(self) -> None:
        self.reply(self.answer_input.text())

    def reply(self, text: str, expected_id: str | None = None) -> None:
        prompt_id = self.pending_id
        if (
            prompt_id is None
            or (expected_id is not None and expected_id != prompt_id)
            or not text.strip()
            or len(text) > 4000
        ):
            return
        # Disable stale controls before dispatching: rapid clicks must never answer twice.
        self.dismiss()
        self.append("Вы", text)
        self.answered.emit(prompt_id, text)

    def dismiss(self) -> None:
        self.pending_id = None
        self.reply_box.hide()
        self.answer_input.clear()
        self.answer_button.setEnabled(False)
        for button in self.choice_buttons:
            button.setEnabled(False)
            self.choices.removeWidget(button)
            button.deleteLater()
        self.choice_buttons.clear()
