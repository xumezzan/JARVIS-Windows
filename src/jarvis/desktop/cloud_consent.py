"""Local task review policy; no model call can classify or approve its own disclosure."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from jarvis.core.planner.contracts import PlannerInput, Proposal, Provider
from jarvis.desktop.protocol import Request
from jarvis.knowledge.models import KnowledgeContext
from jarvis.memory.conversation import TaskMemoryContext
from jarvis.memory.derived import DerivedContext


def review_reasons(command: str) -> tuple[str, ...]:
    text = command.casefold()
    rules = (
        (
            r"оплат|плат[её]ж|покуп|\bкупи|деньг|банков|кредит|инвест|\bpay|purchase|transfer",
            "Деньги и покупки",
        ),
        (
            r"\bотправ(?:ь|ить|ляй)|опублику|разошли|\bsend\b|\bpublish\b|\bpost\b",
            "Отправка или публикация",
        ),
        (
            r"\bудал|\bочист|перезапи[сш]|\bdelete\b|\boverwrite\b|\berase\b",
            "Удаление или перезапись данных",
        ),
        (
            r"аккаунт|парол|настрой|доступ|\baccount\b|\bsettings\b|\bpermission",
            "Аккаунты, доступ и настройки",
        ),
    )
    reasons = [label for pattern, label in rules if re.search(pattern, text)]
    transitions = re.findall(r"\b(?:сначала|затем|потом|далее|first|then|next)\b", text)
    if (
        len(command) >= 500
        or len(command.splitlines()) >= 4
        or len(transitions) >= 3
        or re.search(r"многошаг|сложн(?:ая|ую|ый|ое)|пошагов|\bcomplex\b|multi.step", text)
    ):
        reasons.append("Длинная или многошаговая задача")
    return tuple(reasons)


@dataclass(frozen=True)
class CloudReview:
    id: str
    request: Request
    reasons: tuple[str, ...]
    expires_at: float
    settings_snapshot: str
    history_available: bool
    future: asyncio.Future[bool] | None = None

    def wire(self) -> dict[str, object]:
        return {
            "id": self.id,
            "command": self.request.command,
            "mode": self.request.mode,
            "reasons": list(self.reasons),
            "expires_at": self.expires_at,
            "history_available": self.history_available,
            "profile_count": len(self.request.memory_ids),
        }


class ReviewedCloudProvider:
    def __init__(
        self,
        delegate: Provider,
        review: Callable[[], Awaitable[bool]],
        *,
        reviewed: bool,
        history_allowed: bool,
    ) -> None:
        self.delegate, self.review = delegate, review
        self.reviewed, self.history_allowed = reviewed, history_allowed

    async def propose(self, data: PlannerInput) -> Proposal:
        # A simple request can grow into a long task. Pause before the next outbound
        # call, rather than sending more observations and only then asking the owner.
        if not self.reviewed and (len(data.steps) >= 4 or data.screenshot_b64 is not None):
            self.history_allowed = await self.review()
            self.reviewed = True
        if not self.history_allowed:
            data = replace(
                data,
                task_memory=TaskMemoryContext(),
                derived=DerivedContext(),
                knowledge=KnowledgeContext(),
            )
        return await self.delegate.propose(data)
