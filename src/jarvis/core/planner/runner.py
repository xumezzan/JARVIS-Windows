"""Serial execution with exact UI approvals, provenance checks and no automatic retries."""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import replace
from threading import Event
from uuid import uuid4

from pydantic import ValidationError

from jarvis.core.context.learning import Learner
from jarvis.core.planner.contracts import (
    Answer,
    Limits,
    PlannerInput,
    PlanResult,
    Proposal,
    Provider,
    ProviderError,
    Step,
)
from jarvis.core.workflow.journal import Journal
from jarvis.core.workflow.models import Phase, step_key
from jarvis.core.workflow.store import WorkflowFailure
from jarvis.knowledge.harvest import Harvester
from jarvis.knowledge.models import KnowledgeContext
from jarvis.memory.conversation import TaskMemoryContext
from jarvis.memory.derived import DerivedContext
from jarvis.memory.models import MemoryContext
from jarvis.observability.audit import ErrorCode
from jarvis.observability.diagnostics import Reason
from jarvis.permissions.approvals import Action, ApprovalToken
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.tools.browser import (
    BrowserResult,
    PageTarget,
    PageView,
)
from jarvis.tools.browser import (
    ClickElement as BrowserClick,
)
from jarvis.tools.browser import (
    TypeElement as BrowserType,
)
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.scratchpad import MAX_SCRATCHPAD_BYTES, MAX_SCRATCHPAD_KEYS, ScratchpadWrite
from jarvis.tools.windows import ClickElement, FocusApp, WindowsResult, WindowTarget, window_matches
from jarvis.workspace.models import WorkspaceProfile

