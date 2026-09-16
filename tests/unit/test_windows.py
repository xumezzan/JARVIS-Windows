"""Permission-bound Windows contracts, native failure mapping and process lifecycle."""

import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import AsyncMock

import pytest
from tests.windows_support import WindowsProbe, target

from jarvis.observability.audit import AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.platforms.windows.transport import NativeReply, ProcessBackend
from jarvis.tools.base import ExecutionContext, ToolError
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import NativeRequest, WindowsResult, register_windows


@pytest.fixture
def audit(tmp_path: Path) -> Any:
    log = AuditLog(tmp_path / "audit.sqlite3")
    yield log
    log.close()


def engine_for(
    probe: WindowsProbe | ProcessBackend, audit: AuditLog
) -> tuple[PermissionEngine, ApprovalStore]:
    registry = ToolRegistry()
    register_windows(registry, probe)
    store = ApprovalStore()
    return PermissionEngine(registry, store, audit), store


def prepared(
    engine: PermissionEngine, name: str, args: object, mode: Mode = Mode.EXECUTE
) -> Action:
    action = engine.prepare("windows." + name, args, mode)
    assert isinstance(action, Action)
    return action


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,args",
    [
        ("get_open_windows", {}),
        ("open_app", {"app": "notepad"}),
        ("focus_app", {"target": target().model_dump()}),
        ("type_text", {"target": target().model_dump(), "text": "hello"}),
    ],
)
async def test_simulation_calls_no_native_hooks(name: str, args: object, audit: AuditLog) -> None:
    probe = WindowsProbe()
    engine, store = engine_for(probe, audit)
    action = prepared(engine, name, args, Mode.SIMULATION)
    token = store.take_authority(audit.approved).approve(action) if name == "type_text" else None
    assert (await engine.execute(action, token)).status is Status.SIMULATED
    assert not probe.calls


@pytest.mark.asyncio
async def test_type_requires_exact_approval_and_verifies_readback(audit: AuditLog) -> None:
    probe = WindowsProbe()
    engine, store = engine_for(probe, audit)
    authority = store.take_authority(audit.approved)
    args = {"target": target().model_dump(), "text": "Жарвис {ENTER}\nliteral"}
    action = prepared(engine, "type_text", args)
    assert (await engine.execute(action)).status is Status.DENIED
    assert not probe.calls
    action = prepared(engine, "type_text", args)
    token = authority.approve(action)
    assert (await engine.execute(action, token)).status is Status.SUCCESS
    assert probe.text == args["text"]
    assert [item.operation for item in probe.calls] == ["check_target", "type", "verify"]
    assert (await engine.execute(action, token)).status is Status.INVALID
    assert "literal" not in "".join(audit.recent())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("handle", 999),
        ("pid", 888),
        ("process_started", 1235.0),
        ("title", "Another tab"),
        ("runtime_id", [9, 9]),
        ("executable", "C:\\fake\\notepad.exe"),
        ("editor", target().model_dump()["editor"] | {"selected_tabs": [[9]]}),
    ],
)
async def test_changed_approved_target_never_runs(
    field: str, value: object, audit: AuditLog
) -> None:
    import json

    probe = WindowsProbe()
    engine, store = engine_for(probe, audit)
    action = prepared(engine, "type_text", {"target": target().model_dump(), "text": "test"})
    token = store.take_authority(audit.approved).approve(action)
    args = json.loads(action.payload)
    args["target"][field] = value
    changed = replace(action, payload=json.dumps(args))
    assert (await engine.execute(changed, token)).status is Status.DENIED
    assert not probe.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong,changed", [(True, False), (False, True)])
async def test_readback_failure_or_stale_target_never_succeeds(
    wrong: bool,
    changed: bool,
    audit: AuditLog,
) -> None:
    probe = WindowsProbe(wrong_readback=wrong, changed=changed)
    engine, store = engine_for(probe, audit)
    action = prepared(engine, "type_text", {"target": target().model_dump(), "text": "test"})
    token = store.take_authority(audit.approved).approve(action)
    result = await engine.execute(action, token)
    assert result.status is Status.ERROR
    assert result.error is (ErrorCode.VERIFICATION if wrong else ErrorCode.TARGET_CHANGED)
    assert result.may_have_effects == wrong


