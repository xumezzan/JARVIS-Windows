"""Questions stay in the task chat and replies never become action approvals."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from pytestqt.qtbot import QtBot
from tests.unit.test_planner import MESSAGE, Scripted

from jarvis.config import AppConfig
from jarvis.core.planner.contracts import Answer, Limits, PlannerInput, Proposal, ProviderError
from jarvis.core.planner.offline import call
from jarvis.observability.logging import ShellLog
from jarvis.ui.main_window import ERROR_ADVICE, MainWindow
from jarvis.ui.states import UiState

pytestmark = pytest.mark.integration
PATIENCE = 15000


@pytest.fixture
def shell(qtbot: QtBot, tmp_path: Path) -> Iterator[MainWindow]:
    window = MainWindow(AppConfig(tmp_path), ShellLog(tmp_path / "logs"), probe_connections=False)
    qtbot.addWidget(window)
    window.show()
    yield window
    window.shutdown()
    window.close()


def begin(qtbot: QtBot, shell: MainWindow, provider: Scripted) -> None:
    planner = shell.planner_window
    assert planner is not None
    qtbot.waitUntil(lambda: planner.memory.worker is None, timeout=PATIENCE)
    planner.override_provider = provider
    shell.command_input.setPlainText("Открой блокнот и напиши список дел на завтра")
    shell.submit()
    qtbot.waitUntil(lambda: shell.chat.pending_id is not None, timeout=PATIENCE)


def test_choice_resumes_same_task_and_is_not_chosen_automatically(
    qtbot: QtBot, shell: MainWindow
) -> None:
    provider = Scripted(
        [
            Proposal(
                kind="clarify",
                question="Нет подключённых источников дел. Как подготовить список?",
                choices=["Сделать пустой шаблон", "Я перечислю дела"],
            ),
            call("local.check", {}),
        ]
    )
    begin(qtbot, shell, provider)
    planner = shell.planner_window
    assert planner is not None and planner.worker is not None
    worker = planner.worker
    assert planner.question_host is shell.chat
    assert not planner.isVisible() and QApplication.activeModalWidget() is None
    assert shell.state == UiState.AWAITING_INPUT
    assert shell.command_input.isReadOnly() and shell.chat.answer_input.isEnabled()
    assert len(provider.inputs) == 1 and not worker.runner.steps
    assert len(shell.chat.choice_buttons) == 2
    assert shell.chat.answer_input.text() == "" and not shell.chat.answer_button.isEnabled()
    request_id = shell._request_id
    with qtbot.waitSignal(shell.task_finished, timeout=PATIENCE) as result:
        QTest.mouseClick(shell.chat.choice_buttons[0], Qt.MouseButton.LeftButton)
    assert result.args == ["finished"]
    assert provider.inputs[1].command == provider.inputs[0].command
    assert provider.inputs[1].answers == (
        Answer("Нет подключённых источников дел. Как подготовить список?", "Сделать пустой шаблон"),
    )
    assert shell.chat.pending_id is None and not shell.chat.reply_box.isVisible()
    log = shell.log.path.read_text(encoding="utf-8")
    assert log.count('"event": "command_submitted"') == 1
    assert str(request_id) in log
    assert "Сделать пустой шаблон" not in log


def test_custom_reply_enter_and_old_prompt_cannot_answer_next_question(
    qtbot: QtBot, shell: MainWindow
) -> None:
    provider = Scripted(
        [
            Proposal(kind="clarify", question="Источник?", choices=["Мои заметки"]),
            Proposal(kind="clarify", question="Какие дела?", choices=["Пустой шаблон"]),
            call("local.check", {}),
        ]
    )
    begin(qtbot, shell, provider)
    old_id = shell.chat.pending_id
    assert old_id is not None
    QTest.mouseClick(shell.chat.choice_buttons[0], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: shell.chat.pending_id is not None and shell.chat.pending_id != old_id,
        timeout=PATIENCE,
    )
    new_id = shell.chat.pending_id
    # A delayed response from the former question cannot consume the new prompt.
    shell.chat.reply("запоздалый ответ", old_id)
    assert shell.chat.pending_id == new_id and len(provider.inputs) == 2
    shell.chat.answer_input.setText("PRIVATE_REPLY_SENTINEL: проверить отчёт")
    with qtbot.waitSignal(shell.task_finished, timeout=PATIENCE) as result:
        QTest.keyClick(shell.chat.answer_input, Qt.Key.Key_Return)
    assert result.args == ["finished"]
    assert provider.inputs[2].answers[-1] == Answer(
        "Какие дела?", "PRIVATE_REPLY_SENTINEL: проверить отчёт"
    )
    assert "PRIVATE_REPLY_SENTINEL" not in shell.log.path.read_text(encoding="utf-8")


def test_reply_choice_cannot_approve_following_confirm_action(
    qtbot: QtBot, shell: MainWindow
) -> None:
    shell.autonomy.setChecked(False)
    provider = Scripted(
        [
            Proposal(kind="clarify", question="Что подготовить?", choices=["Сообщение"]),
            call("local.append_message", MESSAGE),
        ]
    )
    begin(qtbot, shell, provider)
    planner = shell.planner_window
    assert planner is not None
    QTest.mouseClick(shell.chat.choice_buttons[0], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: planner.approval_dialog is not None, timeout=PATIENCE)
    assert planner.outbox.count == 0 and planner.pending_question is None
    dialog = planner.approval_dialog
    assert dialog is not None and dialog.token is None
    assert QApplication.activeModalWidget() is dialog
    with qtbot.waitSignal(shell.task_finished, timeout=PATIENCE) as result:
        dialog.reject()
    assert result.args == ["error"] and planner.outbox.count == 0


@pytest.mark.parametrize("stop", ["button", "escape", "close", "timeout"])
def test_pending_chat_is_cleared_when_task_stops(
    qtbot: QtBot, shell: MainWindow, stop: str
) -> None:
    planner = shell.planner_window
    assert planner is not None
    if stop == "timeout":
        planner.limits = Limits(total_seconds=0.5)
    provider = Scripted([Proposal(kind="clarify", question="Куда?", choices=["В новый файл"])])
    begin(qtbot, shell, provider)
    old_id = shell.chat.pending_id
    with qtbot.waitSignal(shell.task_finished, timeout=PATIENCE) as result:
        if stop == "button":
            QTest.mouseClick(shell.chat.stop_button, Qt.MouseButton.LeftButton)
        elif stop == "escape":
            QTest.keyClick(shell.chat.answer_input, Qt.Key.Key_Escape)
        elif stop == "close":
            shell.close()
    assert result.args == ["timeout" if stop == "timeout" else "cancelled"]
    assert shell.chat.pending_id is None and not shell.chat.reply_box.isVisible()
    assert not shell.chat.choice_buttons and planner.pending_question is None
    assert planner.worker is None and not shell.running
    shell.chat.reply("поздний ответ", old_id)
    assert len(provider.inputs) == 1
    assert "command_succeeded" not in shell.log.path.read_text(encoding="utf-8")


def test_question_and_choices_are_plain_text(qtbot: QtBot, shell: MainWindow) -> None:
    question = '<img src="https://invalid.example/pixel"> PRIVATE_QUESTION'
    choice = '<a href="https://invalid.example">Шаблон & заметки</a>'
    begin(qtbot, shell, Scripted([Proposal(kind="clarify", question=question, choices=[choice])]))
    labels = shell.chat.findChildren(QLabel)
    shown = [item for item in labels if item.text() in (question, choice)]
    assert len(shown) == 2
    assert all(item.textFormat() == Qt.TextFormat.PlainText for item in shown)
    assert all(not item.openExternalLinks() for item in shown)
    assert "PRIVATE_QUESTION" not in shell.log.path.read_text(encoding="utf-8")
    with qtbot.waitSignal(shell.task_finished, timeout=PATIENCE):
        shell.stop()


def test_long_answers_wrap_without_clipping_in_compact_window(
    qtbot: QtBot, shell: MainWindow
) -> None:
    shell.resize(860, 640)
    long_answer = (
        "Подготовить пустой шаблон плана на завтра с разделами для важных дел, "
        "встреч и личных заметок"
    )
    begin(
        qtbot,
        shell,
        Scripted(
            [Proposal(kind="clarify", question="Как подготовить план?", choices=[long_answer])]
        ),
    )
    qtbot.waitUntil(lambda: shell.scroll_area.horizontalScrollBar().maximum() == 0)
    for button in shell.chat.choice_buttons:
        caption = button.findChild(QLabel)
        assert caption is not None
        assert button.rect().contains(caption.geometry())
        assert caption.height() >= caption.heightForWidth(caption.width())
    with qtbot.waitSignal(shell.task_finished, timeout=PATIENCE):
        shell.stop()


def test_error_advice_is_visible_in_the_chat(qtbot: QtBot, shell: MainWindow) -> None:
    class MissingCredentials:
        async def propose(self, data: PlannerInput) -> Proposal:
            raise ProviderError("credentials")

    planner = shell.planner_window
    assert planner is not None
    qtbot.waitUntil(lambda: planner.memory.worker is None, timeout=PATIENCE)
    planner.override_provider = MissingCredentials()
    shell.command_input.setPlainText("тест")
    with qtbot.waitSignal(shell.task_finished, timeout=PATIENCE) as result:
        shell.submit()
    assert result.args == ["error"]
    visible_text = [label.text() for label in shell.chat.findChildren(QLabel)]
    assert any(ERROR_ADVICE["credentials"] in text for text in visible_text)
