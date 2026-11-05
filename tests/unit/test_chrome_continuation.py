"""Every next action remains a model decision, including after navigation."""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from jarvis.core.planner.contracts import PlannerInput, Step
from jarvis.core.planner.deepseek_provider import DeepSeekProvider
from jarvis.core.planner.openai_provider import OpenAIProvider
from jarvis.permissions.engine import Outcome
from jarvis.permissions.policies import Mode, Status


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["openai", "deepseek"])
async def test_navigation_never_injects_a_hardcoded_next_step(vendor: str) -> None:
    step = Step(
        "windows.open_chrome_url",
        Outcome(
            uuid4(), Status.SUCCESS, result_json='{"submitted":true,"url":"https://example.test/"}'
        ),
    )
    data = PlannerInput("продолжи произвольную задачу", (), (step,), Mode.EXECUTE, "[]")
    if vendor == "openai":
        response = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"kind":"finish"}'}],
                }
            ],
        }
        provider_type: type[OpenAIProvider] | type[DeepSeekProvider] = OpenAIProvider
    else:
        response = {"choices": [{"message": {"content": '{"kind":"finish"}'}}]}
        provider_type = DeepSeekProvider
    transport = AsyncMock(return_value=json.dumps(response).encode())
    proposal = await provider_type("fixture", transport=transport).propose(data)
    assert proposal.kind == "finish"
    transport.assert_awaited_once()
