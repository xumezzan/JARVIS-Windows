"""Adversarial tests for approval capabilities and the mandatory execution boundary."""

import asyncio
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.observability.audit import AuditEvent, AuditKind, AuditLog, ErrorCode
from jarvis.permissions.approvals import (
    Action,
    ApprovalAuthority,
    ApprovalStore,
    ApprovalToken,
    Channel,
)
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.local import local_registry
from jarvis.tools.registry import ToolRegistry


class Inputs(ToolModel):
    value: str = "data"


class Result(ToolModel):
    count: int


@dataclass
class Probe:
    checks: int = 0
    calls: int = 0
    verifies: int = 0
    delay: float = 0
    permitted: bool = True
    fail: bool = False
    verified: bool = True

    async def check(self, args: Inputs, context: ExecutionContext) -> bool:
        self.checks += 1
        await context.checkpoint()
        return self.permitted

    async def execute(self, args: Inputs, context: ExecutionContext) -> Result:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("PRIVATE_EXCEPTION_SENTINEL")
        return Result(count=self.calls)

    async def verify(self, args: Inputs, result: Result, context: ExecutionContext) -> bool:
        self.verifies += 1
        return self.verified and result.count == self.calls

    def spec(self, risk: Risk = Risk.SAFE, timeout: float = 1) -> ToolSpec[Inputs, Result]:
        return ToolSpec(
            "test.probe",
            "Local test counter.",
            risk,
            Inputs,
            Result,
            self.check,
            self.execute,
            self.verify,
            timeout_seconds=timeout,
        )


@dataclass
class Harness:
    engine: PermissionEngine
    store: ApprovalStore
    authority: ApprovalAuthority
    audit: AuditLog
    probe: Probe
    time: list[float]

    def prepare(self, mode: Mode = Mode.EXECUTE, arguments: object = None) -> Action:
        action = self.engine.prepare("test.probe", {} if arguments is None else arguments, mode)
        assert isinstance(action, Action)
        return action


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    audit = AuditLog(tmp_path / "audit.sqlite3")
    time = [10.0]
    store = ApprovalStore(clock=lambda: time[0])
    probe = Probe()
    registry = ToolRegistry()
    registry.register(probe.spec(Risk.CONFIRM))
    authority = store.take_authority(audit.approved)
    yield Harness(PermissionEngine(registry, store, audit), store, authority, audit, probe, time)
    audit.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("risk", list(Risk))
@pytest.mark.parametrize("mode", list(Mode))
async def test_risk_policy(tmp_path: Path, risk: Risk, mode: Mode) -> None:
    audit = AuditLog(tmp_path / "audit.sqlite3")
    probe = Probe()
    registry = ToolRegistry()
    registry.register(probe.spec(risk))
    store = ApprovalStore()
    engine = PermissionEngine(registry, store, audit)
    prepared = engine.prepare("test.probe", {}, mode)
    result = await engine.execute(prepared) if isinstance(prepared, Action) else prepared
    expected = Status.SIMULATED if mode is Mode.SIMULATION else Status.SUCCESS
    # Without a token only the two levels that need none run; everything else is denied.
    unattended = risk in (Risk.SAFE, Risk.ROUTINE)
    assert result.status is (expected if unattended else Status.DENIED)
    assert probe.calls == (1 if unattended and mode is Mode.EXECUTE else 0)
    assert probe.checks == probe.calls
    assert len(audit.recent()) >= 2
    audit.close()


@pytest.mark.asyncio
async def test_exact_approval_and_replay(harness: Harness) -> None:
    action = harness.prepare()
    token = harness.authority.approve(action)
    assert token.secret not in repr(token)
    assert (await harness.engine.execute(action, token)).status is Status.SUCCESS
    assert (await harness.engine.execute(action, token)).status is not Status.SUCCESS
    assert harness.probe.calls == 1
    assert harness.probe.verifies == 1
    assert token.secret not in "".join(harness.audit.recent())


