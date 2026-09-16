"""A durable record of one request and the effects it has already issued.

The journal exists to answer one question after a crash, a timeout or a second attempt:
what has already happened in the world? It therefore records identity and outcome —
which tool ran, under which key, with what status — and never arguments, results or
recipients. That is the same rule the audit log follows, for the same reason: a record
kept to make retrying safe must not become a copy of the user's content.

A step is written twice: once before the tool runs and once when its outcome is known.
That order is what makes the journal useful, because the dangerous case is precisely the
one where a service accepted the work and the answer never arrived. A step left in the
`issued` state counts as possibly done, so a later attempt refuses to repeat it rather
than guessing that nothing happened.

A key is derived from the tool and its canonical arguments, so the same effect proposed
twice in one run is recognisable even when the plan around it changed.
"""

from hashlib import sha256
from typing import Literal

from pydantic import Field, field_validator, model_validator

from jarvis.observability.audit import ErrorCode
from jarvis.permissions.policies import Mode, Status
from jarvis.tools.base import ToolModel

MAX_STEPS = 64
MAX_REQUEST = 4000

Phase = Literal["running", "waiting", "done", "failed", "cancelled"]
State = Literal["issued", "finished"]
FINISHED: tuple[Phase, ...] = ("done", "failed", "cancelled")

STATUSES = {member.value for member in Status}
ERRORS = {member.value for member in ErrorCode}


def step_key(tool: str, payload: str) -> str:
    """Identity of an effect: the tool and the exact arguments the engine canonicalised."""
    return sha256(f"{tool}\n{payload}".encode()).hexdigest()


class StepRecord(ToolModel):
    index: int = Field(ge=0, lt=MAX_STEPS)
    tool: str = Field(min_length=1, max_length=100)
    key: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: State
    status: str = Field(default="", max_length=20)
    error: str = Field(default="", max_length=40)
    may_have_effects: bool = False
    updated: int = Field(ge=0)

    @model_validator(mode="after")
    def outcome(self) -> "StepRecord":
        if self.state == "issued":
            if self.status or self.error or self.may_have_effects:
                raise ValueError("An issued step has no outcome yet.")
        elif self.status not in STATUSES or self.error not in ERRORS:
            raise ValueError("A finished step carries a known status and error.")
        return self

    @property
    def succeeded(self) -> bool:
        return self.state == "finished" and self.status == Status.SUCCESS.value

    @property
    def unresolved(self) -> bool:
        """True while the outcome is unknown: the effect may or may not have landed."""
        return self.state == "issued"


class RunRecord(ToolModel):
    """`request` is the user's own words, kept so a run can be resumed and shown back."""

    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    request: str = Field(min_length=1, max_length=MAX_REQUEST, repr=False)
    mode: str = Field(max_length=20)
    phase: Phase
    steps: tuple[StepRecord, ...] = Field(default=(), max_length=MAX_STEPS)
    created: int = Field(ge=0)
    updated: int = Field(ge=0)

    @field_validator("mode")
    @classmethod
    def known_mode(cls, value: str) -> str:
        if value not in {member.value for member in Mode}:
            raise ValueError("Unknown run mode.")
        return value

    @property
    def finished(self) -> bool:
        return self.phase in FINISHED

    def issued(self, key: str) -> StepRecord | None:
        """A step under this key whose effect may already exist.

        A step that finished without touching anything — refused by policy, rejected as
        invalid — leaves nothing behind, so it never blocks a later attempt.
        """
        return next(
            (
                step
                for step in self.steps
                if step.key == key and (step.unresolved or step.succeeded or step.may_have_effects)
            ),
            None,
        )
