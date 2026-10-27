"""Headless JSONL, exact prompts, real local engine and deterministic voice checks."""

import asyncio
import base64
import io
import json
import os
import platform
import sys
from collections.abc import Callable
from pathlib import Path
from threading import Thread
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from tests.unit.test_planner import Scripted
from tests.voice_support import VoiceFixture

from jarvis.config import AppConfig
from jarvis.core.planner.contracts import Proposal, Step
from jarvis.core.planner.offline import call
from jarvis.core.planner.openai_provider import OpenAIProvider
from jarvis.desktop.protocol import MAX_REQUEST, MAX_RESPONSE, Request
from jarvis.desktop.service import DesktopService, read_pipe
from jarvis.desktop.voice import DesktopVoice
from jarvis.observability.audit import ErrorCode
from jarvis.observability.diagnostics import Reason, Stage
from jarvis.permissions.engine import Outcome
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.platforms.windows.transport import ProcessBackend
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.windows import MAX_SCREENSHOT_JPEG_BYTES, NativeRequest, WindowsResult


def request(operation: str, **fields: object) -> Request:
    return Request.model_validate_json(
        json.dumps({"id": str(uuid4()), "operation": operation, **fields})
    )


async def until(predicate: Callable[[], bool], *, seconds: float = 5) -> None:
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_desktop_starts_empty_without_account_or_teaching_tools(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []
    service = DesktopService(AppConfig(tmp_path), events.append)
    try:
        state = service.snapshot()
        assert state["history"] == []
        assert service.task.state == "idle" and service.task.steps == []
        assert not state["outlook_connected"] and state["voice_phase"] == "idle"
        names = {item["name"] for item in service.registry.discover()}
        assert "system.info" in names and "memory.scratchpad_write" in names
        assert not any(str(name).startswith("local.") for name in names)
        assert not hasattr(service, "window")
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_flight_recorder_receives_denials_without_sensitive_payloads(tmp_path: Path) -> None:
    service = DesktopService(AppConfig(tmp_path), lambda value: None)
    try:
        outcome = Outcome(
            uuid4(),
            Status.DENIED,
            ErrorCode.UNOBSERVED_TARGET,
            stage=Stage.OBSERVED_TARGET,
            reason=Reason.TARGET_NOT_OBSERVED,
        )
        step = Step(
            "windows.type_text",
            outcome,
            json.dumps({"target": {"app": "notepad"}, "text": "PRIVATE_TYPED_TEXT"}),
        )
        service._event("tool", step)
        record = service.task.steps[0]
        assert record["request_id"] == str(outcome.request_id)
        assert record["failure"] == outcome.diagnostic
        assert "notepad" in str(record["attempt"])
        assert "PRIVATE_TYPED_TEXT" not in json.dumps(record)
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_thirty_screenshots_keep_only_current_image_outside_history_and_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    images: list[str] = []
    request_sizes: list[int] = []
    events: list[dict[str, object]] = []

    async def capture(
        self: ProcessBackend, request: NativeRequest, context: ExecutionContext
    ) -> WindowsResult:
        assert request.operation == "screenshot"
        images.append(base64.b64encode(bytes([len(images)]) * MAX_SCREENSHOT_JPEG_BYTES).decode())
        return WindowsResult(image_base64=images[-1], image_format="JPEG", width=1280, height=800)

    async def transport(payload: dict[str, Any]) -> bytes:
        request_sizes.append(len(json.dumps(payload, ensure_ascii=False).encode()))
        content = payload["input"][0]["content"]
        if images:
            assert len(content) == 2
            assert content[1]["image_url"] == "data:image/jpeg;base64," + images[-1]
            text = content[0]["text"]
        else:
            assert isinstance(content, str)
            text = content
        observations = json.loads(text)["untrusted_observations"]
        assert len(observations) == len(images)
        assert all("image_base64" not in item["untrusted_result"] for item in observations)
        assert all(image not in text for image in images)
        output: dict[str, object]
        if len(images) < 30:
            output = {"type": "function_call", "name": "windows__screenshot", "arguments": "{}"}
        else:
            output = {
                "type": "message",
                "content": [{"type": "output_text", "text": '{"kind":"finish"}'}],
            }
        return json.dumps({"status": "completed", "output": [output]}).encode()

    monkeypatch.setattr(ProcessBackend, "call", capture)
    service = DesktopService(
        AppConfig(tmp_path), events.append, provider=OpenAIProvider("fixture", transport=transport)
    )
    try:
        await service.handle(request("run", command="capture", mode="execute"))
        runner = service.runner
        assert runner is not None
        # Wait for the real bounded run: thirty audited SQLite lifecycles on a shared
        # Windows disk are not a fifteen-second latency acceptance test. Every payload,
        # cleanup and persistence assertion below remains part of this regression.
        worker = service.worker
        assert worker is not None
        await asyncio.wait_for(asyncio.shield(worker), timeout=service.limits.total_seconds)
        assert service.worker is None
        assert service.task.state == "finished" and len(images) == 30
        assert len(request_sizes) == 31 and max(request_sizes) <= 262144
        assert len(runner.steps) == 30 and runner._screenshot_b64 is None
        assert all(step.outcome.screenshot_b64 is None for step in runner.steps)
        assert all(len((step.outcome.result_json or "").encode()) < 1000 for step in runner.steps)
        for event in events:
            line = (json.dumps(event, ensure_ascii=False) + "\n").encode()
            assert len(line) <= MAX_RESPONSE
            assert b"image_base64" not in line and b"screenshot_b64" not in line
        assert all(image not in "".join(service.audit.recent()) for image in images)
    finally:
        await service.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["execute", "simulation"])