@pytest.mark.asyncio
async def test_fabricated_token_is_not_consent(harness: Harness) -> None:
    result = await harness.engine.execute(harness.prepare(), ApprovalToken("not-user-issued"))
    assert result.status is Status.DENIED
    assert harness.probe.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["payload", "tool", "mode", "risk", "request"])
async def test_changed_snapshot_rejected(harness: Harness, change: str) -> None:
    action = harness.prepare()
    token = harness.authority.approve(action)
    variants = {
        "payload": replace(action, payload='{"value":"changed"}'),
        "tool": replace(action, tool="test.other"),
        "mode": replace(action, mode=Mode.SIMULATION),
        "risk": replace(action, risk=Risk.SAFE),
        "request": replace(action, request_id=uuid4()),
    }
    result = await harness.engine.execute(variants[change], token)
    assert result.status is not Status.SUCCESS
    assert harness.probe.calls == 0


@pytest.mark.asyncio
async def test_expired_approval(harness: Harness) -> None:
    action = harness.prepare()
    token = harness.authority.approve(action)
    harness.time[0] += 60
    assert (await harness.engine.execute(action, token)).status is Status.DENIED
    assert harness.probe.calls == 0


def test_expired_or_cancelled_request_cannot_issue(harness: Harness) -> None:
    action = harness.prepare()
    harness.time[0] += 60
    with pytest.raises(ValueError):
        harness.authority.approve(action)
    second = harness.prepare()
    harness.engine.cancel(second)
    with pytest.raises(ValueError):
        harness.authority.approve(second)


@pytest.mark.asyncio
async def test_cancel_invalidates_issued_approval(harness: Harness) -> None:
    action = harness.prepare()
    token = harness.authority.approve(action)
    harness.engine.cancel(action)
    assert not harness.store.is_valid(token, action)
    assert (await harness.engine.execute(action, token)).status is not Status.SUCCESS
    assert harness.probe.calls == 0


@pytest.mark.asyncio
async def test_concurrent_same_request_runs_once(harness: Harness) -> None:
    action = harness.prepare()
    token = harness.authority.approve(action)
    results = await asyncio.gather(
        harness.engine.execute(action, token),
        harness.engine.execute(action, token),
    )
    assert [result.status for result in results].count(Status.SUCCESS) == 1
    assert harness.probe.calls == 1


def test_concurrent_token_consumption_is_atomic(harness: Harness) -> None:
    action = harness.prepare()
    token = harness.authority.approve(action)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: harness.store.consume(token, action), range(20)))
    assert results.count(True) == 1


def test_changed_token_payload_cannot_be_changed_back(harness: Harness) -> None:
    action = harness.prepare()
    token = harness.authority.approve(action)
    assert not harness.store.consume(token, replace(action, payload='{"value":"other"}'))
    assert not harness.store.consume(token, action)


@pytest.mark.asyncio
async def test_simulation_checks_approval_but_calls_no_adapter(harness: Harness) -> None:
    unapproved = harness.prepare(Mode.SIMULATION)
    assert (await harness.engine.execute(unapproved)).status is Status.DENIED
    action = harness.prepare(Mode.SIMULATION)
    token = harness.authority.approve(action)
    result = await harness.engine.execute(action, token)
    assert result.status is Status.SIMULATED
    assert not result.may_have_effects
    assert (harness.probe.checks, harness.probe.calls, harness.probe.verifies) == (0, 0, 0)


@pytest.mark.asyncio
async def test_simulation_token_not_valid_for_execute(harness: Harness) -> None:
    simulation = harness.prepare(Mode.SIMULATION)
    token = harness.authority.approve(simulation)
    real = harness.prepare()
    assert (await harness.engine.execute(real, token)).status is Status.DENIED
    assert harness.probe.calls == 0