MICROSOFT = ("outlook.", "calendar.")

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
        task_memory: TaskMemoryContext | None = None,
        knowledge: KnowledgeContext | None = None,
        derived: DerivedContext | None = None,
        workspace: WorkspaceProfile | None = None,
        journal: Journal | None = None,
        resumed: tuple[Step, ...] = (),
        harvester: Harvester | None = None,
        learner: Learner | None = None,
        notify: Callable[[str, object], None] = lambda kind, value: None,
    ) -> None:
        self.memory = MemoryContext.model_validate(memory or MemoryContext())
        self.task_memory = TaskMemoryContext.model_validate(task_memory or TaskMemoryContext())
        self.scratchpad: dict[str, str] = {}
        self._screenshot_b64: str | None = None
        self.knowledge = KnowledgeContext.model_validate(knowledge or KnowledgeContext())
        self.derived = DerivedContext.model_validate(derived or DerivedContext())
        self.workspace = WorkspaceProfile.model_validate(
            workspace or WorkspaceProfile()
        ).planner_context()
        # Native hints stay local and are resolved by the same installed-app catalog.
        # They cannot inject a path or arguments and never grant a new capability.
        self._native_hints = {
            label.casefold(): app.native_exe_hint
            for app in (workspace.apps if workspace is not None else ())
            if app.enabled and app.kind in ("native", "hybrid") and app.native_exe_hint
            for label in (app.id, app.name)
        }
        self.registry = registry
        self.engine = engine
        self.provider = provider
        self.approve = approve
        self.clarify = clarify
        self.limits = limits or Limits()
        self.journal = journal
        self.harvester = harvester
        self.learner = learner
        # One identity for the run, shared by the journal and by what it observes.
        self.run_id = journal.run_id if journal is not None else uuid4().hex
        self.notify = notify
        self.cancelled = Event()
        self.active: Action | None = None
        # A resumed run starts already holding what the earlier process did. Those steps
        # count against the ceiling and are shown in the report, and they carry no results,
        # so nothing they touched is addressable until this task observes it again.
        self.steps: list[Step] = list(resumed)
        self.calls = 0
        self._used = False
        self._uncertain_effects: set[str] = set()

    def _record(self, step: Step) -> None:
        self._screenshot_b64 = None
        self.steps.append(step)
        self.notify("tool", step)

    async def _refresh_browser(self, action: Action) -> None:
        """Recover observations, never replay an action or reuse its approval."""
        if not action.tool.startswith("browser.") or action.mode is not Mode.EXECUTE:
            return
        target = json.loads(action.payload).get("target", {})
        tab_id = target.get("tab_id")
        if not tab_id or not any(
            step.outcome.status is Status.SUCCESS
            and step.tool.startswith("browser.")
            and (
                (json.loads(step.outcome.result_json or "{}").get("page") or {})
                .get("target", {})
                .get("tab_id")
                == tab_id
                or any(
                    tab.get("tab_id") == tab_id
                    for tab in json.loads(step.outcome.result_json or "{}").get("tabs", [])
                )
            )
            for step in self.steps
        ):
            return
        arguments: dict[str, object] = {}
        for tool in ("browser.get_tabs", "browser.read"):
            if self.cancelled.is_set() or len(self.steps) >= self.limits.max_steps:
                return
            if self.registry.get(tool) is None:
                return
            fresh = self.engine.prepare(tool, arguments, action.mode)
            if isinstance(fresh, Outcome):
                self._record(Step(tool, fresh))
                return
            if fresh.risk is not Risk.SAFE:
                self.engine.cancel(fresh)
                return
            self.active = fresh
            outcome = await self.engine.execute(fresh)
            self.active = None
            self._record(Step(tool, outcome, fresh.payload))
            if outcome.status is not Status.SUCCESS:
                return
            tabs = BrowserResult.model_validate_json(outcome.result_json or "{}").tabs
            current = next((tab for tab in tabs if tab.tab_id == tab_id), None)
            if tool == "browser.get_tabs":
                if current is None:
                    return
                arguments = {"target": current.model_dump()}

    def _recoverable(self, outcome: Outcome) -> bool:
        # Replanning is bounded by both the ordinary budget and three consecutive failures.
        # Cancellation, approval/policy denial, audit failures and timeouts always stop.
        return outcome.error in {
            ErrorCode.INVALID_REQUEST,
            ErrorCode.UNOBSERVED_TARGET,
            ErrorCode.PRECONDITION,
            ErrorCode.TARGET_CHANGED,
            ErrorCode.PAGE_CHANGED,
            ErrorCode.CONTROL_UNSUPPORTED,
            ErrorCode.APPLICATION_MISSING,
            ErrorCode.APPLICATION_AMBIGUOUS,
            ErrorCode.VERIFICATION,
        } and not (
            len(self.steps) >= 3
            and all(
                step.outcome.status not in (Status.SUCCESS, Status.SIMULATED)
                for step in self.steps[-3:]
            )
        )

    def _is_effect(self, action: Action | Step) -> bool:
        registered = self.registry.get(action.tool)
        risk = (
            action.risk
            if isinstance(action, Action)
            else registered.risk
            if registered
            else Risk.CONFIRM
        )
        return risk is not Risk.SAFE or action.tool in {
            "windows.open_app",
            "windows.open_chrome_url",
            "windows.focus_app",
            "windows.type_text",
            "windows.click_element",
            "windows.send_hotkey",
            "windows.clipboard_set",
            "windows.calculator_number",
            "browser.open",
            "browser.navigate",
            "browser.click",
            "browser.type",
            "browser.close",
            "browser.press_key",
            "browser.scroll",
        }

    def cancel(self) -> None:
        self.cancelled.set()
        if self.active is not None:
            self.engine.cancel(self.active)

    async def run(self, command: str, mode: Mode = Mode.SIMULATION) -> PlanResult:
        if self._used or not command.strip() or len(command) > 4000 or not isinstance(mode, Mode):
            return PlanResult("error", error="invalid_request")
        if self.limits.durable and self.journal is None:
            # The long bounds exist because a journal carries the task across a restart.
            # Without one they would be a promise made on trust, so they are refused here
            # rather than honoured quietly (`AGENTS.md`, planner boundary).
            return PlanResult("error", error="journal_required")
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
                result = await job
                self._learn(command, result)
                return self._settle(result)
            self.cancel()
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            return self._settle(
                PlanResult("cancelled" if watcher in done else "timeout", tuple(self.steps))
            )
        except asyncio.CancelledError:
            self.cancel()
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            return self._settle(PlanResult("cancelled", tuple(self.steps)))
        except TimeoutError:
            return self._settle(PlanResult("timeout", tuple(self.steps)))
        except ProviderError as error:
            return self._settle(PlanResult("error", tuple(self.steps), error.code))
        except Exception:
            return self._settle(PlanResult("error", tuple(self.steps), "planner_failed"))
        finally:
            if self.active is not None:
                self.engine.cancel(self.active)
                self.active = None
            self.memory = MemoryContext()
            self.task_memory = TaskMemoryContext()
            self.scratchpad.clear()
            self._screenshot_b64 = None
            self.knowledge = KnowledgeContext()
            self.derived = DerivedContext()
            job.cancel()
            watcher.cancel()
            await asyncio.gather(job, watcher, return_exceptions=True)

    def _mark(self, phase: Phase) -> None:
        """Say where the run stands, and never let saying it change what happens.

        A store that refuses the note leaves the run looking unfinished, and that is the
        safe appearance: an unfinished run is offered for resumption, while a run wrongly
        recorded as done would quietly take its own journal out of the picture.
        """
        if self.journal is None:
            return
        try:
            self.journal.phase(phase)
        except WorkflowFailure:
            return

    def _settle(self, result: PlanResult) -> PlanResult:
        """Close the durable run with the phase its outcome deserves, unchanged."""
        self._mark(
            "done"
            if result.status in ("finished", "simulated", "no_action")
            else "cancelled"
            if result.status == "cancelled"
            else "failed"
        )
        return result

    async def _ask(self, question: str) -> str | None:
        """Waiting for the owner's words is a state of the run, not a pause inside a step."""
        self._mark("waiting_input")
        try:
            return await self.clarify(question)
        finally:
            self._mark("running")

    def _learn(self, command: str, result: PlanResult) -> None:
        """Only a run that finished teaches. A failed one would teach the wrong lesson."""
        if self.learner is None or result.status != "finished" or self.cancelled.is_set():
            return
        try:
            self.learner.learn(command, result.steps)
        except Exception:
            # Noting what worked must never change the outcome of the task itself.
            return

    async def _run(self, command: str, mode: Mode) -> PlanResult:
        answers: list[Answer] = []
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
            question = (
                "В команде есть ссылка на контакт. Уточните полную команду и точного адресата. "
                "Сохранённое имя или роль не определяют получателя и не разрешают отправку. "
                "Для почты нужен точный email-адрес."
            )
            answer = await self._ask(question)
            if answer is None:
                return PlanResult("cancelled")
            if not answer.strip() or len(answer) > 4000:
                return PlanResult("error", error="invalid_answer")
            answers.append(Answer(question, answer))
        catalog = json.dumps(self.registry.discover(), ensure_ascii=False)
        while True:
            if self.cancelled.is_set():
                raise asyncio.CancelledError
            if self.calls >= self.limits.budget.max_calls:
                # The ceiling stops the task with what it has, rather than spending past
                # what the owner agreed to and reporting it afterwards.
                return PlanResult("budget", tuple(self.steps))
            self.calls += 1
            self.notify("budget", (self.calls, self.limits.budget.max_calls))
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
                        knowledge=self.knowledge,
                        derived=self.derived,
                        screenshot_b64=self._latest_screenshot(),
                        scratchpad=self.scratchpad,
                        task_memory=self.task_memory,
                        workspace=self.workspace,
                    )
                )
            proposal = Proposal.model_validate(raw, strict=True)
            if self.cancelled.is_set():
                raise asyncio.CancelledError
            if proposal.kind == "finish":
                failed = next(
                    (
                        step.outcome
                        for step in reversed(self.steps)
                        if step.outcome.status not in (Status.SUCCESS, Status.SIMULATED)
                        and step.outcome.may_have_effects
                        and self._is_effect(step)
                    ),
                    None,
                )
                if self.steps and self.steps[-1].outcome.status not in (
                    Status.SUCCESS,
                    Status.SIMULATED,
                ):
                    failed = self.steps[-1].outcome
                if failed is not None:
                    return PlanResult("error", tuple(self.steps), failed.error.value)
                return PlanResult(
                    "no_action"
                    if not self.steps
                    else "simulated"
                    if mode is Mode.SIMULATION
                    else "finished",
                    tuple(self.steps),
                    spoken_summary=proposal.spoken_summary,
                    scratchpad=tuple(
                        ScratchpadWrite(key=key, value=value)
                        for key, value in self.scratchpad.items()
                    )
                    if mode is Mode.EXECUTE
                    else (),
                )
            if proposal.kind == "clarify":
                if len(answers) >= self.limits.max_questions:
                    return PlanResult("limit", tuple(self.steps))
                answer = await self._ask(proposal.question)
                if answer is None:
                    return PlanResult("cancelled", tuple(self.steps))
                if not answer.strip() or len(answer) > 4000:
                    return PlanResult("error", tuple(self.steps), "invalid_answer")
                answers.append(Answer(proposal.question, answer))
                continue
            if len(self.steps) >= self.limits.max_steps:
                return PlanResult("limit", tuple(self.steps))
            try:
                args = json.loads(proposal.arguments)
            except (ValueError, TypeError):
                args = None  # prepare owns validation and its durable diagnostic.
            if proposal.tool == "windows.open_app" and isinstance(args, dict):
                app_name = args.get("app")
                if isinstance(app_name, str) and app_name.casefold() in self._native_hints:
                    args = args | {"app": self._native_hints[app_name.casefold()]}
            calendar_invite = proposal.tool in (
                "calendar.create_draft_event",
                "calendar.create_event",
            )
            if (
                proposal.tool in ("outlook.local_draft", "outlook.save_draft", "outlook.send")
                or calendar_invite
            ):
                message = (
                    args.get("event" if calendar_invite else "message", {})
                    if isinstance(args, dict)
                    else {}
                )
                recipients = (
                    list(message.get("attendees", []))
                    if calendar_invite and isinstance(message, dict)
                    else [
                        value for field in ("to", "cc", "bcc") for value in message.get(field, [])
                    ]
                    if isinstance(message, dict)
                    else []
                )
                supplied = set(
                    re.findall(
                        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+",
                        "\n".join([command, *(item.answer for item in answers)]),
                    )
                )
                if (not recipients and not calendar_invite) or any(
                    not isinstance(r, str) or r not in supplied for r in recipients
                ):
                    if len(answers) >= self.limits.max_questions:
                        return PlanResult("limit", tuple(self.steps))
                    question = (
                        "Укажите точные email участников встречи. "
                        "Уточнение не подтверждает приглашение."
                        if calendar_invite
                        else (
                            "Укажите точные email для Кому, Копия и Скрытая копия. "
                            "Адрес из письма, памяти или предложения модели "
                            "не определяет получателя. "
                            "Ответ уточняет данные и не подтверждает отправку."
                        )
                    )
                    answer = await self._ask(question)
                    if answer is None:
                        return PlanResult("cancelled", tuple(self.steps))
                    if not answer.strip() or len(answer) > 4000:
                        return PlanResult("error", tuple(self.steps), "invalid_answer")
                    answers.append(Answer(question, answer))
                    continue
            if proposal.tool == "windows.click_element" and isinstance(args, dict):
                try:
                    click = ClickElement.model_validate(args)
                except ValidationError:
                    pass  # Let prepare() reject and audit the original invalid arguments.
                else:
                    if click.mark_id is not None:
                        observed = self._click_observation(click.target)
                        matches = (
                            [e for e in observed.elements if e.mark_id == click.mark_id]
                            if observed is not None
                            else []
                        )
                        if len(matches) != 1:
                            failure = self.engine.reject_proposal(
                                proposal.tool, mode, Reason.STALE_MARK
                            )
                            self._record(Step(proposal.tool, failure))
                            if self._recoverable(failure):
                                continue
                            return PlanResult("error", tuple(self.steps), failure.error.value)
                        # Resolve before prepare: policy/approval bind the full exact element.
                        args = click.model_copy(
                            update={"element": matches[0], "mark_id": None}
                        ).model_dump(mode="json")
            if proposal.tool in ("browser.click", "browser.type") and isinstance(args, dict):
                tool = self.registry.get(proposal.tool)
                if tool is not None:
                    try:
                        browser_args = tool.parameters.model_validate(args, strict=True)
                    except ValidationError:
                        pass
                    else:
                        if (
                            isinstance(browser_args, (BrowserClick, BrowserType))
                            and browser_args.mark_id is not None
                        ):
                            page = self._browser_page(browser_args.target)
                            browser_matches = (
                                [
                                    element
                                    for element in page.elements
                                    if element.mark_id == browser_args.mark_id
                                ]
                                if page
                                else []
                            )
                            if len(browser_matches) != 1:
                                failure = self.engine.reject_proposal(
                                    proposal.tool, mode, Reason.STALE_MARK
                                )
                                self._record(Step(proposal.tool, failure))
                                if self._recoverable(failure):
                                    continue
                                return PlanResult("error", tuple(self.steps), failure.error.value)
                            args = browser_args.model_copy(
                                update={
                                    "element": browser_matches[0],
                                    "mark_id": None,
                                }
                            ).model_dump(mode="json")
            registered = self.registry.get(proposal.tool)
            if proposal.tool == "windows.focus_app" and isinstance(args, dict):
                with suppress(ValueError, TypeError):
                    selector = FocusApp.model_validate(args)
                    if selector.target is None:
                        # Resolve human selectors from this task's observation, then bind the
                        # immutable action to its process identity. A title may change later.
                        selected = self._focus_observation(selector)
                        if selected is not None:
                            args = FocusApp(target=selected).model_dump()
            if registered is not None:
                with suppress(ValueError, TypeError):  # prepare() owns INVALID and its audit.
                    # Function calls arrive as JSON: an array is the valid wire form
                    # of a tuple. Decode with the registered strict JSON schema before
                    # the engine's independent strict Python validation and policy.
                    args = registered.parameters.model_validate_json(
                        json.dumps(args, ensure_ascii=False, allow_nan=False), strict=True
                    ).model_dump()
            action = self.engine.prepare(proposal.tool, args, mode)
            if isinstance(action, Outcome):
                self._record(Step(proposal.tool if registered is not None else "unknown", action))
                if registered is not None and self._recoverable(action):
                    continue
                return PlanResult("error", tuple(self.steps), action.error.value)
            self.active = action
            if action.tool == "memory.scratchpad_write":
                note = ScratchpadWrite.model_validate_json(action.payload)
                candidate = self.scratchpad | {note.key: note.value}
                if (
                    len(candidate) > MAX_SCRATCHPAD_KEYS
                    or len(json.dumps(candidate, ensure_ascii=False).encode())
                    > MAX_SCRATCHPAD_BYTES
                ):
                    raise ProviderError("context_limit")
            if not self._observed_target(action):
                outcome = self.engine.reject(
                    action, ErrorCode.UNOBSERVED_TARGET, self._observation_reason(action)
                )
                self.active = None
                self._record(Step(action.tool, outcome, action.payload))
                if self._recoverable(outcome):
                    await self._refresh_browser(action)
                    continue
                return PlanResult("error", tuple(self.steps), outcome.error.value)
            # Reads leave nothing behind, so only effects are journalled and guarded.
            journal = self.journal if action.risk is not Risk.SAFE else None
            key = step_key(action.tool, action.payload)
            if key in self._uncertain_effects:
                outcome = self.engine.reject(
                    action, ErrorCode.DUPLICATE_EFFECT, Reason.EFFECT_ALREADY_ISSUED
                )
                self.active = None
                self._record(Step(action.tool, outcome, action.payload))
                return PlanResult("error", tuple(self.steps), outcome.error.value)
            if journal is not None and journal.issued(key):
                outcome = self.engine.reject(
                    action, ErrorCode.DUPLICATE_EFFECT, Reason.EFFECT_ALREADY_ISSUED
                )
                self.active = None
                self._record(Step(action.tool, outcome, action.payload))
                return PlanResult("error", tuple(self.steps), "duplicate_effect")
            token = None
            if action.risk is Risk.CONFIRM:
                self._mark("waiting_approval")
                token = await self.approve(action)
                self._mark("running")
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
            self._screenshot_b64 = (
                outcome.screenshot_b64
                if action.tool == "windows.screenshot" and outcome.status is Status.SUCCESS
                else None
            )
            outcome = replace(outcome, screenshot_b64=None)
            step = Step(action.tool, outcome, action.payload)
            self.steps.append(step)
            if action.tool == "memory.scratchpad_write" and outcome.status is Status.SUCCESS:
                note = ScratchpadWrite.model_validate_json(outcome.result_json or "{}")
                self.scratchpad[note.key] = note.value
            self.notify("tool", step)
            if self.harvester is not None and outcome.status is Status.SUCCESS:
                # Noting what was seen must never decide whether the task continues.
                self.harvester.record(action.tool, self.run_id, outcome.result_json)
            if journal is not None:
                try:
                    journal.complete(key, outcome)
                except WorkflowFailure:
                    # The step stays unresolved, so a later attempt still refuses to repeat it.
                    return PlanResult("error", tuple(self.steps), "journal_unavailable")
            if outcome.status not in (Status.SUCCESS, Status.SIMULATED):
                if outcome.may_have_effects and self._is_effect(action):
                    self._uncertain_effects.add(key)
                if self._recoverable(outcome):
                    if outcome.error in (ErrorCode.PAGE_CHANGED, ErrorCode.PRECONDITION):
                        await self._refresh_browser(action)
                    continue
                return PlanResult(
                    "cancelled"
                    if outcome.status is Status.CANCELLED
                    else "timeout"
                    if outcome.status is Status.TIMEOUT
                    else "error",
                    tuple(self.steps),
                    outcome.error.value,
                )

    def _observation_reason(self, action: Action) -> Reason:
        if action.tool in ("windows.click_element", "browser.click", "browser.type"):
            return Reason.STALE_ELEMENT
        if (
            action.tool == "windows.send_hotkey"
            and json.loads(action.payload).get("hotkey") == "Ctrl+V"
        ):
            return Reason.CLIPBOARD_NOT_OBSERVED
        return Reason.TARGET_NOT_OBSERVED

    def _focus_observation(self, selector: FocusApp) -> WindowTarget | None:
        for step in reversed(self.steps):
            if step.outcome.status is not Status.SUCCESS or not step.tool.startswith("windows."):
                continue
            metadata = json.loads(step.outcome.result_json or "{}")
            if not isinstance(metadata, dict) or not (
                "target" in metadata or "windows" in metadata
            ):
                continue  # Chrome handoff receipts do not contain WindowsResult targets.
            result = WindowsResult.model_validate_json(step.outcome.result_json or "{}")
            targets = {
                target.handle: target
                for target in [result.target, *result.windows]
                if target is not None
            }
            matches = [
                target
                for target in targets.values()
                if selector.handle == target.handle
                or selector.title is not None
                and selector.title.casefold() in target.title.casefold()
                or selector.process is not None
                and window_matches(target, selector.process)
            ]
            if matches:
                return matches[0] if len(matches) == 1 else None
        return None

    def _latest_screenshot(self) -> str | None:
        if not self.steps:
            return None
        step = self.steps[-1]
        if step.tool != "windows.screenshot" or step.outcome.status is not Status.SUCCESS:
            return None
        return self._screenshot_b64

    def _click_observation(self, target: WindowTarget) -> WindowsResult | None:
        mutations = {
            "windows.open_app",
            "windows.focus_app",
            "windows.click_element",
            "windows.type_text",
            "windows.send_hotkey",
        }
        for step in reversed(self.steps):
            # Even an uncertain/failed effect invalidates older UIA grounding.
            if step.tool in mutations:
                return None
            if step.outcome.status is not Status.SUCCESS or step.tool not in {
                "windows.inspect_active_window",
                "windows.screenshot",
            }:
                continue
            observed = WindowsResult.model_validate_json(step.outcome.result_json or "{}")
            if observed.target != target:
                # A newer active-window observation of another window expires the marks.
                if observed.target is not None:
                    return None
                continue
            if step.tool == "windows.screenshot" and not observed.elements:
                continue  # Legacy image-only screenshot must not hide the last UIA snapshot.
            return observed
        return None

    def _observed_target(self, action: Action) -> bool:
        args = json.loads(action.payload)
        if action.tool.startswith("browser.") and "target" in args:
            target = PageTarget.model_validate(args["target"])
            if action.tool in ("browser.click", "browser.type"):
                browser_page = self._browser_page(target)
                element = (
                    (BrowserClick if action.tool == "browser.click" else BrowserType)
                    .model_validate_json(action.payload)
                    .element
                )
                return (
                    browser_page is not None
                    and element is not None
                    and element in browser_page.elements
                )
            return self._browser_target_observed(target)
        if action.tool == "windows.click_element":
            click = ClickElement.model_validate_json(action.payload)
            click_observed = self._click_observation(click.target)
            if click_observed is None or click.mark_id is not None:
                return False
            if click.element is not None:
                return click.element in click_observed.elements
            return (
                click.window_rectangle is not None
                and click.window_rectangle == click_observed.window_rectangle
            )
        if action.tool == "fs.write_file" and args.get("expected_version") is not None:
            return any(
                step.tool == "fs.read_document"
                and step.outcome.status is Status.SUCCESS
                and (observed := json.loads(step.outcome.result_json or "{}"))
                and observed.get("path") == args.get("path")
                and observed.get("version") == args.get("expected_version")
                and observed.get("truncated") is False
                for step in reversed(self.steps)
            )
        # Mail and calendar are one account, so either surface may supply the observation.
        if action.tool.startswith(MICROSOFT) and action.tool != "outlook.account":
            observations = [
                json.loads(step.outcome.result_json or "{}")
                for step in self.steps
                if step.tool.startswith(MICROSOFT) and step.outcome.status is Status.SUCCESS
            ]
            if not any(value.get("account") == args.get("account") for value in observations):
                return False
            if action.tool == "calendar.create_event":
                return any(
                    value.get("state") == "local_draft"
                    and value.get("draft_id") == args.get("draft_id")
                    and value.get("event") == args.get("event")
                    and value.get("account") == args.get("account")
                    for value in observations
                )
            if action.tool == "outlook.read":
                return any(
                    row.get("id") == args.get("message_id")
                    for value in observations
                    if value.get("state") == "listed"
                    for row in json.loads(value.get("data", "[]"))
                    if isinstance(row, dict)
                )
            if action.tool in ("calendar.get", "calendar.update", "calendar.cancel"):
                # A meeting is addressable only after this task has seen it listed, read
                # or created; an identifier the model supplies on its own is refused.
                calendar_observations = [
                    json.loads(step.outcome.result_json or "{}")
                    for step in self.steps
                    if step.tool.startswith("calendar.") and step.outcome.status is Status.SUCCESS
                ]
                listed = any(
                    row.get("id") == args.get("event_id")
                    for value in calendar_observations
                    if value.get("state") == "listed"
                    for row in (
                        value["events"]
                        if isinstance(value.get("events"), list)
                        else json.loads(value.get("data", "[]"))
                    )
                    if isinstance(row, dict)
                )
                return listed or any(
                    value.get("event_id") == args.get("event_id")
                    for value in calendar_observations
                    if value.get("event_id")
                )
            return True
        if action.tool == "windows.focus_app" and args.get("target") is None:
            return self._focus_observation(FocusApp.model_validate_json(action.payload)) is not None
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
                if action.tool == "windows.send_hotkey" and args.get("hotkey") == "Ctrl+V":
                    for observed in reversed(self.steps):
                        if (
                            observed.tool in ("windows.clipboard_get", "windows.clipboard_set")
                            and observed.outcome.status is Status.SUCCESS
                        ):
                            clipboard = json.loads(observed.outcome.result_json or "{}")
                            return args.get("clipboard_sha256") is not None and args[
                                "clipboard_sha256"
                            ] == clipboard.get("clipboard_sha256")
                    return False
                if action.tool == "windows.calculator_number":
                    if args.get("calculator") == result.get("calculator"):
                        return True
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

    def _browser_target_observed(self, target: PageTarget) -> bool:
        return self._browser_latest(target)[0]

    def _browser_page(self, target: PageTarget) -> PageView | None:
        return self._browser_latest(target)[1]

    def _browser_latest(self, target: PageTarget) -> tuple[bool, PageView | None]:
        """Latest observation per tab, never history before a failed/uncertain mutation."""
        mutations = {
            "browser.click",
            "browser.type",
            "browser.navigate",
            "browser.close",
            "browser.scroll",
            "browser.press_key",
            "browser.trigger_1password_autofill",
        }
        for step in reversed(self.steps):
            if not step.tool.startswith("browser."):
                continue
            result = BrowserResult.model_validate_json(step.outcome.result_json or "{}")
            if result.closed_tab == target.tab_id or result.affected_tab == target.tab_id:
                return False, None
            if step.outcome.status is Status.SUCCESS:
                if result.page is not None and result.page.target.tab_id == target.tab_id:
                    return (True, result.page) if result.page.target == target else (False, None)
                for tab in result.tabs:
                    if tab.tab_id == target.tab_id:
                        return tab == target, None
            if step.tool in mutations:
                payload = json.loads(step.payload or "{}")
                if payload.get("target", {}).get("tab_id") == target.tab_id:
                    return False, None
        return False, None
