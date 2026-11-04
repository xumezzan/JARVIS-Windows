"""UI requests retain acknowledged settings, exact notices and cancellation boundaries."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from tests.integration.test_desktop_service import request, until
from tests.mail_support import FakeCredentials, FakeGraph
from tests.voice_support import VoiceFixture

from jarvis.config import AppConfig, load_config
from jarvis.core.planner.contracts import PlannerInput, Proposal
from jarvis.desktop.protocol import Request
from jarvis.desktop.service import DesktopService
from jarvis.desktop.settings import UiSettingsStore
from jarvis.desktop.voice import DesktopVoice
from jarvis.mail.models import Message
from jarvis.workspace.store import WorkspaceFailure


@pytest.mark.asyncio
async def test_partial_settings_preserve_provider_model_and_restore_appearance(
    tmp_path: Path,
) -> None:
    service = DesktopService(AppConfig(tmp_path), lambda event: None)
    try:
        await service.handle(request("configure", settings={"provider": "openai"}))
        await service.handle(request("configure", settings={"model": "fixture-model"}))
        await service.handle(request("configure", settings={"theme": "Graphite"}))
        await service.handle(request("configure", settings={"motion_enabled": False}))
        await service.handle(request("configure", settings={"task_memory_enabled": False}))
        assert service.settings.provider == "openai"
        assert service.settings.model == "fixture-model"
        stored = UiSettingsStore(tmp_path).load()
        assert stored.provider == "openai" and stored.planner_model == "fixture-model"
        assert stored.theme == "Graphite" and not stored.motion_enabled
    finally:
        await service.shutdown()
    restored = DesktopService(load_config({"JARVIS_DATA_DIR": str(tmp_path)}), lambda event: None)
    try:
        assert restored.settings.provider == "openai"
        assert restored.settings.model == "fixture-model"
        assert restored.settings.theme == "Graphite" and not restored.settings.motion_enabled
        assert not restored.settings.task_memory_enabled
    finally:
        await restored.shutdown()


@pytest.mark.asyncio
async def test_failed_settings_write_does_not_change_acknowledged_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[dict[str, object]] = []
    service = DesktopService(AppConfig(tmp_path), events.append)
    try:
        await service.handle(request("configure", settings={"model": "fixture-model"}))
        before = service.ui_store.path.read_bytes()

        def fail(*args: object) -> None:
            raise WorkspaceFailure("storage")

        monkeypatch.setattr(service.ui_store, "save", fail)
        await service.handle(request("configure", settings={"provider": "openai"}))
        assert events[-2]["error"] == "workspace_storage"
        assert service.snapshot()["provider"] == "offline"
        assert service.ui_store.path.read_bytes() == before
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_saved_openai_selection_reaches_provider_and_never_offline_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    class Provider:
        def __init__(self, model: str) -> None:
            self.model = model

        async def propose(self, data: PlannerInput) -> Proposal:
            calls.append((self.model, data.command))
            return Proposal(kind="finish")

    monkeypatch.setattr("jarvis.desktop.service.OpenAIProvider", Provider)
    service = DesktopService(AppConfig(tmp_path), lambda event: None)
    try:
        await service.handle(
            request("configure", settings={"provider": "openai", "model": "fixture-model"})
        )
    finally:
        await service.shutdown()
    restored = DesktopService(load_config({"JARVIS_DATA_DIR": str(tmp_path)}), lambda event: None)
    try:
        await restored.handle(request("run", command="unfamiliar fixture", cloud_consent=True))
        await until(lambda: restored.worker is None)
        assert calls == [("fixture-model", "unfamiliar fixture")]
        assert restored.prompt is None  # OfflineProvider would ask its recipe question.
    finally:
        await restored.shutdown()


@pytest.mark.asyncio
async def test_openai_without_model_returns_specific_error_without_offline_run(
    tmp_path: Path,
) -> None:
    events: list[dict[str, object]] = []
    service = DesktopService(AppConfig(tmp_path), events.append)
    try:
        await service.handle(request("configure", settings={"provider": "openai"}))
        await service.handle(request("run", command="unfamiliar fixture", cloud_consent=True))
        assert events[-2]["error"] == "planner_model_required"
        assert service.worker is None and service.prompt is None and service.started_count == 0
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_notice_dismissal_survives_publish_and_cannot_hide_a_new_notice(
    tmp_path: Path,
) -> None:
    events: list[dict[str, object]] = []
    service = DesktopService(AppConfig(tmp_path), events.append)
    try:
        service.notice = "First notice"
        await service.handle(request("dismiss_notice", notice_text="First notice"))
        service.publish()
        assert service.notice == "" and "First notice" not in json.dumps(events[-1])
        service.notice = "New notice"
        await service.handle(request("dismiss_notice", notice_text="First notice"))
        assert service.notice == "New notice" and events[-2]["error"] == "notice_changed"
    finally:
        await service.shutdown()


@pytest.mark.parametrize("fields", [{}, {"notice_text": ""}, {"notice_text": "x" * 4001}])
def test_notice_dismissal_requires_exact_bounded_text(fields: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Request.model_validate({"id": uuid4(), "operation": "dismiss_notice", **fields})
    with pytest.raises(ValueError):
        request("status", notice_text="Not a dismissal")


@pytest.mark.asyncio
async def test_execute_wake_resume_is_valid_jsonl_and_retains_mode(tmp_path: Path) -> None:
    service = DesktopService(AppConfig(tmp_path), lambda event: None)
    try:
        service.voice.wake_enabled = True
        service.voice.wake_suspended = True
        await service.handle(request("voice_wake_resume", mode="execute"))
        assert not service.voice.wake_suspended and service.voice.wake_mode.value == "execute"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("transcript", ["стоп", "подтвердить", "проверь систему"])
async def test_outlook_dialog_microphone_only_cancels_and_never_approves(
    tmp_path: Path, transcript: str
) -> None:
    events: list[dict[str, object]] = []
    service = DesktopService(AppConfig(tmp_path), events.append)
    fixture, graph = VoiceFixture(transcript), FakeGraph()
    service.voice = DesktopVoice(
        service.publish,
        service.transcript,
        service._voice_start,
        service.cancel,
        recorder=fixture,
        recognizer=fixture,
        speaker=fixture,
    )
    service.voice.auto_start = True
    service.mail_session.credentials = FakeCredentials()
    service.mail_session.graph = graph
    try:
        await service.handle(
            request(
                "mail",
                mail={
                    "operation": "connect",
                    "client_id": "00000000-0000-0000-0000-000000000001",
                    "consent": True,
                },
            )
        )
        await until(lambda: service.auxiliary is None)
        await service.handle(
            request(
                "mail",
                mode="execute",
                mail={
                    "operation": "send",
                    "message": Message(
                        to=["recipient@example.test"], subject="Fixture", body="Fixture"
                    ).model_dump(),
                },
            )
        )
        await until(lambda: service.prompt is not None)
        prompt = service.prompt
        await service.handle(request("voice_start", mode="execute"))
        await until(fixture.recording.is_set)
        await service.handle(request("voice_release"))
        await until(lambda: service.voice.task is None)
        assert fixture.captures == 1 and not graph.writes and service.started_count == 0
        assert not any(event.get("event") == "transcript" for event in events)
        if transcript == "стоп":
            await until(lambda: service.auxiliary is None)
            assert service.prompt is None
        else:
            assert service.prompt is prompt
    finally:
        await service.shutdown()
