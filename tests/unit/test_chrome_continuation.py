"""URL handoff resumes native observations; it is never proof of a purchase."""

import json
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from jarvis.core.planner.contracts import PlannerInput, Step
from jarvis.core.planner.deepseek_provider import DeepSeekProvider
from jarvis.core.planner.openai_provider import OpenAIProvider, chrome_handoff_observation
from jarvis.permissions.engine import Outcome
from jarvis.permissions.policies import Mode, Status
from jarvis.tools.windows import ChromeHandoff


def context() -> PlannerInput:
    receipt = ChromeHandoff(url="https://www.apple.com/", submitted=True)
    step = Step(
        "windows.open_chrome_url",
        Outcome(uuid4(), Status.SUCCESS, result_json=receipt.model_dump_json()),
    )
    return PlannerInput(
        "Открой apple.com и добавь в корзину iphone 18 pro max 256 gb burgundy",
        (),
        (step,),
        Mode.EXECUTE,
        json.dumps([{"name": "windows.get_open_windows", "risk": "SAFE"}]),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [OpenAIProvider, DeepSeekProvider])
async def test_successful_handoff_observes_before_another_model_call(provider: type) -> None:
    transport = AsyncMock(side_effect=AssertionError("No model/credentials needed for this read"))
    proposal = await provider("fixture", transport=transport).propose(context())
    assert proposal.tool == "windows.get_open_windows"
    assert json.loads(proposal.arguments) == {"app": None}
    transport.assert_not_called()


@pytest.mark.parametrize("status", [Status.SIMULATED, Status.ERROR, Status.DENIED, Status.TIMEOUT])
def test_unsuccessful_handoff_never_manufactures_observation(status: Status) -> None:
    data = context()
    step = replace(data.steps[-1], outcome=replace(data.steps[-1].outcome, status=status))
    assert chrome_handoff_observation(replace(data, steps=(step,))) is None


def test_simulation_missing_tool_invalid_receipt_and_newer_observation_do_not_inject_reads() -> (
    None
):
    data = context()
    assert chrome_handoff_observation(replace(data, mode=Mode.SIMULATION)) is None
    assert chrome_handoff_observation(replace(data, catalog_json="[]")) is None
    assert chrome_handoff_observation(replace(data, steps=())) is None
    for receipt in ('{"submitted":true}', '{"url":"https://apple.com/","submitted":false}'):
        step = replace(data.steps[-1], outcome=replace(data.steps[-1].outcome, result_json=receipt))
        assert chrome_handoff_observation(replace(data, steps=(step,))) is None
    observed = Step("windows.get_open_windows", Outcome(uuid4(), Status.SUCCESS, result_json="{}"))
    assert chrome_handoff_observation(replace(data, steps=(*data.steps, observed))) is None


def test_owner_blocked_observation_is_not_proposed() -> None:
    data = replace(
        context(),
        catalog_json=json.dumps([{"name": "windows.get_open_windows", "risk": "BLOCKED"}]),
    )
    assert chrome_handoff_observation(data) is None
