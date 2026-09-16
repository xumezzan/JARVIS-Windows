"""Planner UI events, lifecycle and observations through real permission boundaries."""

import json
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot
from tests.browser_support import FixtureSite
from tests.unit.test_planner import MESSAGE, Scripted
from tests.windows_support import WindowsProbe, target

from jarvis.browser.host import BrowserHost
from jarvis.config import AppConfig
from jarvis.core.planner.contracts import PlannerInput, Proposal
from jarvis.core.planner.offline import call
from jarvis.security.browser_policy import NetworkPolicy
from jarvis.ui.planner_window import PlannerWindow


def click_approval(window: PlannerWindow, qtbot: QtBot) -> None:
    qtbot.waitUntil(lambda: window.approval_dialog is not None, timeout=15000)
    dialog = window.approval_dialog
    assert dialog is not None and dialog.token is None
    QTest.mouseClick(dialog.approve_button, Qt.MouseButton.LeftButton)


@pytest.mark.parametrize("simulation", [False, True])
def test_offline_multistep(qtbot: QtBot, tmp_path: Path, simulation: bool) -> None:
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    window.show()
    try:
        assert window.simulation.isChecked() and window.provider_choice.currentIndex() == 0
        window.simulation.setChecked(simulation)
        window.command.setPlainText("проверь систему дважды")
        with qtbot.waitSignal(window.task_finished) as result:
            QTest.mouseClick(window.run_button, Qt.MouseButton.LeftButton)
        assert result.args == ["simulated" if simulation else "finished"]
        assert window.output.toPlainText().count("local.check:") == 2
        assert window.host._thread is None
    finally:
        window.shutdown()


@pytest.mark.parametrize("approve", [True, False])
def test_exact_ui_approval_only(qtbot: QtBot, tmp_path: Path, approve: bool) -> None:
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
        dialog = window.approval_dialog
        assert dialog is not None and window.outbox.count == 0
        assert MESSAGE["body"] in dialog.preview.toPlainText()
        assert not window.command.isEnabled()
        with qtbot.waitSignal(window.task_finished) as result:
            if approve:
                QTest.mouseClick(dialog.approve_button, Qt.MouseButton.LeftButton)
            else:
                dialog.accept()  # A generic dialog acceptance has no approval authority.
        assert result.args == ["finished" if approve else "error"]
        assert window.outbox.count == int(approve)
    finally:
        window.shutdown()


@pytest.mark.parametrize("autonomous", [True, False])
def test_autonomous_mode_replaces_only_the_human_review(
    qtbot: QtBot, tmp_path: Path, autonomous: bool
) -> None:
    window = PlannerWindow(
        AppConfig(tmp_path), provider=Scripted([call("local.append_message", MESSAGE)])
    )
    qtbot.addWidget(window)
    window.show()
    try:
        if autonomous:
            with qtbot.waitSignal(window.task_finished) as result:
                assert window.run_command("test", execute=True, autonomous=True)
            assert result.args == ["finished"]
            # No dialog was shown, yet the outbox holds exactly the approved snapshot.
            assert window.approval_dialog is None and window.outbox.count == 1
            assert "Автономное подтверждение" in window.output.toPlainText()
        else:
            assert window.run_command("test", execute=True, autonomous=False)
            qtbot.waitUntil(lambda: window.approval_dialog is not None)
            assert window.outbox.count == 0
            with qtbot.waitSignal(window.task_finished):
                window.stop()
            assert window.outbox.count == 0
    finally:
        window.shutdown()


def test_clarification_resumes(qtbot: QtBot, tmp_path: Path) -> None:
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    window.show()
    try:
        window.command.setPlainText("сделай что-нибудь")
        window.start()
        qtbot.waitUntil(lambda: window.question_dialog is not None)
        assert window.answer_input is not None and window.answer_button is not None
        window.answer_input.setText("проверь систему дважды")
        with qtbot.waitSignal(window.task_finished) as result:
            QTest.mouseClick(window.answer_button, Qt.MouseButton.LeftButton)
        assert result.args == ["simulated"]
        assert window.output.toPlainText().count("local.check:") == 2
    finally:
        window.shutdown()


@pytest.mark.parametrize("phase", ["provider", "approval", "clarification"])
def test_stop_pending_work(qtbot: QtBot, tmp_path: Path, phase: str) -> None:
    provider = Scripted(
        [Proposal(kind="clarify", question="Какое приложение?")]
        if phase == "clarification"
        else [call("local.append_message", MESSAGE)],
        delay=10 if phase == "provider" else 0,
    )
    window = PlannerWindow(AppConfig(tmp_path), provider=provider)
    qtbot.addWidget(window)
    window.show()
    try:
        window.command.setPlainText("test")
        window.simulation.setChecked(False)
        window.start()
        if phase == "provider":
            qtbot.waitUntil(lambda: bool(provider.inputs))
        else:
            qtbot.waitUntil(
                lambda: window.approval_dialog is not None or window.question_dialog is not None
            )
        with qtbot.waitSignal(window.task_finished) as result:
            QTest.mouseClick(window.stop_button, Qt.MouseButton.LeftButton)
        assert result.args == ["cancelled"]
        assert window.worker is None and window.outbox.count == 0
        assert window.approval_dialog is None and window.question_dialog is None
    finally:
        window.shutdown()


