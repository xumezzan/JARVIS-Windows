"""Single production entry point for typed tools, approval, cancellation and factual audit."""

import asyncio
from dataclasses import dataclass, field
from threading import Event, RLock
from time import monotonic
from uuid import UUID, uuid4

from jarvis.observability.audit import AuditEvent, AuditKind, AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalStore, ApprovalToken
from jarvis.permissions.policies import Decision, Mode, Risk, Status, decide
from jarvis.tools.base import ExecutionContext, canonical
from jarvis.tools.registry import ToolRegistry


@dataclass(frozen=True)
class Outcome:
    request_id: UUID
    status: Status
    error: ErrorCode = ErrorCode.NONE
    may_have_effects: bool = False
    result_json: str | None = field(default=None, repr=False)


@dataclass
class _Pending:
    action: Action
    cancelled: Event = field(default_factory=Event)
    running: bool = False
    created: float = field(default_factory=monotonic)


class PermissionEngine:
    def __init__(self, registry: ToolRegistry, approvals: ApprovalStore, audit: AuditLog) -> None:
        registry.seal()
        self._registry = registry
        self._approvals = approvals
        self._audit = audit
        self._pending: dict[UUID, _Pending] = {}
        self._cancelled: dict[UUID, tuple[str, Outcome]] = {}
        self._lock = RLock()

    def prepare(
        self, tool_name: str, arguments: object, mode: Mode = Mode.SIMULATION
    ) -> Action | Outcome:
        request_id = uuid4()
        try:
            tool = self._registry.get(tool_name)
            if tool is None or not isinstance(mode, Mode):
                raise ValueError("Invalid request.")
            payload = tool.normalize(arguments)
            if len(payload.encode()) > 65536:
                raise ValueError("Action exceeds snapshot limit.")
            action = Action(request_id, tool.name, payload, tool.risk, mode)
        except Exception:
            return self._invalid(request_id, mode if isinstance(mode, Mode) else Mode.SIMULATION)
        try:
            self._audit.write(
                AuditEvent(
                    AuditKind.PREPARED,
                    request_id,
                    action.tool,
                    action.risk,
                    action.mode,
                    decide(action.risk),
                )
            )
        except Exception:
            return Outcome(request_id, Status.ERROR, ErrorCode.AUDIT)
        if decide(action.risk) is Decision.DENY:
            return self._finish(action, Status.DENIED, ErrorCode.POLICY)
        with self._lock:
            for pending in list(self._pending.values()):
                if not pending.running and monotonic() - pending.created > 300:
                    self.cancel(pending.action)
            if len(self._pending) >= 256:
                return self._finish(action, Status.DENIED, ErrorCode.REPLAY)
            self._pending[request_id] = _Pending(action)
            if action.risk is Risk.CONFIRM:
                self._approvals.request(action)
        return action

    def _invalid(self, request_id: UUID, mode: Mode) -> Outcome:
        try:
            self._audit.write(
                AuditEvent(
                    AuditKind.FINISHED,
                    request_id,
                    "unknown",
                    None,
                    mode,
                    Decision.DENY,
                    Status.INVALID,
                    error=ErrorCode.INVALID_REQUEST,
                )
            )
        except Exception:
            return Outcome(request_id, Status.ERROR, ErrorCode.AUDIT)
        return Outcome(request_id, Status.INVALID, ErrorCode.INVALID_REQUEST)

    def _finish(
        self,
        action: Action,
        status: Status,
        error: ErrorCode = ErrorCode.NONE,
        started: float | None = None,
        may_have_effects: bool = False,
        result: str | None = None,
    ) -> Outcome:
        try:
            self._audit.write(
                AuditEvent(
                    AuditKind.FINISHED,
                    action.request_id,
                    action.tool,
                    action.risk,
                    action.mode,
                    Decision.DENY
                    if status in (Status.DENIED, Status.INVALID)
                    else Decision.ALLOW
                    if status in (Status.SUCCESS, Status.SIMULATED) or may_have_effects
                    else decide(action.risk),
                    status,
                    int((monotonic() - started) * 1000) if started is not None else 0,
                    error,
                    may_have_effects,
                )
            )
        except Exception:
            return Outcome(action.request_id, Status.ERROR, ErrorCode.AUDIT, may_have_effects)
        return Outcome(action.request_id, status, error, may_have_effects, result)

    def cancel(self, action: Action) -> None:
        with self._lock:
            pending = self._pending.get(action.request_id)
            if pending is None:
                return
            pending.cancelled.set()
            self._approvals.invalidate(pending.action)
            if not pending.running:
                self._pending.pop(action.request_id)
                outcome = self._finish(pending.action, Status.CANCELLED, ErrorCode.CANCELLED)
                self._cancelled[action.request_id] = (pending.action.signature, outcome)
                while len(self._cancelled) > 256:
                    self._cancelled.pop(next(iter(self._cancelled)))

    def cancel_all(self) -> None:
        with self._lock:
            for pending in list(self._pending.values()):
                self.cancel(pending.action)

    async def execute(self, action: Action, token: ApprovalToken | None = None) -> Outcome:
        started = monotonic()
        with self._lock:
            pending = self._pending.get(action.request_id)
            if pending is None:
                cancelled = self._cancelled.get(action.request_id)
                if cancelled is not None and cancelled[0] == action.signature:
                    self._cancelled.pop(action.request_id)
                    return cancelled[1]
                # Never log fields from an unrecognized/forged action.
                return self._invalid(uuid4(), Mode.SIMULATION)
            if pending.action != action:
                self.cancel(pending.action)
                return self._finish(pending.action, Status.DENIED, ErrorCode.APPROVAL)
            if pending.running:
                return self._finish(action, Status.DENIED, ErrorCode.REPLAY)
            pending.running = True
        tool = self._registry.get(action.tool)
        assert tool is not None  # Sealed registry and engine-owned snapshot.
        may_have_effects = False
        context = ExecutionContext(pending.cancelled)

        async def perform() -> Outcome:
            nonlocal may_have_effects
            await context.checkpoint()
            if decide(action.risk) is Decision.DENY:
                return self._finish(action, Status.DENIED, ErrorCode.POLICY, started)
            if action.risk is Risk.CONFIRM and not self._approvals.is_valid(token, action):
                return self._finish(action, Status.DENIED, ErrorCode.APPROVAL, started)
            if action.mode is Mode.EXECUTE and not await tool.check(action.payload, context):
                return self._finish(action, Status.DENIED, ErrorCode.PRECONDITION, started)
            await context.checkpoint()
            if action.risk is Risk.CONFIRM and not self._approvals.consume(token, action):
                return self._finish(action, Status.DENIED, ErrorCode.APPROVAL, started)
            if action.mode is Mode.SIMULATION:
                return self._finish(action, Status.SIMULATED, started=started)
            # Mandatory durable record before calling the adapter. Fail closed on audit error.
            try:
                self._audit.write(
                    AuditEvent(
                        AuditKind.STARTED,
                        action.request_id,
                        action.tool,
                        action.risk,
                        action.mode,
                        Decision.ALLOW,
                    )
                )
            except Exception:
                return self._finish(action, Status.ERROR, ErrorCode.AUDIT, started)
            await context.checkpoint()
            may_have_effects = True
            result = await tool.run(action.payload, context)
            await context.checkpoint()
            if not await tool.verify(action.payload, result, context):
                return self._finish(
                    action,
                    Status.ERROR,
                    ErrorCode.VERIFICATION,
                    started,
                    may_have_effects=True,
                )
            await context.checkpoint()
            return self._finish(
                action,
                Status.SUCCESS,
                started=started,
                may_have_effects=True,
                result=canonical(result),
            )

        async def cancellation() -> None:
            while not pending.cancelled.is_set():
                await asyncio.sleep(0.01)

        job = asyncio.create_task(perform())
        watcher = asyncio.create_task(cancellation())
        try:
            done, _ = await asyncio.wait(
                (job, watcher),
                timeout=tool.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Completed results reflect fact, even if stop arrives after verification/commit.
            if job in done:
                return await job
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            if watcher in done:
                return self._finish(
                    action,
                    Status.CANCELLED,
                    ErrorCode.CANCELLED,
                    started,
                    may_have_effects,
                )
            pending.cancelled.set()
            return self._finish(
                action, Status.TIMEOUT, ErrorCode.TIMEOUT, started, may_have_effects
            )
        except asyncio.CancelledError:
            pending.cancelled.set()
            return self._finish(
                action, Status.CANCELLED, ErrorCode.CANCELLED, started, may_have_effects
            )
        except Exception:
            return self._finish(
                action, Status.ERROR, ErrorCode.EXECUTION, started, may_have_effects
            )
        finally:
            job.cancel()
            watcher.cancel()
            await asyncio.gather(job, watcher, return_exceptions=True)
            with self._lock:
                self._pending.pop(action.request_id, None)
                self._approvals.invalidate(action)
