"""Bounded installer exercises with synthetic data and disposable storage only.

These checks never create approval authority, read credentials, contact providers,
or inspect user memory. Native credential construction proves availability only,
not whether an account, credential, or operating-system store actually works.
"""

import asyncio
import json
import tempfile
from pathlib import Path

from keyring.backend import KeyringBackend

from jarvis.core.planner.contracts import Clarification, Limits
from jarvis.core.planner.offline import OfflineProvider
from jarvis.core.planner.runner import Runner
from jarvis.memory.models import PROFILE_TTL, Entry
from jarvis.memory.store import MemoryStore
from jarvis.observability.audit import AuditKind, AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalStore, ApprovalToken
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.tools.base import ExecutionContext, ToolSpec
from jarvis.tools.local import CheckInput, CheckResult, local_registry


def _require(condition: bool) -> None:
    if not condition:
        raise RuntimeError("offline_check_failed")


def _action(value: Action | Outcome) -> Action:
    if not isinstance(value, Action):
        raise RuntimeError("offline_check_failed")
    return value


def _verified(outcome: Outcome) -> bool:
    return (
        outcome.status is Status.SUCCESS
        and outcome.error is ErrorCode.NONE
        and json.loads(outcome.result_json or "null") == {"completed": True}
    )


async def _permissions(path: Path) -> None:
    registry, outbox = local_registry()
    hooks: list[str] = []
    policy_calls: list[str] = []

    async def preconditions(args: CheckInput, context: ExecutionContext) -> bool:
        hooks.append("preconditions")
        await context.checkpoint()
        return True

    async def execute(args: CheckInput, context: ExecutionContext) -> CheckResult:
        hooks.append("execute")
        await context.checkpoint()
        return CheckResult(completed=True)

    async def verify(args: CheckInput, result: CheckResult, context: ExecutionContext) -> bool:
        hooks.append("verify")
        await context.checkpoint()
        return result.completed

    def policy(args: CheckInput) -> None:
        policy_calls.append("policy")

    registry.register(
        ToolSpec(
            "diagnostic.check",
            "Disposable installer check without external effects.",
            Risk.SAFE,
            CheckInput,
            CheckResult,
            preconditions,
            execute,
            verify,
            timeout_seconds=1,
            policy=policy,
        )
    )
    audit = AuditLog(path)
    engine = PermissionEngine(registry, ApprovalStore(), audit)
    expected: dict[str, Status] = {}
    safe_id = ""
    before_close: list[str] = []
    try:
        safe = _action(engine.prepare("local.check", {"delay_ms": 0}, Mode.EXECUTE))
        safe_id = str(safe.request_id)
        _require(_verified(await engine.execute(safe)))
        expected[safe_id] = Status.SUCCESS

        simulation = _action(engine.prepare("diagnostic.check", {}, Mode.SIMULATION))
        simulated = await engine.execute(simulation)
        _require(simulated.status is Status.SIMULATED and not simulated.may_have_effects)
        _require(not hooks and policy_calls == ["policy"])
        expected[str(simulation.request_id)] = Status.SIMULATED

        confirmation = _action(
            engine.prepare(
                "local.append_message",
                {"recipient": "installer-fixture", "body": "Installer diagnostic message"},
                Mode.EXECUTE,
            )
        )
        denied = await engine.execute(confirmation)
        _require(
            denied.status is Status.DENIED
            and denied.error is ErrorCode.APPROVAL
            and not denied.may_have_effects
            and outbox.count == 0
        )
        expected[str(confirmation.request_id)] = Status.DENIED

        for name in ("local.critical_test", "local.blocked_test"):
            blocked = engine.prepare(name, {}, Mode.EXECUTE)
            _require(isinstance(blocked, Outcome))
            if isinstance(blocked, Outcome):
                _require(
                    blocked.status is Status.DENIED
                    and blocked.error is ErrorCode.POLICY
                    and not blocked.may_have_effects
                )
                expected[str(blocked.request_id)] = Status.DENIED

        cancelled = _action(engine.prepare("diagnostic.check", {}, Mode.EXECUTE))
        engine.cancel(cancelled)
        cancellation = await engine.execute(cancelled)
        _require(
            cancellation.status is Status.CANCELLED
            and cancellation.error is ErrorCode.CANCELLED
            and not cancellation.may_have_effects
            and not hooks
        )
        expected[str(cancelled.request_id)] = Status.CANCELLED
        before_close = audit.recent()
    finally:
        engine.cancel_all()
        audit.close()

    reopened = AuditLog(path)
    try:
        records = reopened.recent()
        _require(records == before_close)
        events = [json.loads(record) for record in records]
        finished = {
            event["request_id"]: event["status"]
            for event in events
            if event["event"] == AuditKind.FINISHED.value
        }
        _require(finished == {request_id: status.value for request_id, status in expected.items()})
        _require(all(event["arguments"] == "[REDACTED]" for event in events))
        _require(
            all(
                event["request_id"] == safe_id
                for event in events
                if event["event"] == AuditKind.STARTED.value
            )
        )
        safe_events = [
            event["event"] for event in reversed(events) if event["request_id"] == safe_id
        ]
        _require(
            safe_events
            == [AuditKind.PREPARED.value, AuditKind.STARTED.value, AuditKind.FINISHED.value]
        )
    finally:
        reopened.close()