@pytest.mark.parametrize("escape", [False, True])
def test_close_drains_worker_and_disposes_session(
    qtbot: QtBot, tmp_path: Path, escape: bool
) -> None:
    provider = Scripted([], delay=10)
    window = PlannerWindow(AppConfig(tmp_path), provider=provider)
    qtbot.addWidget(window)
    window.show()
    window.command.setPlainText("test")
    window.start()
    qtbot.waitUntil(lambda: bool(provider.inputs))
    with qtbot.waitSignal(window.finished):
        if escape:
            QTest.keyClick(window, Qt.Key.Key_Escape)
        else:
            window.close()
    assert window._closed and window.worker is None and not window.isVisible()


def test_cloud_requires_disclosure_and_model(qtbot: QtBot, tmp_path: Path) -> None:
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    try:
        window.command.setPlainText("test")
        window.provider_choice.setCurrentIndex(1)
        window.start()
        assert window.worker is None and "разрешить" in window.status.text()
        window.cloud_consent.setChecked(True)
        window.start()
        assert window.worker is None and "идентификатор" in window.status.text()
    finally:
        window.shutdown()


@pytest.mark.parametrize("text", ["«Точный текст»", "Jarvis test successful"])
def test_observed_notepad_then_literal_typing(qtbot: QtBot, tmp_path: Path, text: str) -> None:
    probe = WindowsProbe()
    window = PlannerWindow(AppConfig(tmp_path), windows_backend=probe)
    qtbot.addWidget(window)
    window.show()
    try:
        window.simulation.setChecked(False)
        window.command.setPlainText("открой блокнот и напиши " + text)
        window.start()
        qtbot.waitUntil(lambda: window.approval_dialog is not None)
        dialog = window.approval_dialog
        assert dialog is not None and probe.text == ""
        preview = dialog.preview.toPlainText()
        assert "selected_tabs" in preview and text.strip("«»") in preview
        with qtbot.waitSignal(window.task_finished) as result:
            click_approval(window, qtbot)
        assert result.args == ["finished"] and probe.text == text.strip("«»")
        assert "windows.type_text: SUCCESS" in window.output.toPlainText()
    finally:
        window.shutdown()


@pytest.mark.parametrize("simulation", [False, True])
def test_invented_target_is_rejected_without_backend_hooks(
    qtbot: QtBot, tmp_path: Path, simulation: bool
) -> None:
    probe = WindowsProbe()
    window = PlannerWindow(
        AppConfig(tmp_path),
        windows_backend=probe,
        provider=Scripted(
            [call("windows.type_text", {"target": target().model_dump(), "text": "x"})]
        ),
    )
    qtbot.addWidget(window)
    try:
        window.simulation.setChecked(simulation)
        window.command.setPlainText("test")
        with qtbot.waitSignal(window.task_finished) as result:
            window.start()
        assert result.args == ["error"] and "unobserved_target" in window.status.text()
        assert not probe.calls and window.approval_dialog is None
    finally:
        window.shutdown()


def test_real_browser_observation_drives_next_registered_step(qtbot: QtBot, tmp_path: Path) -> None:
    site = FixtureSite()
    host = BrowserHost(NetworkPolicy(fixture_origin=site.origin))

    class PageProvider:
        async def propose(self, data: PlannerInput) -> Proposal:
            if not data.steps:
                return call("browser.open", {"url": site.origin + "/"})
            if len(data.steps) == 1:
                page = json.loads(data.steps[-1].outcome.result_json or "{}")["page"]
                return call("browser.read", {"target": page["target"]})
            return Proposal(kind="finish")

    window = PlannerWindow(AppConfig(tmp_path), provider=PageProvider(), browser_host=host)
    qtbot.addWidget(window)
    window.show()
    try:
        window.simulation.setChecked(False)
        window.command.setPlainText("Read the controlled local page")
        with qtbot.waitSignal(window.task_finished, timeout=15000) as result:
            window.start()
            click_approval(window, qtbot)
        assert result.args == ["finished"]
        assert "browser.read: SUCCESS" in window.output.toPlainText()
        assert "Browser fixture" in window.output.toPlainText()
        assert [row[:2] for row in site.seen] == [("GET", "/")]
    finally:
        window.shutdown()
        site.close()
    assert host._thread is not None and not host._thread.is_alive()