@pytest.mark.parametrize(
    "arguments", [{"value": 1}, {"approved": True}, {"value": "x", "risk": "SAFE"}]
)
def test_invalid_arguments_never_bypass_schema(harness: Harness, arguments: object) -> None:
    result = harness.engine.prepare("test.probe", arguments)
    assert isinstance(result, Outcome) and result.status is Status.INVALID
    assert harness.probe.calls == 0


def test_unknown_name_is_not_logged(harness: Harness) -> None:
    result = harness.engine.prepare("PRIVATE_NAME_SENTINEL", {})
    assert isinstance(result, Outcome) and result.status is Status.INVALID
    assert "PRIVATE_NAME_SENTINEL" not in "".join(harness.audit.recent())


@pytest.mark.asyncio
async def test_precondition_failure(harness: Harness) -> None:
    action = harness.prepare()
    token = harness.authority.approve(action)
    harness.probe.permitted = False
    result = await harness.engine.execute(action, token)
    assert result.error is ErrorCode.PRECONDITION
    assert harness.probe.calls == 0
    assert not harness.store.is_valid(token, action)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "verification"])
async def test_no_false_success_or_raw_exception(harness: Harness, failure: str) -> None:
    harness.probe.fail = failure == "exception"
    harness.probe.verified = failure != "verification"
    action = harness.prepare(arguments={"value": "PRIVATE_ARGUMENT_SENTINEL"})
    token = harness.authority.approve(action)
    result = await harness.engine.execute(action, token)
    assert result.status is Status.ERROR
    assert result.may_have_effects
    records = "".join(harness.audit.recent())
    assert "PRIVATE_EXCEPTION_SENTINEL" not in records
    assert "PRIVATE_ARGUMENT_SENTINEL" not in records


@pytest.mark.asyncio
async def test_running_cancel_has_honest_effect_flag(harness: Harness) -> None:
    harness.probe.delay = 1
    action = harness.prepare()
    job = asyncio.create_task(harness.engine.execute(action, harness.authority.approve(action)))
    while harness.probe.calls == 0:
        await asyncio.sleep(0)
    harness.engine.cancel(action)
    result = await job
    assert result.status is Status.CANCELLED
    assert result.may_have_effects


@pytest.mark.asyncio
async def test_timeout_is_bounded(tmp_path: Path) -> None:
    probe = Probe(delay=5)
    registry = ToolRegistry()
    registry.register(probe.spec(timeout=0.03))
    audit = AuditLog(tmp_path / "audit.sqlite3")
    engine = PermissionEngine(registry, ApprovalStore(), audit)
    action = engine.prepare("test.probe", {}, Mode.EXECUTE)
    assert isinstance(action, Action)
    result = await asyncio.wait_for(engine.execute(action), timeout=0.5)
    assert result.status is Status.TIMEOUT
    assert result.may_have_effects
    audit.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [AuditKind.STARTED, AuditKind.FINISHED])
async def test_audit_failure_never_reports_success(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    phase: AuditKind,
) -> None:
    action = harness.prepare()
    token = harness.authority.approve(action)
    original = harness.audit.write

    def fail(event: AuditEvent) -> None:
        if event.kind is phase:
            raise OSError("PRIVATE_AUDIT_EXCEPTION")
        original(event)

    monkeypatch.setattr(harness.audit, "write", fail)
    result = await harness.engine.execute(action, token)
    assert result.status is Status.ERROR and result.error is ErrorCode.AUDIT
    assert harness.probe.calls == (1 if phase is AuditKind.FINISHED else 0)
    assert result.may_have_effects is (phase is AuditKind.FINISHED)