async def test_desktop_runs_actual_engine_and_reports_observations(
    tmp_path: Path, mode: str
) -> None:
    service = DesktopService(AppConfig(tmp_path), lambda value: None)
    try:
        await service.handle(request("run", command="проверь систему", mode=mode))
        await until(lambda: service.worker is None)
        assert service.task.state == ("finished" if mode == "execute" else "simulated")
        assert len(service.task.steps) == 1
        step = service.task.steps[0]
        assert step["tool"] == "system.info"
        if mode == "execute":
            observed = json.loads(str(step["result"]))
            assert observed["system"] == platform.system()
            assert observed["architecture"] == platform.machine()
            assert observed["cpu_count"] >= 1
            assert observed["disk_total_bytes"] >= observed["disk_free_bytes"] > 0
            assert step["status"] == "SUCCESS"
        else:
            assert step["result"] == "" and step["status"] == "SIMULATED"
        assert len(service.history) == 1
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_cloud_consent_is_one_task_and_navigation_never_starts_audio(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []
    service = DesktopService(AppConfig(tmp_path), events.append, provider=Scripted([]))
    try:
        await service.handle(request("configure", settings={"provider": "openai", "model": "test"}))
        await service.handle(request("run", command="test", mode="execute"))
        assert events[-2]["error"] == "cloud_consent_required"
        assert service.worker is None
        await service.handle(request("run", command="test", cloud_consent=True))
        await until(lambda: service.worker is None)
        assert service.started_count == 1
        await service.handle(request("run", command="test"))
        assert service.started_count == 1
        await service.handle(request("open", panel="voice"))
        assert service.voice.task is None and service.voice.phase == "idle"
    finally:
        await service.shutdown()


@pytest.mark.parametrize(
    "fields",
    [
        {"operation": "approve"},
        {"operation": "run", "command": "x", "approval": True},
        {"operation": "open", "panel": "shell"},
        {"operation": "status", "command": "x"},
        {"operation": "cancel", "mode": "execute"},
        {"operation": "run", "command": "x" * 4001},
        {"operation": "respond", "reply": {"prompt_id": uuid4().hex, "token": "x"}},
        {"operation": "voice_start", "cloud_consent": True},
        {"operation": "status", "settings": {}},
    ],
)
def test_protocol_rejects_authority_and_extra_inputs(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Request.model_validate_json(json.dumps({"id": str(uuid4()), **fields}))


@pytest.mark.asyncio
async def test_pipe_invalid_or_oversized_closes_without_dispatch() -> None:
    for content in (b'{"operation":"approve"}\n', b"x" * (MAX_REQUEST + 1)):
        queue: asyncio.Queue[Request | None] = asyncio.Queue(maxsize=8)
        loop = asyncio.get_running_loop()
        thread = Thread(target=read_pipe, args=(io.BytesIO(content), loop, queue))
        thread.start()
        assert await asyncio.wait_for(queue.get(), 2) is None
        await asyncio.to_thread(thread.join)


class Empty(ToolModel):
    pass


def confirmed_service(
    tmp_path: Path, events: list[dict[str, object]]
) -> tuple[DesktopService, list[bool]]:
    service = DesktopService(
        AppConfig(tmp_path),
        events.append,
        provider=Scripted(
            [call("test.effect", {}), Proposal(kind="finish", spoken_summary="Готово.")]
        ),
    )
    # Test-only spec is registered before replacing the production engine's sealed registry.
    from jarvis.permissions.approvals import ApprovalStore
    from jarvis.permissions.engine import PermissionEngine
    from jarvis.tools.registry import ToolRegistry

    registry = ToolRegistry()
    effects: list[bool] = []

    async def check(args: Empty, context: ExecutionContext) -> bool:
        return True

    async def execute(args: Empty, context: ExecutionContext) -> Empty:
        effects.append(True)
        return Empty()

    async def verify(args: Empty, result: Empty, context: ExecutionContext) -> bool:
        return True

    registry.register(
        ToolSpec("test.effect", "Fixture", Risk.CONFIRM, Empty, Empty, check, execute, verify)
    )
    store = ApprovalStore()
    service.registry = registry
    service.engine = PermissionEngine(registry, store, service.audit)
    service._authority = store.take_authority(service.audit.approved)
    return service, effects


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ["approve", "deny", "cancel", "expired", "changed", "wrong_id", "audit_failed"]
)
async def test_exact_one_use_prompt_never_approves_stale_action(
    tmp_path: Path,
    scenario: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict[str, object]] = []
    service, effects = confirmed_service(tmp_path, events)
    try:
        service.start("test", Mode.EXECUTE)
        await until(lambda: service.prompt is not None)
        prompt = service.prompt
        assert prompt is not None and prompt.kind == "approval"
        wire = prompt.wire()
        assert not effects and "secret" not in json.dumps(wire)
        reply = {
            "prompt_id": prompt.id,
            "signature": wire["signature"],
            "approved": scenario != "deny",
        }
        if scenario == "cancel":
            service.cancel()
        else:
            if scenario == "expired":
                prompt.expires_at = 0
            if scenario == "changed":
                reply["signature"] = "a" * 64
            if scenario == "wrong_id":
                reply["prompt_id"] = uuid4().hex
            if scenario == "audit_failed":
                monkeypatch.setattr(
                    service.audit, "write", lambda event: (_ for _ in ()).throw(OSError())
                )
            await service.handle(request("respond", reply=reply))
        if scenario not in {"approve", "deny", "cancel"}:
            assert not effects
            service.cancel()
        await until(lambda: service.worker is None)
        assert len(effects) == (1 if scenario == "approve" else 0)
        await service.handle(request("respond", reply=reply))
        assert events[-2]["error"] == "prompt_expired"
        assert len(effects) == (1 if scenario == "approve" else 0)
        if scenario == "approve":
            assert service.last_speech == "Готово."
    finally:
        await service.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "confidence,enabled,expected", [(0.75, True, 1), (0.99, False, 0), (0.749, True, 0)]
)
async def test_voice_threshold_and_acknowledgement(
    tmp_path: Path,
    confidence: float,
    enabled: bool,
    expected: int,
) -> None:
    fixture = VoiceFixture("проверь систему", confidence=confidence)
    service = DesktopService(AppConfig(tmp_path), lambda value: None)
    service.voice = DesktopVoice(
        service.publish,
        service.transcript,
        service._voice_start,
        service.cancel,
        recorder=fixture,
        recognizer=fixture,
        speaker=fixture,
    )
    service.voice.auto_start = enabled
    try:
        await service.handle(request("voice_start", mode="execute"))
        await until(fixture.recording.is_set)
        await service.handle(request("voice_release"))
        await until(lambda: service.voice.task is None and service.worker is None)
        assert service.started_count == expected
        assert fixture.spoken == (["Принял, выполняю"] if expected else [])
        assert fixture.record_closed.is_set()
    finally:
        await service.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["recording", "transcribing", "speaking"])