def check_permissions() -> None:
    """Exercise actual permission decisions, simulation, cancellation and durable audit."""
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-permissions-check-") as directory:
            asyncio.run(_permissions(Path(directory) / "audit.sqlite3"))
    except Exception:
        raise RuntimeError("offline_permissions_failed") from None


async def _planner(path: Path) -> None:
    registry, outbox = local_registry()
    audit = AuditLog(path)
    engine = PermissionEngine(registry, ApprovalStore(), audit)

    async def approve(action: Action) -> ApprovalToken | None:
        raise RuntimeError("offline_check_failed")

    async def clarify(question: Clarification) -> str | None:
        raise RuntimeError("offline_check_failed")

    try:
        runner = Runner(
            registry,
            engine,
            OfflineProvider(),
            approve,
            clarify,
            limits=Limits(max_steps=2, max_questions=0, provider_seconds=1, total_seconds=5),
        )
        result = await runner.run("проверь систему дважды", Mode.EXECUTE)
        _require(result.status == "finished" and len(result.steps) == 2)
        _require(
            all(step.tool == "local.check" and _verified(step.outcome) for step in result.steps)
        )
        _require(outbox.count == 0 and runner.active is None)
    finally:
        engine.cancel_all()
        audit.close()


def check_planner() -> None:
    """Run the offline two-step recipe through the real Runner and PermissionEngine."""
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-planner-check-") as directory:
            asyncio.run(_planner(Path(directory) / "audit.sqlite3"))
    except Exception:
        raise RuntimeError("offline_planner_failed") from None


def check_memory() -> None:
    """Save, reopen, edit, delete and clear synthetic labels in a disposable database."""
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-check-") as directory:
            path = Path(directory) / "memory.sqlite3"
            now = 100000
            first = Entry(
                kind="project",
                label="Diagnostic project",
                value="Alpha",
                updated=now,
                expires=now + PROFILE_TTL,
            )
            store = MemoryStore(path, clock=lambda: now)
            _require(store.save(first) == (first,))
            reopened = MemoryStore(path, clock=lambda: now)
            _require(reopened.read() == (first,))
            edited = Entry.model_validate(first.model_dump() | {"value": "Beta"})
            _require(reopened.save(edited, first) == (edited,))
            _require(store.read() == (edited,))
            _require(reopened.delete(edited) == () and store.read() == ())
            _require(store.save(first) == (first,))
            _require(store.clear() == () and reopened.read() == ())
    except Exception:
        raise RuntimeError("offline_memory_failed") from None


def check_credentials_backend() -> None:
    """Construct the explicit native backend; never read, write or validate a credential."""
    try:
        from jarvis.platforms.credentials import native_store

        _require(isinstance(native_store(), KeyringBackend))
    except Exception:
        raise RuntimeError("credentials_backend_unavailable") from None
