"""Single production entry point for typed tools, approval, cancellation and factual audit."""

import asyncio
import json
from dataclasses import dataclass, field
from threading import Event, RLock
from time import monotonic
from uuid import UUID, uuid4

from jarvis.observability.audit import AuditEvent, AuditKind, AuditLog, ErrorCode
from jarvis.observability.diagnostics import Reason, Stage, failure_message
from jarvis.permissions.approvals import Action, ApprovalStore, ApprovalToken
from jarvis.permissions.policies import Decision, Mode, Risk, Status, decide
from jarvis.tools.base import ExecutionContext, ToolError, canonical
from jarvis.tools.registry import ToolRegistry


@dataclass(frozen=True)
class Outcome:
    request_id: UUID
    status: Status
    error: ErrorCode = ErrorCode.NONE
    may_have_effects: bool = False
    result_json: str | None = field(default=None, repr=False)
    # Ephemeral verified image handoff; never part of textual results or audit.
    screenshot_b64: str | None = field(default=None, repr=False)
    stage: Stage | None = None
    reason: Reason | None = None

    @property
    def diagnostic(self) -> dict[str, str | bool | None] | None:
        if self.error is ErrorCode.NONE:
            return None
        return {
            "stage": self.stage.value if self.stage else None,
            "reason": self.reason.value if self.reason else None,
            "message": failure_message(self.error.value, self.reason, self.may_have_effects),
            "may_have_effects": self.may_have_effects,
        }


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
        tool = None
        try:
            tool = self._registry.get(tool_name)
            if tool is None or not isinstance(mode, Mode):
                raise ValueError("Invalid request.")
            payload = tool.normalize(arguments)
            if len(payload.encode()) > 65536:
                raise ValueError("Action exceeds snapshot limit.")
            action = Action(request_id, tool.name, payload, tool.classify_risk(payload), mode)
        except ToolError as error:
            return self._invalid(
                request_id,
                mode if isinstance(mode, Mode) else Mode.SIMULATION,
                tool=tool.name if tool is not None else "unknown",
                error=ErrorCode(error.code),
                reason=error.reason,
            )
        except Exception:
            return self._invalid(
                request_id,
                mode if isinstance(mode, Mode) else Mode.SIMULATION,
                tool=tool.name if tool is not None else "unknown",
            )
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
            return Outcome(request_id, Status.ERROR, ErrorCode.AUDIT, stage=Stage.AUDIT)
        if decide(action.risk) is Decision.DENY:
            return self._finish(action, Status.DENIED, ErrorCode.POLICY, stage=Stage.POLICY)
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

    def _invalid(
        self,
        request_id: UUID,
        mode: Mode,
        *,
        tool: str = "unknown",
        error: ErrorCode = ErrorCode.INVALID_REQUEST,
        stage: Stage = Stage.PREPARE,
        reason: Reason | None = Reason.INVALID_ARGUMENTS,
    ) -> Outcome:
        # Keep normalization's INVALID contract while preserving the policy's actual code.
        status = (
            Status.DENIED
            if error in (ErrorCode.NETWORK_DENIED, ErrorCode.UNOBSERVED_TARGET)
            else Status.INVALID
        )
        try:
            self._audit.write(
                AuditEvent(
                    AuditKind.FINISHED,
                    request_id,
                    tool,
                    None,
                    mode,
                    Decision.DENY,
                    status,
                    error=error,
                    stage=stage,
                    reason=reason,
                )
            )
        except Exception:
            return Outcome(request_id, Status.ERROR, ErrorCode.AUDIT, stage=Stage.AUDIT)
        return Outcome(request_id, status, error, stage=stage, reason=reason)

    def reject_proposal(self, tool_name: str, mode: Mode, reason: Reason) -> Outcome:
        """Record grounding failures before a complete action can be prepared."""
        tool = self._registry.get(tool_name)
        return self._invalid(
            uuid4(),
            mode,
            tool=tool.name if tool is not None else "unknown",
            error=ErrorCode.UNOBSERVED_TARGET,
            stage=Stage.OBSERVED_TARGET,
            reason=reason,
        )

    def reject(self, action: Action, error: ErrorCode, reason: Reason) -> Outcome:
        """Refuse an engine-owned unissued action, without mislabelling it cancellation."""
        with self._lock:
            pending = self._pending.get(action.request_id)
            if pending is None or pending.running or pending.action != action:
                return self._invalid(uuid4(), Mode.SIMULATION)
            self._pending.pop(action.request_id)
            self._approvals.invalidate(action)
            return self._finish(
                action,
                Status.DENIED,
                error,
                stage=Stage.PRECONDITIONS
                if error is ErrorCode.DUPLICATE_EFFECT
                else Stage.OBSERVED_TARGET,
                reason=reason,
            )

    def _finish(
        self,
        action: Action,
        status: Status,
        error: ErrorCode = ErrorCode.NONE,
        started: float | None = None,
        may_have_effects: bool = False,
        result: str | None = None,
        screenshot_b64: str | None = None,
        *,
        stage: Stage | None = None,
        reason: Reason | None = None,
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
                    stage=stage,
                    reason=reason,
                )
            )
        except Exception:
            return Outcome(
                action.request_id,
                Status.ERROR,
                ErrorCode.AUDIT,
                may_have_effects,
                stage=Stage.AUDIT,
            )
        return Outcome(
            action.request_id,
            status,
            error,
            may_have_effects,
            result,
            screenshot_b64,
            stage,
            reason,
        )

    def cancel(self, action: Action) -> None:
        with self._lock:
            pending = self._pending.get(action.request_id)
            if pending is None:
                return
            pending.cancelled.set()
            self._approvals.invalidate(pending.action)
            if not pending.running:
                self._pending.pop(action.request_id)
                outcome = self._finish(
                    pending.action,
                    Status.CANCELLED,
                    ErrorCode.CANCELLED,
                    stage=Stage.APPROVAL if action.risk is Risk.CONFIRM else Stage.PREPARE,
                )
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
        stage = Stage.POLICY
        context = ExecutionContext(pending.cancelled)

        async def perform() -> Outcome:
            nonlocal may_have_effects, stage
            await context.checkpoint()
            if decide(action.risk) is Decision.DENY:
                return self._finish(action, Status.DENIED, ErrorCode.POLICY, started, stage=stage)
            stage = Stage.APPROVAL
            if action.risk is Risk.CONFIRM and not self._approvals.is_valid(token, action):
                return self._finish(action, Status.DENIED, ErrorCode.APPROVAL, started, stage=stage)
            stage = Stage.PRECONDITIONS
            if action.mode is Mode.EXECUTE and not await tool.check(action.payload, context):
                return self._finish(
                    action, Status.DENIED, ErrorCode.PRECONDITION, started, stage=stage
                )
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
                return self._finish(
                    action, Status.ERROR, ErrorCode.AUDIT, started, stage=Stage.AUDIT
                )
            await context.checkpoint()
            may_have_effects = True
            stage = Stage.EXECUTE
            result = await tool.run(action.payload, context)
            await context.checkpoint()
            stage = Stage.VERIFY
            if not await tool.verify(action.payload, result, context):
                return self._finish(
                    action,
                    Status.ERROR,
                    ErrorCode.VERIFICATION,
                    started,
                    may_have_effects=True,
                    stage=stage,
                )
            await context.checkpoint()
            result_json = canonical(result)
            screenshot_b64 = None
            if action.tool == "windows.screenshot":
                metadata = json.loads(result_json)
                screenshot_b64 = metadata.pop("image_base64")
                result_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
            return self._finish(
                action,
                Status.SUCCESS,
                started=started,
                may_have_effects=True,
                result=result_json,
                screenshot_b64=screenshot_b64,
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
                    stage=stage,
                )
            pending.cancelled.set()
            return self._finish(
                action, Status.TIMEOUT, ErrorCode.TIMEOUT, started, may_have_effects, stage=stage
            )
        except asyncio.CancelledError:
            pending.cancelled.set()
            return self._finish(
                action,
                Status.CANCELLED,
                ErrorCode.CANCELLED,
                started,
                may_have_effects,
                stage=stage,
            )
        except ToolError as error:
            return self._finish(
                action,
                Status.TIMEOUT
                if error.code in ("native_timeout", "browser_timeout", "worker_timeout")
                else Status.ERROR,
                ErrorCode(error.code),
                started,
                may_have_effects,
                stage=stage,
                reason=error.reason,
            )
        except Exception:
            return self._finish(
                action, Status.ERROR, ErrorCode.EXECUTION, started, may_have_effects, stage=stage
            )
        finally:
            job.cancel()
            watcher.cancel()
            await asyncio.gather(job, watcher, return_exceptions=True)
            with self._lock:
                self._pending.pop(action.request_id, None)
                self._approvals.invalidate(action)