async def test_voice_cancellation_drains_without_stale_transcript(
    tmp_path: Path, phase: str
) -> None:
    fixture = VoiceFixture()
    fixture.recognize_delay = 30 if phase == "transcribing" else 0
    fixture.speech_delay = 30
    transcripts: list[str] = []
    voice = DesktopVoice(
        lambda: None,
        transcripts.append,
        lambda text, mode: True,
        lambda: None,
        recorder=fixture,
        recognizer=fixture,
        speaker=fixture,
    )
    try:
        if phase == "speaking":
            voice.speak("Test")
            await until(fixture.speaking.is_set)
        else:
            voice.record(Mode.EXECUTE, False)
            await until(fixture.recording.is_set)
            if phase == "transcribing":
                voice.release()
                await until(fixture.recognizing.is_set)
        await voice.drain()
        assert voice.task is None and voice.phase == "idle" and not transcripts
    finally:
        await voice.shutdown()


@pytest.mark.asyncio
async def test_headless_subprocess_works_with_invalid_qt_plugin(tmp_path: Path) -> None:
    environment = dict(os.environ, JARVIS_DATA_DIR=str(tmp_path), QT_QPA_PLATFORM="invalid-plugin")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "jarvis.desktop.service",
        "--stdio",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )
    assert process.stdin is not None and process.stdout is not None
    async with asyncio.timeout(10):
        assert json.loads(await process.stdout.readline())["event"] == "state"
        process.stdin.write(
            request("run", command="проверь систему", mode="execute").model_dump_json().encode()
            + b"\n"
        )
        await process.stdin.drain()
        while True:
            message = json.loads(await process.stdout.readline())
            if message.get("event") == "state" and message["state"]["task"]["state"] == "finished":
                break
        process.stdin.close()
        await process.wait()
    assert process.returncode == 0


