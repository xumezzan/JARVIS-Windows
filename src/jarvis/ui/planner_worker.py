"""Qt bridge: only UI owns approval authority; provider/runner receive opaque replies."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from PySide6.QtCore import QThread, Signal

from jarvis.core.context.learning import Learner
from jarvis.core.planner.contracts import Limits, PlanResult, Provider, Step
from jarvis.core.planner.runner import Runner
from jarvis.core.workflow.journal import Journal
from jarvis.knowledge.harvest import Harvester
from jarvis.knowledge.models import KnowledgeContext
from jarvis.memory.derived import DerivedContext
from jarvis.memory.models import MemoryContext
from jarvis.permissions.approvals import Action, ApprovalToken
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.policies import Mode
from jarvis.tools.registry import ToolRegistry


@dataclass(frozen=True)
class Prompt:
    id: str
    kind: Literal["approval", "clarification"]
    value: Action | str


class PlannerWorker(QThread):
    progress_event = Signal(str, object)
    prompt = Signal(object)

    def __init__(
        self,
        registry: ToolRegistry,
        engine: PermissionEngine,
        provider: Provider,
        command: str,
        mode: Mode,
        limits: Limits,
        memory: MemoryContext | None = None,
        knowledge: KnowledgeContext | None = None,
        derived: DerivedContext | None = None,
        harvester: Harvester | None = None,
        learner: Learner | None = None,
        journal: Journal | None = None,
        resumed: tuple[Step, ...] = (),
    ) -> None:
        super().__init__()
        self.command = command
        self.mode = mode
        self.runner = Runner(
            registry,
            engine,
            provider,
            self._approve,
            self._clarify,
            limits=limits,
            memory=memory,
            knowledge=knowledge,
            derived=derived,
            journal=journal,
            resumed=resumed,
            harvester=harvester,
            learner=learner,
            notify=self.progress_event.emit,
        )
        self.outcome = PlanResult("error", error="worker_failed")
        self.loop: asyncio.AbstractEventLoop | None = None
        self.waiting: tuple[str, asyncio.Future[object]] | None = None

    async def _ask(self, kind: Literal["approval", "clarification"], value: Action | str) -> object:
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        prompt = Prompt(uuid4().hex, kind, value)
        self.waiting = (prompt.id, future)
        self.prompt.emit(prompt)
        try:
            return await future
        finally:
            self.waiting = None

    async def _approve(self, action: Action) -> ApprovalToken | None:
        value = await self._ask("approval", action)
        return value if isinstance(value, ApprovalToken) else None

    async def _clarify(self, question: str) -> str | None:
        value = await self._ask("clarification", question)
        return value if isinstance(value, str) else None

    def respond(self, prompt_id: str, value: object) -> None:
        def deliver() -> None:
            pending = self.waiting
            if pending is not None and pending[0] == prompt_id and not pending[1].done():
                pending[1].set_result(value)

        if self.loop is not None and not self.loop.is_closed():
            # Completion can close the loop between the UI's check and delivery.
            with suppress(RuntimeError):
                self.loop.call_soon_threadsafe(deliver)

    def cancel(self) -> None:
        self.runner.cancel()

    def run(self) -> None:
        async def run() -> PlanResult:
            self.loop = asyncio.get_running_loop()
            return await self.runner.run(self.command, self.mode)

        try:
            self.outcome = asyncio.run(run())
        except Exception:
            self.outcome = PlanResult("error", tuple(self.runner.steps), "worker_failed")
        finally:
            self.command = ""
