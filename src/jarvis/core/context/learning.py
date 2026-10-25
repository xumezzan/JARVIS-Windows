"""Where the third source of context comes from: the owner's own finished runs.

The audit log deliberately carries no payload and the workflow journal keeps identity and
outcome but not arguments. That is the right design and it stays, which means learned
memory cannot be mined out of the logs afterwards - it has to be written here, once, by
code the owner switched on.

A run teaches only when it finished. A run that failed halfway would teach that a word
means a tool that did not work, and the assistant would confidently repeat it. What is
taken from it is deliberately thin: the words the owner said, and the tools that actually
succeeded. Arguments stay out, with one exception - opening an application records which
application opened, because "открой почту" is precisely the mapping worth keeping.
"""

import json
from typing import Protocol

from jarvis.core.context.relevance import terms
from jarvis.core.planner.contracts import Step
from jarvis.memory.derived import DerivedStore, Learned
from jarvis.memory.store import MemoryFailure
from jarvis.permissions.policies import Status

MAX_PHRASES = 4
MAX_MEANINGS = 3
LAUNCH = "windows.open_app"


class Learner(Protocol):
    def learn(self, command: str, steps: tuple[Step, ...]) -> None: ...


def meanings(steps: tuple[Step, ...]) -> tuple[tuple[Learned, str], ...]:
    """What the run turned out to do, as mappings rather than as a transcript."""
    found: list[tuple[Learned, str]] = []
    for step in steps:
        if step.outcome.status is not Status.SUCCESS:
            continue
        pair: tuple[Learned, str] = ("tool", step.tool)
        if pair not in found:
            found.append(pair)
        if step.tool == LAUNCH:
            try:
                application = json.loads(step.payload or "{}").get("app")
            except ValueError:
                continue
            opened: tuple[Learned, str] = ("application", str(application))
            if application and opened not in found:
                found.append(opened)
    return tuple(found[:MAX_MEANINGS])


class MemoryLearner:
    def __init__(self, store: DerivedStore) -> None:
        self.store = store
        self.written = 0

    def learn(self, command: str, steps: tuple[Step, ...]) -> None:
        """Note what worked. Like harvesting, it must never decide anything for the task."""
        try:
            if not self.store.learning():
                return
        except MemoryFailure:
            return
        found = meanings(steps)
        if not found:
            return
        for phrase in terms(command)[:MAX_PHRASES]:
            for kind, target in found:
                try:
                    self.store.record(kind, phrase, target)
                    self.written += 1
                except MemoryFailure:
                    # A word that reads like a secret, or a full store: skip it, keep the rest.
                    continue
