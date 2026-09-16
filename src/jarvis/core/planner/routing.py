"""Choosing which model plans a step, and moving up only on evidence.

Every request starts on the fast model. It moves to the strong one when the task has shown
itself to be multi-layered — the plan has run past a couple of steps, or it has touched more
than one application — or when the fast model answers off-schema. Nothing is classified in
advance, so no extra model call stands between the user and their first action, and the
decision is reproducible from the run itself rather than from a guess about the wording.

Escalation is one-way within a run. A task that has proven complex does not drift back to
the cheap model halfway through and start contradicting its own earlier reasoning.

A schema violation is worth escalating because the fast model's answer is rejected before
anything executes, so retrying costs nothing but tokens. Missing credentials and an
unreachable service are not: silently moving a refused request onto the other vendor would
send the user's data somewhere they did not choose and bill them for it.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from jarvis.core.planner.contracts import PlannerInput, Proposal, ProviderError

Tier = Literal["fast", "strong"]
Reason = Literal["start", "steps", "applications", "off_schema"]

REASONS: dict[Reason, str] = {
    "start": "обычная задача",
    "steps": "план вышел за отведённые шаги",
    "applications": "задача затронула больше одного приложения",
    "off_schema": "быстрая модель ответила не по схеме",
}


class ModelProvider(Protocol):
    tier: Tier
    model: str

    async def propose(self, data: PlannerInput) -> Proposal: ...


@dataclass(frozen=True)
class Escalation:
    """Thresholds counted from the run's own observations."""

    steps: int = 2
    applications: int = 1

    def __post_init__(self) -> None:
        if not 1 <= self.steps <= 16 or not 1 <= self.applications <= 8:
            raise ValueError("Invalid escalation thresholds.")


@dataclass(frozen=True)
class Choice:
    tier: Tier
    model: str
    reason: Reason

    @property
    def text(self) -> str:
        return f"{self.model}: {REASONS[self.reason]}"


@dataclass
class EscalatingRouter:
    """A provider in its own right: the runner never learns that there are two models."""

    fast: ModelProvider
    strong: ModelProvider
    escalation: Escalation = field(default_factory=Escalation)
    choices: list[Choice] = field(default_factory=list)
    escalated: bool = False

    def _record(self, provider: ModelProvider, reason: Reason) -> None:
        choice = Choice(provider.tier, provider.model, reason)
        if not self.choices or self.choices[-1] != choice:
            self.choices.append(choice)

    def _reason(self, data: PlannerInput) -> Reason | None:
        if len(data.steps) >= self.escalation.steps:
            return "steps"
        applications = {step.tool.split(".")[0] for step in data.steps}
        if len(applications) > self.escalation.applications:
            return "applications"
        return None

    async def propose(self, data: PlannerInput) -> Proposal:
        if not self.escalated:
            reason = self._reason(data)
            if reason is not None:
                self.escalated = True
                self._record(self.strong, reason)
        if self.escalated:
            return await self.strong.propose(data)
        self._record(self.fast, "start")
        try:
            return await self.fast.propose(data)
        except ProviderError as error:
            if error.code != "provider_output":
                raise
            # The answer was refused before anything ran, so asking again costs only tokens.
            self.escalated = True
            self._record(self.strong, "off_schema")
            return await self.strong.propose(data)

    @property
    def summary(self) -> str:
        return " → ".join(choice.text for choice in self.choices)
