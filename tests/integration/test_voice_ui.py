"""Real Qt gestures with deterministic audio; planner effects still require exact approval."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot
from tests.unit.test_planner import MESSAGE, Scripted
from tests.voice_support import VoiceFixture

from jarvis.config import AppConfig
from jarvis.core.planner.contracts import Proposal
from jarvis.core.planner.offline import call
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore, Channel
from jarvis.permissions.policies import Mode, Risk
from jarvis.ui.approval_dialog import ApprovalDialog
from jarvis.ui.planner_window import PlannerWindow
from jarvis.ui.voice_panel import HoldButton, VoicePanel
from jarvis.ui.voice_worker import VoiceWorker
from jarvis.voice.approval import Confirmation
from jarvis.voice.contracts import ERROR_TEXT


def make_window(qtbot: QtBot, tmp_path: Path, fixture: VoiceFixture) -> PlannerWindow:
    panel = VoicePanel(recorder=fixture, recognizer=fixture, speaker=fixture)
    window = PlannerWindow(AppConfig(tmp_path), voice_panel=panel)
    qtbot.addWidget(window)
    window.show()
    return window


def record(qtbot: QtBot, panel: VoicePanel, button: HoldButton | None = None) -> None:
    hold = button or panel.hold
    QTest.mousePress(hold, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Идёт запись" in panel.status.text())
    QTest.mouseRelease(hold, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.worker is None)


def test_installed_model_path_is_prefilled_without_capture(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = str(tmp_path / "Асаль voice model")
    monkeypatch.setenv("JARVIS_VOSK_MODEL", model)
    fixture = VoiceFixture("fixture")
    window = make_window(qtbot, tmp_path, fixture)
    try:
        assert window.voice.model_path.text() == model
        assert window.voice.worker is None and fixture.captures == 0
        assert not fixture.spoken
    finally:
        window.shutdown()


def test_review_edit_and_explicit_submission(qtbot: QtBot, tmp_path: Path) -> None:
    fixture = VoiceFixture("ошибочно распознанная команда", confidence=0.3)
    window = make_window(qtbot, tmp_path, fixture)
    try:
        window.voice.hold.click()
        window.voice.hold.held.emit()
        assert fixture.captures == 0  # A generic click is not a recording gesture.
        record(qtbot, window.voice)
        assert window.worker is None
        assert fixture.record_closed.is_set()
        assert window.command.toPlainText() == fixture.text
        assert "неуверенное" in window.voice.status.text()
        window.command.setPlainText("проверь систему дважды")
        with qtbot.waitSignal(window.task_finished) as result:
            QTest.mouseClick(window.run_button, Qt.MouseButton.LeftButton)
        assert result.args == ["simulated"]
        assert window.output.toPlainText().count("local.check:") == 2
        assert not fixture.spoken
    finally:
        window.shutdown()


@pytest.mark.parametrize("phase", ["recording", "transcribing", "speaking"])
@pytest.mark.parametrize("close", [False, True])
def test_cancel_every_audio_phase_no_stale_result(
    qtbot: QtBot,
    tmp_path: Path,
    phase: str,
    close: bool,
) -> None:
    fixture = VoiceFixture()
    fixture.recognize_delay = 30 if phase == "transcribing" else 0
    fixture.speech_delay = 30
    window = make_window(qtbot, tmp_path, fixture)
    try:
        if phase == "speaking":
            window.voice.speech_enabled.setChecked(True)
            window.command.setPlainText("проверь систему")
            window.start()
            qtbot.waitUntil(fixture.speaking.is_set)
        else:
            QTest.mousePress(window.voice.hold, Qt.MouseButton.LeftButton)
            qtbot.waitUntil(fixture.recording.is_set)
            if phase == "transcribing":
                QTest.mouseRelease(window.voice.hold, Qt.MouseButton.LeftButton)
                qtbot.waitUntil(fixture.recognizing.is_set)
        if close:
            QTest.keyClick(window, Qt.Key.Key_Escape)
        else:
            QTest.mouseClick(window.voice.stop_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: window.voice.worker is None)
        assert window.worker is None
        assert not window.voice.deadline.isActive()
        if phase != "speaking":
            assert fixture.record_closed.is_set()
            assert not window.command.toPlainText()
        else:
            assert fixture.speech_closed.is_set()
    finally:
        window.shutdown()


@pytest.mark.parametrize("phase", ["provider", "approval", "clarification"])
def test_voice_cancel_pending_planner(qtbot: QtBot, tmp_path: Path, phase: str) -> None:
    fixture = VoiceFixture("стоп")
    provider = Scripted(
        [Proposal(kind="clarify", question="Куда?")]
        if phase == "clarification"
        else [call("local.append_message", MESSAGE)],
        delay=30 if phase == "provider" else 0,
    )
    panel = VoicePanel(recorder=fixture, recognizer=fixture, speaker=fixture)
    window = PlannerWindow(AppConfig(tmp_path), voice_panel=panel, provider=provider)
    qtbot.addWidget(window)
    window.show()
    try:
        window.simulation.setChecked(False)
        window.command.setPlainText("команда")
        window.start()
        hold = panel.hold
        if phase != "provider":
            qtbot.waitUntil(
                lambda: window.approval_dialog is not None or window.question_dialog is not None
            )
            dialog = window.approval_dialog or window.question_dialog
            assert dialog is not None
            found = dialog.findChild(HoldButton)
            assert found is not None
            hold = found
        with qtbot.waitSignal(window.task_finished) as result:
            record(qtbot, panel, hold)
        assert result.args == ["cancelled"]
        assert window.outbox.count == 0 and window.approval_dialog is None
    finally:
        window.shutdown()


def test_speech_cannot_approve_or_replace_running_command(qtbot: QtBot, tmp_path: Path) -> None:
    fixture = VoiceFixture("подтверждаю отправку")
    panel = VoicePanel(recorder=fixture, recognizer=fixture, speaker=fixture)
    window = PlannerWindow(
        AppConfig(tmp_path),
        voice_panel=panel,
        provider=Scripted([call("local.append_message", MESSAGE)]),
    )
    qtbot.addWidget(window)
    window.show()
    try:
        window.simulation.setChecked(False)
        window.command.setPlainText("исходная команда")
        window.start()
        qtbot.waitUntil(lambda: window.approval_dialog is not None)
        dialog = window.approval_dialog
        assert dialog is not None
        hold = dialog.findChild(HoldButton)
        assert hold is not None
        record(qtbot, panel, hold)
        assert dialog.token is None and window.outbox.count == 0
        assert window.command.toPlainText() == "исходная команда"
        with qtbot.waitSignal(window.task_finished):
            QTest.mouseClick(dialog.approve_button, Qt.MouseButton.LeftButton)
        assert window.outbox.count == 1
    finally:
        window.shutdown()


def test_no_tts_microphone_feedback(qtbot: QtBot, tmp_path: Path) -> None:
    fixture = VoiceFixture()
    fixture.speech_delay = 30
    window = make_window(qtbot, tmp_path, fixture)
    try:
        window.voice.speech_enabled.setChecked(True)
        window.command.setPlainText("проверь систему")
        window.start()
        qtbot.waitUntil(fixture.speaking.is_set)
        QTest.mousePress(window.voice.hold, Qt.MouseButton.LeftButton)
        QTest.mouseRelease(window.voice.hold, Qt.MouseButton.LeftButton)
        assert fixture.captures == 0 and not window.run_button.isEnabled()
        assert "Симуляция" in fixture.spoken[0]
        window.stop()
        qtbot.waitUntil(lambda: window.voice.worker is None)
    finally:
        window.shutdown()


@pytest.mark.parametrize("error", ["device", "silence", "overflow", "timeout", "voice_failed"])
def test_device_errors_allow_text_fallback(qtbot: QtBot, tmp_path: Path, error: str) -> None:
    fixture = VoiceFixture()
    fixture.error = error
    window = make_window(qtbot, tmp_path, fixture)
    try:
        record(qtbot, window.voice)
        assert window.worker is None and window.run_button.isEnabled()
        assert fixture.record_closed.is_set()
        window.command.setPlainText("проверь систему")
        with qtbot.waitSignal(window.task_finished):
            window.start()
    finally:
        window.shutdown()


def test_voice_lifetime_expires_review(qtbot: QtBot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path, VoiceFixture())
    try:
        record(qtbot, window.voice)
        window.voice.deadline.start(20)
        qtbot.waitUntil(lambda: not window.command.toPlainText())
        assert window.worker is None
    finally:
        window.shutdown()


def test_cloud_adapter_rejected_without_any_calls() -> None:
    fixture = VoiceFixture()
    fixture.local_only = False
    with pytest.raises(ValueError, match="consent"):
        VoiceWorker(fixture, fixture, fixture)
    assert fixture.captures == 0 and not fixture.spoken


def test_escape_in_modal_cancels_voice_and_plan(qtbot: QtBot, tmp_path: Path) -> None:
    fixture = VoiceFixture("стоп")
    panel = VoicePanel(recorder=fixture, recognizer=fixture, speaker=fixture)
    window = PlannerWindow(
        AppConfig(tmp_path),
        voice_panel=panel,
        provider=Scripted([call("local.append_message", MESSAGE)]),
    )
    qtbot.addWidget(window)
    window.show()
    try:
        window.command.setPlainText("команда")
        window.start()
        qtbot.waitUntil(lambda: window.approval_dialog is not None)
        dialog = window.approval_dialog
        assert dialog is not None
        hold = dialog.findChild(HoldButton)
        assert hold is not None
        QTest.mousePress(hold, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(fixture.recording.is_set)
        with qtbot.waitSignal(window.task_finished) as result:
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        qtbot.waitUntil(lambda: panel.worker is None)
        assert result.args == ["cancelled"] and fixture.record_closed.is_set()
        assert window.outbox.count == 0 and not window.voice.deadline.isActive()
    finally:
        window.shutdown()


def test_hands_free_listens_again_and_only_obeys_its_name(qtbot: QtBot, tmp_path: Path) -> None:
    fixture = VoiceFixture(text="сегодня дождь")
    window = make_window(qtbot, tmp_path, fixture)
    panel = window.voice
    try:
        assert not panel.hands_free and panel.worker is None  # Never listening by default.
        panel.set_hands_free(True)
        qtbot.waitUntil(lambda: fixture.listens == 1)
        # A phrase that does not name the assistant is dropped, and capture resumes.
        fixture.speech_ends.set()
        qtbot.waitUntil(lambda: "Пропущено" in panel.status.text(), timeout=5000)
        assert window.command.toPlainText() == ""
        fixture.speech_ends.clear()
        qtbot.waitUntil(lambda: fixture.listens == 2, timeout=5000)
        # Naming the assistant submits only the words that follow it.
        fixture.text = "джарвис проверь систему"
        fixture.speech_ends.set()
        qtbot.waitUntil(lambda: window.command.toPlainText() == "проверь систему", timeout=5000)
        panel.set_hands_free(False)
        qtbot.waitUntil(lambda: panel.worker is None, timeout=5000)
        captures = fixture.captures
        qtbot.wait(400)
        assert fixture.captures == captures  # Switching off really stops the microphone.
    finally:
        window.shutdown()


def test_hands_free_stops_on_a_device_failure(qtbot: QtBot, tmp_path: Path) -> None:
    fixture = VoiceFixture()
    fixture.error = "device"
    window = make_window(qtbot, tmp_path, fixture)
    panel = window.voice
    try:
        panel.set_hands_free(True)
        fixture.speech_ends.set()
        qtbot.waitUntil(lambda: not panel.hands_free, timeout=5000)
        assert ERROR_TEXT["device"] in panel.status.text()
        captures = fixture.captures
        qtbot.wait(400)
        assert fixture.captures == captures
    finally:
        window.shutdown()


def awaiting_approval(
    qtbot: QtBot,
    tmp_path: Path,
    fixture: VoiceFixture,
    *,
    subject: str = "Тест разрешений",
    hands_free: bool = False,
) -> PlannerWindow:
    """One real CONFIRM step of a running plan, stopped at its confirmation."""
    panel = VoicePanel(recorder=fixture, recognizer=fixture, speaker=fixture)
    window = PlannerWindow(
        AppConfig(tmp_path),
        voice_panel=panel,
        provider=Scripted([call("local.append_message", MESSAGE | {"subject": subject})]),
    )
    qtbot.addWidget(window)
    window.show()
    # The memory panel reads its store on the next turn; starting before it settles would
    # measure that start-up instead of the confirmation.
    qtbot.waitUntil(lambda: window.memory.worker is None, timeout=15000)
    window.simulation.setChecked(False)
    if hands_free:
        # The command itself arrives by voice, the way it does with standing capture on.
        spoken, fixture.text = fixture.text, "джарвис команда"
        panel.set_hands_free(True)
        qtbot.waitUntil(lambda: fixture.listens == 1)
        fixture.speech_ends.set()
        qtbot.waitUntil(lambda: window.command.toPlainText() == "команда")
        fixture.speech_ends.clear()
        fixture.text = spoken
    else:
        window.command.setPlainText("команда")
    window.start()
    qtbot.waitUntil(lambda: window.approval_dialog is not None)
    return window


def approvals(window: PlannerWindow) -> list[str]:
    records = [json.loads(record) for record in window.audit.recent()]
    return [record["actor"] for record in records if record["event"] == "approved"]


def test_the_spoken_detail_approves_the_exact_action(qtbot: QtBot, tmp_path: Path) -> None:
    fixture = VoiceFixture("разрешений")
    window = awaiting_approval(qtbot, tmp_path, fixture)
    try:
        dialog = window.approval_dialog
        assert dialog is not None and dialog.detail is not None
        assert dialog.detail.word.casefold() == "разрешений"
        # The confirmation appearing on screen never opens the microphone by itself.
        assert fixture.captures == 0 and dialog.listen_button is not None
        fixture.speech_ends.set()
        with qtbot.waitSignal(window.task_finished) as result:
            QTest.mouseClick(dialog.listen_button, Qt.MouseButton.LeftButton)
        assert result.args == ["finished"] and window.outbox.count == 1
        assert dialog.token is not None and dialog.token.channel is Channel.VOICE
        assert approvals(window) == ["user_voice"]
    finally:
        window.shutdown()


def test_standing_capture_arms_the_confirmation_and_frees_the_microphone(
    qtbot: QtBot, tmp_path: Path
) -> None:
    fixture = VoiceFixture("разрешений")
    window = awaiting_approval(qtbot, tmp_path, fixture, hands_free=True)
    try:
        dialog = window.approval_dialog
        assert dialog is not None
        # Standing capture is already the owner's visible choice, so it asks without a press.
        qtbot.waitUntil(lambda: window.voice.confirming is not None)
        with qtbot.waitSignal(window.task_finished) as result:
            fixture.speech_ends.set()
        assert result.args == ["finished"] and window.outbox.count == 1
        assert dialog.token is not None and dialog.token.channel is Channel.VOICE
    finally:
        window.shutdown()


@pytest.mark.parametrize(
    ("text", "confidence", "verdict"),
    [
        ("да, подтверждаю", 0.99, Confirmation.MISMATCH),
        ("договор", 0.99, Confirmation.MISMATCH),
        ("разрешений", 0.3, Confirmation.UNCERTAIN),
    ],
)
def test_nothing_but_the_detail_reaches_the_authority(
    qtbot: QtBot, tmp_path: Path, text: str, confidence: float, verdict: str
) -> None:
    fixture = VoiceFixture(text, confidence=confidence)
    window = awaiting_approval(qtbot, tmp_path, fixture)
    try:
        dialog = window.approval_dialog
        assert dialog is not None and dialog.listen_button is not None
        fixture.speech_ends.set()
        with qtbot.waitSignal(window.voice.confirmation) as heard:
            QTest.mouseClick(dialog.listen_button, Qt.MouseButton.LeftButton)
        assert heard.args == [verdict]
        assert dialog.token is None and window.outbox.count == 0
        assert window.approval_dialog is dialog and not approvals(window)
        # The confirmation stays open, and the button it never replaced still works.
        assert dialog.listen_button.isEnabled()
        with qtbot.waitSignal(window.task_finished) as result:
            QTest.mouseClick(dialog.approve_button, Qt.MouseButton.LeftButton)
        assert result.args == ["finished"] and window.outbox.count == 1
        assert approvals(window) == ["user_ui"]
    finally:
        window.shutdown()


def test_a_spoken_refusal_confirms_nothing_and_leaves_no_effect(
    qtbot: QtBot, tmp_path: Path
) -> None:
    fixture = VoiceFixture("отмена")
    window = awaiting_approval(qtbot, tmp_path, fixture)
    try:
        dialog = window.approval_dialog
        assert dialog is not None and dialog.listen_button is not None
        fixture.speech_ends.set()
        with qtbot.waitSignal(window.task_finished) as result:
            QTest.mouseClick(dialog.listen_button, Qt.MouseButton.LeftButton)
        assert result.args == ["error"] and window.outbox.count == 0
        assert dialog.token is None and not approvals(window)
    finally:
        window.shutdown()


def test_the_detail_is_spoken_before_the_microphone_opens(qtbot: QtBot, tmp_path: Path) -> None:
    fixture = VoiceFixture("разрешений")
    window = awaiting_approval(qtbot, tmp_path, fixture)
    try:
        dialog = window.approval_dialog
        assert dialog is not None and dialog.listen_button is not None
        window.voice.speech_enabled.setChecked(True)
        fixture.speech_ends.set()
        with qtbot.waitSignal(window.task_finished):
            QTest.mouseClick(dialog.listen_button, Qt.MouseButton.LeftButton)
        # What is said is the application's own description plus the word that is on screen.
        assert "разрешений" in fixture.spoken[0] and "Тестовое сообщение" in fixture.spoken[0]
        assert window.outbox.count == 1
    finally:
        window.shutdown()


def test_an_action_without_a_speakable_detail_keeps_only_its_button(
    qtbot: QtBot, tmp_path: Path
) -> None:
    fixture = VoiceFixture("разрешений")
    window = awaiting_approval(qtbot, tmp_path, fixture, subject="")
    try:
        dialog = window.approval_dialog
        assert dialog is not None
        assert dialog.detail is None and dialog.listen_button is None and dialog.voice is None
        assert fixture.captures == 0
        with qtbot.waitSignal(window.task_finished) as result:
            QTest.mouseClick(dialog.approve_button, Qt.MouseButton.LeftButton)
        assert result.args == ["finished"] and approvals(window) == ["user_ui"]
    finally:
        window.shutdown()


def test_an_expired_confirmation_cannot_be_spoken_into_a_token(
    qtbot: QtBot, tmp_path: Path
) -> None:
    clock = [0.0]
    store = ApprovalStore(clock=lambda: clock[0])
    audit = AuditLog(tmp_path / "audit.sqlite3")
    authority = store.take_authority(audit.approved)
    action = Action(
        uuid4(),
        "files.recycle",
        json.dumps({"path": "D:/Документы/смета.txt"}, ensure_ascii=False),
        Risk.CONFIRM,
        Mode.EXECUTE,
    )
    store.request(action)
    fixture = VoiceFixture("смета")
    panel = VoicePanel(recorder=fixture, recognizer=fixture, speaker=fixture)
    dialog = ApprovalDialog(action, authority, voice=panel)
    qtbot.addWidget(panel)
    qtbot.addWidget(dialog)
    dialog.show()
    try:
        assert dialog.detail is not None and dialog.listen_button is not None
        clock[0] = 61  # The window this snapshot was prepared in has passed.
        fixture.speech_ends.set()
        with qtbot.waitSignal(panel.confirmation) as heard:
            QTest.mouseClick(dialog.listen_button, Qt.MouseButton.LeftButton)
        # The word was the right one; the snapshot it belonged to is gone, so nothing issued.
        assert heard.args == [Confirmation.CONFIRMED]
        assert dialog.token is None and "истекло" in dialog.error_label.text()
        assert not [
            record for record in audit.recent() if json.loads(record)["event"] == "approved"
        ]
    finally:
        panel.shutdown()
        dialog.close()
        audit.close()
