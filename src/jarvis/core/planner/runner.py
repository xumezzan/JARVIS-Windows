"""Serial execution with exact UI approvals, provenance checks and no automatic retries."""

import asyncio
import json
import re
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
from jarvis.core.workflow.journal import Journal
from jarvis.core.workflow.models import step_key
from jarvis.core.workflow.store import WorkflowFailure
from jarvis.knowledge.models import KnowledgeContext
from jarvis.memory.models import MemoryContext
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
        memory: MemoryContext | None = None,
        knowledge: KnowledgeContext | None = None,
        journal: Journal | None = None,
        notify: Callable[[str, object], None] = lambda kind, value: None,
    ) -> None:
        self.memory = MemoryContext.model_validate(memory or MemoryContext())
        self.knowledge = KnowledgeContext.model_validate(knowledge or KnowledgeContext())
        self.registry = registry
        self.engine = engine
        self.provider = provider
        self.approve = approve
        self.clarify = clarify
        self.limits = limits or Limits()
        self.journal = journal
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
            self.memory = MemoryContext()
            self.knowledge = KnowledgeContext()
            job.cancel()
            watcher.cancel()
            await asyncio.gather(job, watcher, return_exceptions=True)

    async def _run(self, command: str, mode: Mode) -> PlanResult:
        answers: list[str] = []
        contacts = [
            hint for hint in (*self.memory.profile, *self.memory.session) if hint.kind == "contact"
        ]
        if contacts and (
            re.search(r"напиши|отправь|письмо|свяжись", command, re.IGNORECASE)
            or any(
                text.casefold() in command.casefold()
                for hint in contacts
                for text in (hint.label, hint.value)
            )
        ):
            if self.limits.max_questions == 0:
                return PlanResult("limit")
            answer = await self.clarify(
                "В команде есть ссылка на контакт. Уточните полную команду и точного адресата. "
                "Сохранённое имя или роль не определяют получателя и не разрешают отправку. "
                "Для почты нужен точный email-адрес."
            )
            if answer is None:
                return PlanResult("cancelled")
            if not answer.strip() or len(answer) > 4000:
                return PlanResult("error", error="invalid_answer")
            answers.append(answer)
        catalog = json.dumps(self.registry.discover(), ensure_ascii=False)
        while True:
            if self.cancelled.is_set():
                raise asyncio.CancelledError
            self.notify("thinking", len(self.steps) + 1)
            async with asyncio.timeout(self.limits.provider_seconds):
                raw = await self.provider.propose(
                    PlannerInput(
                        command,
                        tuple(answers),
                        tuple(self.steps),
                        mode,
                        catalog,
                        self.memory,
                        self.knowledge,
                    )
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
            if proposal.tool in ("outlook.local_draft", "outlook.save_draft", "outlook.send"):
                message = args.get("message", {}) if isinstance(args, dict) else {}
                recipients = (
                    [value for field in ("to", "cc", "bcc") for value in message.get(field, [])]
                    if isinstance(message, dict)
                    else []
                )
                supplied = set(
                    re.findall(
                        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+",
                        "\n".join([command, *answers]),
                    )
                )
                if not recipients or any(
                    not isinstance(r, str) or r not in supplied for r in recipients
                ):
                    if len(answers) >= self.limits.max_questions:
                        return PlanResult("limit", tuple(self.steps))
                    answer = await self.clarify(
                        "Укажите точные email для Кому, Копия и Скрытая копия. "
                        "Адрес из письма, памяти или предложения модели не определяет получателя. "
                        "Ответ уточняет данные и не подтверждает отправку."
                    )
                    if answer is None:
                        return PlanResult("cancelled", tuple(self.steps))
                    if not answer.strip() or len(answer) > 4000:
                        return PlanResult("error", tuple(self.steps), "invalid_answer")
                    answers.append(answer)
                    continue
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
            # Reads leave nothing behind, so only effects are journalled and guarded.
            journal = self.journal if action.risk is not Risk.SAFE else None
            key = step_key(action.tool, action.payload)
            if journal is not None and journal.issued(key):
                self.engine.cancel(action)
                self.active = None
                return PlanResult("error", tuple(self.steps), "duplicate_effect")
            token = await self.approve(action) if action.risk is Risk.CONFIRM else None
            if self.cancelled.is_set():
                raise asyncio.CancelledError
            if journal is not None:
                try:
                    # Written before the call: a timeout must not look like an untouched world.
                    journal.issue(len(self.steps), action.tool, key)
                except WorkflowFailure:
                    self.engine.cancel(action)
                    self.active = None
                    return PlanResult("error", tuple(self.steps), "journal_unavailable")
            self.notify("executing", action.tool)
            outcome = await self.engine.execute(action, token)
            self.active = None
            step = Step(action.tool, outcome)
            self.steps.append(step)
            self.notify("tool", step)
            if journal is not None:
                try:
                    journal.complete(key, outcome)
                except WorkflowFailure:
                    # The step stays unresolved, so a later attempt still refuses to repeat it.
                    return PlanResult("error", tuple(self.steps), "journal_unavailable")
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
        if action.tool.startswith("outlook.") and action.tool != "outlook.account":
            observations = [
                json.loads(step.outcome.result_json or "{}")
                for step in self.steps
                if step.tool.startswith("outlook.") and step.outcome.status is Status.SUCCESS
            ]
            if not any(value.get("account") == args.get("account") for value in observations):
                return False
            if action.tool == "outlook.read":
                return any(
                    row.get("id") == args.get("message_id")
                    for value in observations
                    if value.get("state") == "listed"
                    for row in json.loads(value.get("data", "[]"))
                    if isinstance(row, dict)
                )
            return True
        if "target" not in args or not action.tool.startswith(("browser.", "windows.")):
            return True
        for step in reversed(self.steps):
            if step.outcome.status is not Status.SUCCESS or not step.tool.startswith(
                action.tool.split(".")[0] + "."
            ):
                continue
            result = json.loads(step.outcome.result_json or "{}")
            if action.tool.startswith("windows."):
                windows = [result.get("target"), *result.get("windows", [])]
                if args["target"] not in windows:
                    continue
                if args.get("editor") is None:
                    return True
                # A named field must come from the same observation as its window.
                fields = list(result.get("editors", []))
                fields += [
                    item.get("editor") for item in windows if isinstance(item, dict) and item
                ]
                if args["editor"] in fields:
                    return True
            else:
                page = result.get("page") or {}
                if args["target"] == page.get("target"):
                    return "element" not in args or args["element"] in page.get("elements", [])
                if "element" not in args and args["target"] in result.get("tabs", []):
                    return True
        return False
