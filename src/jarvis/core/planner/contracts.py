"""Immutable messages across provider, runner and UI boundaries."""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import Field, model_validator

from jarvis.knowledge.models import KnowledgeContext
from jarvis.memory.derived import DerivedContext
from jarvis.memory.models import MemoryContext
from jarvis.permissions.engine import Outcome
from jarvis.permissions.policies import Mode
from jarvis.tools.base import ToolModel


class Proposal(ToolModel):
    kind: Literal["call", "clarify", "finish"]
    tool: str = Field(default="", max_length=100)
    arguments: str = Field(default="{}", max_length=65536, repr=False)
    question: str = Field(default="", max_length=1000, repr=False)

    @model_validator(mode="after")
    def distinct_modes(self) -> "Proposal":
        if self.kind == "call":
            if not self.tool or self.question:
                raise ValueError("Invalid call proposal.")
        elif self.tool or self.arguments != "{}" or (self.kind == "finish" and self.question):
            raise ValueError("Invalid control proposal.")
        elif self.kind == "clarify" and not self.question.strip():
            raise ValueError("Empty clarification.")
        return self


@dataclass(frozen=True)
class Step:
    tool: str
    outcome: Outcome
    # The exact arguments the engine normalised, kept so the assistant can say what it did.
    # Providers receive tool, status and result only; this field never reaches them.
    payload: str = field(default="", repr=False)


@dataclass(frozen=True)
class Answer:
    """One clarification, kept together with the question it answered.

    An answer used to travel on its own, and a live run showed what that costs. The strong
    model asked whether it should carry on, was told "yes", could not tell from `["yes"]`
    what it had agreed to, and asked again - three times, until the question limit ended a
    task with two of its seven steps done. A bare answer answers nothing.
    """

    question: str = field(repr=False)
    answer: str = field(repr=False)


@dataclass(frozen=True)
class PlannerInput:
    command: str = field(repr=False)
    answers: tuple[Answer, ...] = field(repr=False)
    steps: tuple[Step, ...] = field(repr=False)
    mode: Mode
    catalog_json: str = field(repr=False)
    memory: MemoryContext = field(default_factory=MemoryContext, repr=False)
    knowledge: KnowledgeContext = field(default_factory=KnowledgeContext, repr=False)
    derived: DerivedContext = field(default_factory=DerivedContext, repr=False)


class ProviderError(Exception):
    """Only finite error categories may cross into UI/logs."""

    def __init__(
        self,
        code: Literal[
            "provider_unavailable",
            "provider_failed",
            "provider_output",
            "credentials",
            "context_limit",
        ],
    ):
        self.code = code
        super().__init__(code)


class Provider(Protocol):
    async def propose(self, data: PlannerInput) -> Proposal: ...


@dataclass(frozen=True)
class Budget:
    """What one task may spend on thinking, counted rather than estimated.

    Calls are the honest unit. A price per call would have to be copied from a vendor's
    page, would go stale without saying so, and would put a number in front of the owner
    that nothing in this repository can verify. So the ceiling is the number of times a
    model was asked; the run reports how much of it went, and stops when it is gone.

    The count is deliberately tier-blind, because the runner is: it never learns that there
    are two models (`planner/routing.py`). A ceiling that distinguished the expensive model
    would have to live with the router that chooses it.
    """

    max_calls: int = 16

    def __post_init__(self) -> None:
        if not 1 <= self.max_calls <= 256:
            raise ValueError("Invalid planner budget.")


@dataclass(frozen=True)
class Limits:
    """Bounds on one task.

    The long bounds are not a matter of trust: `durable` may only be set by a caller that
    also carries the run journal, and the runner refuses a long task without one. Sixty-four
    steps is the journal's own ceiling (`workflow.models.MAX_STEPS`), so a task can never
    outgrow the record that makes resuming it safe.
    """

    max_steps: int = 8
    max_questions: int = 3
    provider_seconds: float = 30
    total_seconds: float = 180
    durable: bool = False
    budget: "Budget" = field(default_factory=Budget)

    def __post_init__(self) -> None:
        steps, seconds = (64, 3600) if self.durable else (16, 300)
        if not (
            1 <= self.max_steps <= steps
            and 0 <= self.max_questions <= 3
            and 0 < self.provider_seconds <= 60
            and 0 < self.total_seconds <= seconds
        ):
            raise ValueError("Invalid planner limits.")

    @classmethod
    def long(cls, budget: "Budget | None" = None) -> "Limits":
        """The bounds a durable run may use: the owner's own multi-step work."""
        return cls(
            max_steps=64,
            provider_seconds=60,
            total_seconds=3600,
            durable=True,
            budget=budget or Budget(max_calls=96),
        )


PlanStatus = Literal[
    "finished", "simulated", "no_action", "error", "cancelled", "timeout", "limit", "budget"
]


@dataclass(frozen=True)
class PlanResult:
    status: PlanStatus
    steps: tuple[Step, ...] = field(default=(), repr=False)
    error: str = ""

    @property
    def summary(self) -> str:
        titles = {
            "finished": "Планировщик завершил работу. Ниже — факты выполнения инструментов.",
            "simulated": "Симуляция завершена. Реальные действия не выполнялись.",
            "no_action": "Действия не выполнены. Результат задачи не подтверждён.",
            "error": "Выполнение остановлено с ошибкой.",
            "cancelled": "Задача отменена.",
            "timeout": "Истекло время задачи или ожидания ответа.",
            "limit": "Достигнут предел шагов или уточнений.",
            "budget": "Исчерпан бюджет обращений к модели. Задача остановлена.",
        }
        rows = [titles[self.status]]
        if self.error:
            rows.append("Категория: " + self.error)
        rows.extend(
            f"{index}. {step.tool}: {step.outcome.status.value} ({step.outcome.error.value})"
            for index, step in enumerate(self.steps, 1)
        )
        if self.status in ("error", "cancelled", "timeout", "limit", "budget") and any(
            step.outcome.may_have_effects for step in self.steps
        ):
            rows.append("Уже выданные действия не отозваны; проверьте их результаты.")
        return "\n".join(rows)