def test_audit_failure_prevents_token(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    action = harness.prepare()

    def fail(event: AuditEvent) -> None:
        raise OSError("unavailable")

    monkeypatch.setattr(harness.audit, "write", fail)
    with pytest.raises(OSError):
        harness.authority.approve(action)
    assert harness.probe.calls == 0


@pytest.mark.asyncio
async def test_snapshot_copies_nested_attachments(tmp_path: Path) -> None:
    registry, outbox = local_registry()
    audit = AuditLog(tmp_path / "audit.sqlite3")
    store = ApprovalStore()
    authority = store.take_authority(audit.approved)
    engine = PermissionEngine(registry, store, audit)
    attachment = {"name": "example.txt", "sha256": "a" * 64}
    arguments = {"recipient": "alice", "body": "original", "attachments": [attachment]}
    action = engine.prepare("local.append_message", arguments, Mode.EXECUTE)
    assert isinstance(action, Action)
    token = authority.approve(action)
    attachment["sha256"] = "b" * 64
    arguments["body"] = "changed"
    result = await engine.execute(action, token)
    assert result.status is Status.SUCCESS and result.result_json is not None
    assert outbox.matches(json.loads(result.result_json)["receipt_id"], action.payload)
    assert json.loads(action.payload)["attachments"][0]["sha256"] == "a" * 64
    audit.close()


def test_registry_discovery_duplicates_and_sealing(tmp_path: Path) -> None:
    registry = ToolRegistry()
    probe = Probe()
    registry.register(probe.spec())
    with pytest.raises(ValueError):
        registry.register(probe.spec())
    schema = registry.discover()[0]
    assert schema["risk"] == "SAFE"
    assert "parameters" in schema and "result" in schema
    registry.seal()
    with pytest.raises(ValueError):
        registry.register(replace(probe.spec(), name="test.another"))


@pytest.mark.asyncio
# Ids are the member names: a parametrisation id of "voice" would read as the opt-in
# microphone marker and skip this test.
@pytest.mark.parametrize("channel", list(Channel), ids=[channel.name for channel in Channel])
async def test_the_channel_is_recorded_and_changes_nothing_else(
    harness: Harness, channel: Channel
) -> None:
    action = harness.prepare()
    token = harness.authority.approve(action, channel)
    approvals = [
        json.loads(record)
        for record in harness.audit.recent()
        if json.loads(record)["event"] == "approved"
    ]
    assert [record["actor"] for record in approvals] == ["user_" + channel.value]
    assert token.channel is channel
    result = await harness.engine.execute(action, token)
    # A spoken approval is the same capability: exact, expiring and used exactly once.
    assert result.status is Status.SUCCESS and harness.probe.calls == 1
    assert not harness.store.is_valid(token, action)
    assert (await harness.engine.execute(harness.prepare(), token)).status is Status.DENIED


def test_an_unknown_channel_issues_nothing(harness: Harness) -> None:
    action = harness.prepare()
    with pytest.raises(ValueError):
        harness.authority.approve(action, "voice")  # type: ignore[arg-type]
    assert not [
        record for record in harness.audit.recent() if json.loads(record)["event"] == "approved"
    ]


def test_issuer_is_not_a_registered_tool_and_cannot_be_reclaimed(harness: Harness) -> None:
    with pytest.raises(ValueError):
        harness.store.take_authority(harness.audit.approved)
    result = harness.engine.prepare("approval.issue", {"approved": True})
    assert isinstance(result, Outcome) and result.status is Status.INVALID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["account", "recipient", "subject", "body", "attachments", "action_type", "service"]
)
async def test_every_message_field_is_bound(tmp_path: Path, field: str) -> None:
    registry, outbox = local_registry()
    audit = AuditLog(tmp_path / "audit.sqlite3")
    store = ApprovalStore()
    authority = store.take_authority(audit.approved)
    engine = PermissionEngine(registry, store, audit)
    action = engine.prepare(
        "local.append_message", {"recipient": "alice", "body": "original"}, Mode.EXECUTE
    )
    assert isinstance(action, Action)
    token = authority.approve(action)
    payload = json.loads(action.payload)
    payload[field] = [{"name": "file", "sha256": "b" * 64}] if field == "attachments" else "changed"
    changed = replace(action, payload=json.dumps(payload, sort_keys=True, separators=(",", ":")))
    result = await engine.execute(changed, token)
    assert result.status is Status.DENIED
    assert outbox.count == 0
    assert not store.is_valid(token, action)
    audit.close()


