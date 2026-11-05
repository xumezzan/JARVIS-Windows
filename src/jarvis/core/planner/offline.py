"""Explicit scripted diagnostic provider; never interprets user commands."""

import asyncio
import json
from collections.abc import Sequence

from jarvis.core.planner.contracts import PlannerInput, Proposal, ProviderError


def call(tool: str, arguments: object) -> Proposal:
    return Proposal(kind="call", tool=tool, arguments=json.dumps(arguments, ensure_ascii=False))


class OfflineProvider:
    """Run a trusted diagnostic script only when the caller explicitly supplies it.

    There is deliberately no natural-language parser or default task recipe. Production
    user requests belong to OpenAIProvider; an absent API key never selects this provider.
    """

    def __init__(self, *, demo_actions: Sequence[Proposal] = ()) -> None:
        self.demo_actions = tuple(demo_actions)

    async def propose(self, data: PlannerInput) -> Proposal:
        await asyncio.sleep(0)
        if not self.demo_actions:
            raise ProviderError("provider_unavailable")
        if len(data.steps) < len(self.demo_actions):
            return self.demo_actions[len(data.steps)]
        return Proposal(kind="finish")