@pytest.mark.parametrize(
    "name,args",
    [
        # A path, argument or shell fragment is not an application name.
        ("open_app", {"app": "C:/Windows/System32/cmd.exe"}),
        ("open_app", {"app": "notepad", "args": ["secret.txt"]}),
        ("type_text", {"target": target().model_dump() | {"app": "chrome"}, "text": "test"}),
        ("type_text", {"target": target().model_dump() | {"empty": False}, "text": "test"}),
        ("type_text", {"target": target().model_dump(), "text": "bad\x00"}),
        ("type_text", {"target": target().model_dump(), "text": "test", "press_enter": True}),
        ("focus_app", {"target": target().model_dump() | {"handle": "100"}}),
    ],
)
def test_invalid_inputs(name: str, args: object, audit: AuditLog) -> None:
    probe = WindowsProbe()
    engine, _ = engine_for(probe, audit)
    result = engine.prepare("windows." + name, args)
    assert isinstance(result, Outcome) and result.status is Status.INVALID
    assert not probe.calls


@pytest.mark.asyncio
async def test_missing_app_and_stop(audit: AuditLog) -> None:
    probe = WindowsProbe()
    engine, _ = engine_for(probe, audit)
    result = await engine.execute(prepared(engine, "open_app", {"app": "chrome"}))
    assert result.error is ErrorCode.APPLICATION_MISSING and not result.may_have_effects
    probe.delay = 60
    probe.exited = False
    action = prepared(engine, "open_app", {"app": "notepad"})
    running = asyncio.create_task(engine.execute(action))
    await asyncio.sleep(0.02)
    engine.cancel(action)
    assert (await asyncio.wait_for(running, 1)).status is Status.CANCELLED
    assert probe.exited


@pytest.mark.asyncio
async def test_unsupported_platform_is_explicit(
    audit: AuditLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("jarvis.platforms.windows.transport.is_windows", lambda: False)
    engine, _ = engine_for(ProcessBackend(), audit)
    result = await engine.execute(prepared(engine, "get_open_windows", {}))
    assert result.error is ErrorCode.UNSUPPORTED_PLATFORM


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_real_hung_helper_is_killed_and_reaped(
    cancel: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_launch = asyncio.create_subprocess_exec
    processes: list[asyncio.subprocess.Process] = []

    async def launch(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await real_launch(
            sys.executable, "-I", "-c", "import time; time.sleep(60)", **kwargs
        )
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    backend = ProcessBackend(0.15)
    task = asyncio.create_task(
        backend._exchange(NativeRequest(operation="list"), ExecutionContext(Event()))
    )
    while not processes:
        await asyncio.sleep(0.005)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(ToolError, match="native_timeout"):
            await task
    assert processes[0].returncode is not None


@pytest.mark.asyncio
async def test_cancel_during_process_creation_reaps_child(monkeypatch: pytest.MonkeyPatch) -> None:
    real_launch = asyncio.create_subprocess_exec
    created = asyncio.Event()
    processes: list[asyncio.subprocess.Process] = []

    async def launch(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await real_launch(
            sys.executable, "-I", "-c", "import time; time.sleep(60)", **kwargs
        )
        processes.append(process)
        created.set()
        await asyncio.sleep(0.05)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    task = asyncio.create_task(
        ProcessBackend()._exchange(NativeRequest(operation="list"), ExecutionContext(Event()))
    )
    await created.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None


@pytest.mark.asyncio
async def test_payload_not_in_process_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    process = AsyncMock()
    process.returncode = 0
    process.stdin.write = lambda data: None
    process.stdin.close = lambda: None
    process.stdout.readuntil.return_value = (
        NativeReply(result=WindowsResult()).model_dump_json().encode() + b"\n"
    )
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    await ProcessBackend()._exchange(
        NativeRequest(operation="type", text="PRIVATE_SENTINEL"), ExecutionContext(Event())
    )
    assert "PRIVATE_SENTINEL" not in str(launch.call_args)
    assert "jarvis.platforms.windows.worker" in launch.call_args.args


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["not-json\\n", "x" * 150000], ids=["not-json", "oversized"])
async def test_bad_or_oversized_protocol_is_rejected_and_reaped(
    output: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_launch = asyncio.create_subprocess_exec
    processes: list[asyncio.subprocess.Process] = []

    async def launch(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        literal = repr(output)
        if len(literal) > 4096:
            # Windows caps a command line at 32767 chars; let the child build it.
            assert output == output[0] * len(output)
            literal = f"{output[0]!r} * {len(output)}"
        script = (
            "import sys,time; sys.stdout.write(" + literal + "); sys.stdout.flush(); time.sleep(60)"
        )
        process = await real_launch(sys.executable, "-I", "-c", script, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    with pytest.raises(ToolError):
        await asyncio.wait_for(
            ProcessBackend(0.3)._exchange(
                NativeRequest(operation="list"), ExecutionContext(Event())
            ),
            2,
        )
    assert processes[0].returncode is not None