@pytest.mark.asyncio
async def test_approval_expires_during_preconditions(tmp_path: Path) -> None:
    probe = Probe()
    time = [0.0]
    store = ApprovalStore(clock=lambda: time[0])
    audit = AuditLog(tmp_path / "audit.sqlite3")
    authority = store.take_authority(audit.approved)
    registry = ToolRegistry()

    async def check(args: Inputs, context: ExecutionContext) -> bool:
        time[0] = 60
        return True

    registry.register(replace(probe.spec(Risk.CONFIRM), preconditions=check))
    engine = PermissionEngine(registry, store, audit)
    action = engine.prepare("test.probe", {}, Mode.EXECUTE)
    assert isinstance(action, Action)
    result = await engine.execute(action, authority.approve(action))
    assert result.status is Status.DENIED and result.error is ErrorCode.APPROVAL
    assert probe.calls == 0
    audit.close()


@pytest.mark.asyncio
async def test_caller_cancellation_joins_adapter(harness: Harness) -> None:
    harness.probe.delay = 5
    action = harness.prepare()
    job = asyncio.create_task(harness.engine.execute(action, harness.authority.approve(action)))
    while harness.probe.calls == 0:
        await asyncio.sleep(0)
    job.cancel()
    result = await job
    assert result.status is Status.CANCELLED
    assert not harness.store.is_valid(None, action)
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]


def test_normalization_and_prepare_audit_failure(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = harness.prepare(arguments={})
    second = harness.prepare(arguments={"value": "data"})
    assert first.payload == second.payload

    def fail(event: AuditEvent) -> None:
        raise OSError("unavailable")

    monkeypatch.setattr(harness.audit, "write", fail)
    result = harness.engine.prepare("test.probe", {})
    assert isinstance(result, Outcome)
    assert result.error is ErrorCode.AUDIT
    assert harness.probe.calls == 0


@pytest.mark.asyncio
async def test_wrong_result_type_is_not_success(tmp_path: Path) -> None:
    class DishonestResult(Result):
        extra: str = "not-in-result-schema"

    probe = Probe()

    async def execute(args: Inputs, context: ExecutionContext) -> Result:
        return DishonestResult(count=1)

    registry = ToolRegistry()
    registry.register(replace(probe.spec(), execute=execute))
    audit = AuditLog(tmp_path / "audit.sqlite3")
    engine = PermissionEngine(registry, ApprovalStore(), audit)
    action = engine.prepare("test.probe", {}, Mode.EXECUTE)
    assert isinstance(action, Action)
    result = await engine.execute(action)
    assert result.status is Status.ERROR
    assert probe.verifies == 0
    audit.close()


@pytest.mark.asyncio
async def test_routine_runs_without_a_token_and_is_still_recorded(tmp_path: Path) -> None:
    """The everyday level skips approval only; it keeps every other guarantee."""
    audit = AuditLog(tmp_path / "audit.sqlite3")
    probe = Probe()
    registry = ToolRegistry()
    registry.register(probe.spec(Risk.ROUTINE))
    store = ApprovalStore()
    authority = store.take_authority(audit.approved)
    engine = PermissionEngine(registry, store, audit)

    action = engine.prepare("test.probe", {}, Mode.EXECUTE)
    assert isinstance(action, Action)
    # No token exists for it, and none can be minted: approval is not merely skipped.
    with pytest.raises(ValueError):
        authority.approve(action)
    assert (await engine.execute(action)).status is Status.SUCCESS
    assert probe.calls == 1

    # It is prepared and finished in the audit like any other action, under its own name.
    recorded = "".join(audit.recent())
    assert "ROUTINE" in recorded
    # One execution per prepared action still holds without a token to consume.
    assert (await engine.execute(action)).status is Status.INVALID
    assert probe.calls == 1
    audit.close()