@pytest.mark.asyncio
async def test_memory_edits_preserve_store_and_explicit_selection(tmp_path: Path) -> None:
    service = DesktopService(AppConfig(tmp_path), lambda value: None, provider=Scripted([]))
    from time import time

    from jarvis.memory.models import PROFILE_TTL, Entry

    stamp = int(time())
    entry = Entry(
        kind="project", label="Проект", value="Jarvis", updated=stamp, expires=stamp + PROFILE_TTL
    )
    try:
        await service.handle(
            request("memory", memory={"operation": "save", "entry": entry.model_dump(mode="json")})
        )
        await until(lambda: service.auxiliary is None)
        assert len(service.entries) == 1
        provider = service.override_provider
        assert isinstance(provider, Scripted)
        await service.handle(request("run", command="test"))
        await until(lambda: service.worker is None)
        assert provider.inputs[-1].memory.empty
        await service.handle(request("run", command="test", memory_ids=[entry.id]))
        await until(lambda: service.worker is None)
        assert provider.inputs[-1].memory.profile[0].value == "Jarvis"
        await service.handle(
            request(
                "memory", memory={"operation": "delete", "previous": entry.model_dump(mode="json")}
            )
        )
        await until(lambda: service.auxiliary is None)
        assert service.entries == []
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_voice_cancel_before_coroutine_starts_clears_busy() -> None:
    fixture = VoiceFixture()
    voice = DesktopVoice(
        lambda: None,
        lambda text: None,
        lambda text, mode: True,
        lambda: None,
        recorder=fixture,
        recognizer=fixture,
        speaker=fixture,
    )
    try:
        voice.record(Mode.EXECUTE, False)
        await voice.drain()
        assert voice.task is None and fixture.captures == 0 and voice.phase == "idle"
        voice.speak("Test")
        await voice.drain()
        assert voice.task is None and not fixture.spoken and voice.phase == "idle"
    finally:
        await voice.shutdown()


