"""Serial execution with exact UI approvals, provenance checks and no automatic retries."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from threading import Event

from jarvis.core.planner.contracts import (
    Limits,
    PlannerInput,
    PlanResult,
    Proposal,
    Provider,
    ProviderError,
    Step,
)
from jarvis.permissions.approvals import Action, ApprovalToken
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.tools.registry import ToolRegistry

Approve = Callable[[Action], Awaitable[ApprovalToken | None]]
Clarify = Callable[[str], Awaitable[str | None]]


class Runner:
    def __init__(
        self,
        registry: ToolRegistry,
        engine: PermissionEngine,
        provider: Provider,
        approve: Approve,
        clarify: Clarify,
        *,
        limits: Limits | None = None,
        notify: Callable[[str, object], None] = lambda kind, value: None,
    ) -> None:
        self.registry = registry
        self.engine = engine
        self.provider = provider
        self.approve = approve
        self.clarify = clarify
        self.limits = limits or Limits()
        self.notify = notify
        self.cancelled = Event()
        self.active: Action | None = None
        self.steps: list[Step] = []
        self._used = False

    def cancel(self) -> None:
        self.cancelled.set()
        if self.active is not None:
            self.engine.cancel(self.active)

    async def run(self, command: str, mode: Mode = Mode.SIMULATION) -> PlanResult:
        if self._used or not command.strip() or len(command) > 4000 or not isinstance(mode, Mode):
            return PlanResult("error", error="invalid_request")
        self._used = True
        job = asyncio.create_task(self._run(command, mode))

        async def watch() -> None:
            while not self.cancelled.is_set():
                await asyncio.sleep(0.01)

        watcher = asyncio.create_task(watch())
        try:
            done, _ = await asyncio.wait(
                (job, watcher),
                timeout=self.limits.total_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if job in done:
                return await job
            self.cancel()
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            return PlanResult("cancelled" if watcher in done else "timeout", tuple(self.steps))
        except asyncio.CancelledError:
            self.cancel()
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            return PlanResult("cancelled", tuple(self.steps))
        except TimeoutError:
            return PlanResult("timeout", tuple(self.steps))
        except ProviderError as error:
            return PlanResult("error", tuple(self.steps), error.code)
        except Exception:
            return PlanResult("error", tuple(self.steps), "planner_failed")
        finally:
            if self.active is not None:
                self.engine.cancel(self.active)
                self.active = None
            job.cancel()
            watcher.cancel()
            await asyncio.gather(job, watcher, return_exceptions=True)

    async def _run(self, command: str, mode: Mode) -> PlanResult:
        answers: list[str] = []
        catalog = json.dumps(self.registry.discover(), ensure_ascii=False)
        while True:
            if self.cancelled.is_set():
                raise asyncio.CancelledError
            self.notify("thinking", len(self.steps) + 1)
            async with asyncio.timeout(self.limits.provider_seconds):
                raw = await self.provider.propose(
                    PlannerInput(command, tuple(answers), tuple(self.steps), mode, catalog)
                )
            proposal = Proposal.model_validate(raw, strict=True)
            if self.cancelled.is_set():
                raise asyncio.CancelledError
            if proposal.kind == "finish":
                return PlanResult(
                    "no_action"
                    if not self.steps
                    else "simulated"
                    if mode is Mode.SIMULATION
                    else "finished",
                    tuple(self.steps),
                )
            if proposal.kind == "clarify":
                if len(answers) >= self.limits.max_questions:
                    return PlanResult("limit", tuple(self.steps))
                answer = await self.clarify(proposal.question)
                if answer is None:
                    return PlanResult("cancelled", tuple(self.steps))
                if not answer.strip() or len(answer) > 4000:
                    return PlanResult("error", tuple(self.steps), "invalid_answer")
                answers.append(answer)
                continue
            if len(self.steps) >= self.limits.max_steps:
                return PlanResult("limit", tuple(self.steps))
            args = json.loads(proposal.arguments)
            action = self.engine.prepare(proposal.tool, args, mode)
            if isinstance(action, Outcome):
                self.steps.append(
                    Step(
                        "invalid_request" if action.status is Status.INVALID else proposal.tool,
                        action,
                    )
                )
                return PlanResult("error", tuple(self.steps), action.error.value)
            self.active = action
            if not self._observed_target(action):
                self.engine.cancel(action)
                self.active = None
                return PlanResult("error", tuple(self.steps), "unobserved_target")
            token = await self.approve(action) if action.risk is Risk.CONFIRM else None
            if self.cancelled.is_set():
                raise asyncio.CancelledError
            self.notify("executing", action.tool)
            outcome = await self.engine.execute(action, token)
            self.active = None
            step = Step(action.tool, outcome)
            self.steps.append(step)
            self.notify("tool", step)
            if outcome.status not in (Status.SUCCESS, Status.SIMULATED):
                return PlanResult(
                    "cancelled"
                    if outcome.status is Status.CANCELLED
                    else "timeout"
                    if outcome.status is Status.TIMEOUT
                    else "error",
                    tuple(self.steps),
                    outcome.error.value,
                )

    def _observed_target(self, action: Action) -> bool:
        args = json.loads(action.payload)
        if "target" not in args or not action.tool.startswith(("browser.", "windows.")):
            return True
        for step in reversed(self.steps):
            if step.outcome.status is not Status.SUCCESS or not step.tool.startswith(
                action.tool.split(".")[0] + "."
            ):
                continue
            result = json.loads(step.outcome.result_json or "{}")
            if action.tool.startswith("windows."):
                if args["target"] in [result.get("target"), *result.get("windows", [])]:
                    return True
            else:
                page = result.get("page") or {}
                if args["target"] == page.get("target"):
                    return "element" not in args or args["element"] in page.get("elements", [])
                if "element" not in args and args["target"] in result.get("tabs", []):
                    return True
        return False
