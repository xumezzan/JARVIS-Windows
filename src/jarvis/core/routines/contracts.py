"""What a background routine is, and the only things it may produce.

A routine is trusted code, not a plan. It declares which observation to make next and turns
what came back into suggestions. Nothing a service answers becomes a tool name, an argument
or a schedule, so a mailbox full of someone else's words cannot steer what runs while the
owner is away.

That is also why no model runs in the background at all. A routine exists to notice, and
noticing does not need one: the observations are fixed, and the sentence the owner reads is
assembled from the observed fields by the same trusted code. It is a stronger promise than
pinning the background to the fast model, and it costs nothing to keep.
"""

import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Protocol

MAX_OBSERVATIONS = 4
MAX_SUGGESTIONS = 20
MAX_TITLE = 120
MAX_DETAIL = 400
SUGGESTION_TTL = 1800.0


def reason_key(routine: str, *parts: str) -> str:
    """Identity of a reason: the same meeting, the same letter, the same day's agenda."""
    return sha256("\n".join((routine, *parts)).encode()).hexdigest()


@dataclass(frozen=True)
class Call:
    """One observation. Its arguments are written here, never taken from an answer."""

    tool: str
    arguments: dict[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.tool or len(self.tool) > 100 or not isinstance(self.arguments, dict):
            raise ValueError("A routine observes a named tool with its own arguments.")


@dataclass(frozen=True)
class Suggestion:
    """A reason waiting for the owner, and at most one thing to do about it.

    It holds no approval and no prepared action. The window prepares the action when the
    owner opens it, so what is confirmed is a snapshot of the world as it is then, not as
    it was when the routine looked.
    """

    routine: str
    key: str
    title: str
    detail: str = ""
    tool: str = ""
    arguments: str = "{}"

    def __post_init__(self) -> None:
        if not self.routine or not self.key or not self.title:
            raise ValueError("A suggestion names its routine, its reason and what it saw.")
        if len(self.title) > MAX_TITLE or len(self.detail) > MAX_DETAIL:
            raise ValueError("A suggestion is a short sentence, not a document.")
        if len(self.tool) > 100 or len(self.arguments) > 65536:
            raise ValueError("A suggested action names a tool and its exact arguments.")
        try:
            parsed = json.loads(self.arguments)
        except ValueError:
            raise ValueError("Suggested arguments must be exact JSON.") from None
        if not isinstance(parsed, dict) or (not self.tool and parsed):
            raise ValueError("Arguments belong to a suggested tool.")

    @property
    def actionable(self) -> bool:
        return bool(self.tool)


class Routine(Protocol):
    """Trusted code that looks, and says what it saw.

    `observe` is asked repeatedly with everything seen so far, so one observation can name
    the account another returned; it answers `None` when it has looked enough. `notice`
    returns nothing at all when there is no reason, which is the normal outcome and the
    reason routines stay quiet.
    """

    name: str
    title: str
    description: str
    every_seconds: int

    def observe(self, seen: dict[str, str], now: float) -> Call | None: ...

    def notice(self, seen: dict[str, str], now: float) -> tuple[Suggestion, ...]: ...