@pytest.mark.asyncio
async def test_stop_while_draining_ack_never_restarts_result_speech(tmp_path: Path) -> None:
    service = DesktopService(AppConfig(tmp_path), lambda value: None)
    draining = asyncio.Event()
    release = asyncio.Event()
    fixture = VoiceFixture()
    service.voice.speaker = fixture
    service.voice.speech_enabled = True
    original = service.voice.drain

    async def blocked_drain() -> None:
        draining.set()
        await release.wait()
        await original()

    service.voice.drain = blocked_drain  # type: ignore[method-assign]
    try:
        service.start("проверь систему", Mode.EXECUTE)
        await draining.wait()
        service.cancel()
        release.set()
        await until(lambda: service.worker is None)
        assert not fixture.spoken and service.voice.task is None
    finally:
        release.set()
        await service.shutdown()


@pytest.mark.asyncio
async def test_headless_outlook_preserves_account_drafts_attachments_and_approvals(
    tmp_path: Path,
) -> None:
    import base64

    from tests.mail_support import FakeCredentials, FakeGraph

    from jarvis.mail.models import Attachment, Message

    service = DesktopService(AppConfig(tmp_path), lambda value: None)
    credentials, graph = FakeCredentials(), FakeGraph()
    service.mail_session.credentials = credentials
    service.mail_session.graph = graph

    async def mail(operation: str, **kwargs: object) -> None:
        await service.handle(
            request("mail", mode="execute", mail={"operation": operation, **kwargs})
        )
        await until(lambda: service.auxiliary is None)

    try:
        await mail("connect", client_id="00000000-0000-0000-0000-000000000001", consent=True)
        assert service.snapshot()["outlook_connected"]
        await mail("attach", name="fixture.txt", content=base64.b64encode(b"fixture").decode())
        assert len(service.attachments) == 1
        message = Message(
            to=["recipient@example.test"],
            subject="Fixture",
            body="Exact body",
            attachments=[Attachment.model_validate(item) for item in service.attachments],
        )
        await mail("local_draft", message=message.model_dump(mode="json"))
        assert len(service.mail_session.drafts()) == 1 and not graph.writes
        key = service.mail_session.drafts()[0][0]
        await mail("load_draft", draft_id=key)
        assert json.loads(str(service.mail_result["data"]))["body"] == "Exact body"
        await service.handle(
            request(
                "mail",
                mode="execute",
                mail={"operation": "send", "message": message.model_dump(mode="json")},
            )
        )
        await until(lambda: service.prompt is not None)
        prompt = service.prompt
        assert prompt is not None
        assert not graph.writes
        await service.handle(
            request(
                "respond",
                reply={
                    "prompt_id": prompt.id,
                    "signature": prompt.wire()["signature"],
                    "approved": True,
                },
            )
        )
        await until(lambda: service.auxiliary is None)
        assert len(graph.writes) == 1 and service.mail_result["state"] == "accepted"
        await mail("disconnect")
        assert not service.snapshot()["outlook_connected"]
        assert not service.attachments
        assert not service.mail_session.drafts() and not service.mail_result
        assert "Exact body" not in "".join(service.audit.recent())
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_eleven_result_requires_consent_consumed_once_and_exact_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.voice.elevenlabs import ElevenLabsSpeaker

    service = DesktopService(AppConfig(tmp_path), lambda value: None)
    spoken: list[str] = []

    async def speak(speaker: ElevenLabsSpeaker, text: str) -> None:
        assert text == speaker.authorized_text and not speaker.used
        speaker.used = True
        spoken.append(text)

    monkeypatch.setattr(ElevenLabsSpeaker, "speak", speak)
    service.eleven = {"account": "a" * 64, "voices": [{"id": "fixture", "name": "Fixture"}]}
    service.last_speech = "Я проверил систему."

    async def select(consent: bool) -> None:
        await service.handle(
            request(
                "eleven", eleven={"operation": "select", "voice_id": "fixture", "consent": consent}
            )
        )
        await until(lambda: service.auxiliary is None)

    try:
        await select(False)
        assert not bool(service.cloud_voice)
        await select(True)
        assert bool(service.cloud_voice)
        await service.handle(request("speak_result"))
        await until(lambda: service.voice.task is None)
        assert spoken == ["Я проверил систему."] and service.cloud_voice is None
        await select(True)
        service.start("проверь систему", Mode.SIMULATION)
        await until(lambda: service.worker is None)
        assert not bool(service.cloud_voice)
    finally:
        await service.shutdown()
