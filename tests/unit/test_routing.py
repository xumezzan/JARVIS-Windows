"""Model routing and the DeepSeek provider. No API account, key or network is used."""

import json
from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.planner.contracts import PlannerInput, Proposal, ProviderError, Step
from jarvis.core.planner.deepseek_provider import DeepSeekProvider
from jarvis.core.planner.offline import call
from jarvis.core.planner.routing import EscalatingRouter, Escalation, Tier
from jarvis.observability.audit import ErrorCode
from jarvis.permissions.engine import Outcome
from jarvis.permissions.policies import Mode, Status
from jarvis.tools.local import local_registry


def context(*tools: str) -> PlannerInput:
    registry, _ = local_registry()
    steps = tuple(
        Step(tool, Outcome(uuid4(), Status.SUCCESS, ErrorCode.NONE, result_json="{}"))
        for tool in tools
    )
    return PlannerInput(
        "проверь систему", (), steps, Mode.SIMULATION, json.dumps(registry.discover())
    )


class Fake:
    def __init__(self, tier: Tier, model: str, *, fail: str = "") -> None:
        self.tier = tier
        self.model = model
        self.fail = fail
        self.calls = 0

    async def propose(self, data: PlannerInput) -> Proposal:
        self.calls += 1
        if self.fail:
            raise ProviderError(self.fail)  # type: ignore[arg-type]
        return Proposal(kind="finish")


def router(**kwargs: Any) -> tuple[EscalatingRouter, Fake, Fake]:
    fast = Fake("fast", "deepseek-flash", fail=kwargs.pop("fast_fails", ""))
    strong = Fake("strong", "gpt-5.4-mini")
    return EscalatingRouter(fast, strong, **kwargs), fast, strong


@pytest.mark.asyncio
async def test_an_ordinary_request_stays_on_the_fast_model() -> None:
    route, fast, strong = router()
    await route.propose(context())
    await route.propose(context("local.check"))
    assert (fast.calls, strong.calls) == (2, 0)
    assert route.summary == "deepseek-flash: обычная задача"


@pytest.mark.asyncio
async def test_a_plan_that_keeps_going_moves_up() -> None:
    route, fast, strong = router()
    await route.propose(context())
    await route.propose(context("local.check", "local.check"))
    assert (fast.calls, strong.calls) == (1, 1)
    assert "план вышел" in route.summary


@pytest.mark.asyncio
async def test_touching_a_second_application_moves_up() -> None:
    route, fast, strong = router(escalation=Escalation(steps=8))
    await route.propose(context("local.check", "browser.read"))
    assert (fast.calls, strong.calls) == (0, 1)
    assert "больше одного приложения" in route.summary


@pytest.mark.asyncio
async def test_an_off_schema_answer_is_retried_on_the_strong_model() -> None:
    route, fast, strong = router(fast_fails="provider_output")
    assert (await route.propose(context())).kind == "finish"
    assert (fast.calls, strong.calls) == (1, 1)
    assert "не по схеме" in route.summary


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["credentials", "provider_unavailable", "context_limit"])
async def test_a_refused_request_is_never_moved_to_the_other_vendor(code: str) -> None:
    route, fast, strong = router(fast_fails=code)
    with pytest.raises(ProviderError):
        await route.propose(context())
    # Sending the user's data elsewhere because one vendor said no is not a fallback.
    assert strong.calls == 0


@pytest.mark.asyncio
async def test_escalation_does_not_drift_back_down() -> None:
    route, fast, strong = router()
    await route.propose(context("local.check", "local.check"))
    await route.propose(context())
    assert (fast.calls, strong.calls) == (0, 2)


def test_thresholds_outside_their_range_are_refused() -> None:
    for steps, applications in ((0, 1), (17, 1), (2, 0), (2, 9)):
        with pytest.raises(ValueError):
            Escalation(steps=steps, applications=applications)


def test_a_model_identifier_must_be_a_plain_token() -> None:
    assert DeepSeekProvider("deepseek-flash").tier == "fast"
    for model in ("", "x" * 101, "deep seek"):
        with pytest.raises(ValueError):
            DeepSeekProvider(model)


@pytest.mark.asyncio
async def test_deepseek_sends_one_action_at_a_time_without_beta_strict_mode() -> None:
    seen: list[dict[str, Any]] = []

    async def transport(payload: dict[str, Any]) -> bytes:
        seen.append(payload)
        return json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "local__check",
                                        "arguments": '{"delay_ms":0,"fail":false}',
                                    },
                                }
                            ]
                        },
                    }
                ]
            }
        ).encode()

    proposal = await DeepSeekProvider("deepseek-flash", transport=transport).propose(context())
    assert proposal.tool == "local.check"
    payload = seen[0]
    assert payload["parallel_tool_calls"] is False and payload["stream"] is False
    # Beta strict mode is documented to return malformed arguments, so it is not requested.
    assert all("strict" not in tool["function"] for tool in payload["tools"])
    check = next(tool for tool in payload["tools"] if tool["function"]["name"] == "local__check")
    parameters = check["function"]["parameters"]
    assert parameters["additionalProperties"] is False
    assert set(parameters["required"]) == {"delay_ms", "fail"}
    # Both providers describe the task identically; only the envelope differs.
    assert json.loads(payload["messages"][1]["content"])["user_command"] == "проверь систему"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        '{"choices":[]}',
        '{"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}',
        '{"choices":[{"message":{"tool_calls":[{"type":"function","function":{"name":"nope",'
        '"arguments":"{}"}}]}}]}',
        '{"choices":[{"message":{"content":"{\\"kind\\":\\"call\\",\\"tool\\":\\"local.check\\"}"}}]}',
        '{"choices":[{"message":{"content":"{oops"}}]}',
    ],
)
async def test_an_unusable_answer_becomes_one_finite_error(body: str) -> None:
    async def transport(payload: dict[str, Any]) -> bytes:
        return body.encode()

    with pytest.raises(ProviderError) as error:
        await DeepSeekProvider("deepseek-flash", transport=transport).propose(context())
    assert error.value.code == "provider_output"


@pytest.mark.asyncio
async def test_a_control_answer_passes_through_untouched() -> None:
    async def transport(payload: dict[str, Any]) -> bytes:
        return json.dumps(
            {"choices": [{"message": {"content": '{"kind":"clarify","question":"Куда?"}'}}]}
        ).encode()

    proposal = await DeepSeekProvider("deepseek-flash", transport=transport).propose(context())
    assert proposal.kind == "clarify" and proposal.question == "Куда?"


@pytest.mark.asyncio
async def test_the_runner_never_learns_there_are_two_models() -> None:
    route, _, _ = router()
    proposal = await route.propose(context())
    assert isinstance(proposal, Proposal)
    assert call("local.check", {}).kind == "call"


@pytest.mark.asyncio
async def test_several_calls_at_once_still_propose_one_action() -> None:
    """Measured on deepseek-flash: parallel_tool_calls=false is sent and not honoured.

    Asked for three files, the vendor answers with three calls in one message. Refusing the
    whole answer used to cost the task its first action, and with no retries, the task.
    """

    async def transport(payload: dict[str, Any]) -> bytes:
        assert payload["parallel_tool_calls"] is False  # still asked for, still ignored
        return json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "local__check",
                                        "arguments": '{"delay_ms":0,"fail":false}',
                                    },
                                },
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "local__check",
                                        "arguments": '{"delay_ms":5,"fail":true}',
                                    },
                                },
                            ]
                        },
                    }
                ]
            }
        ).encode()

    proposal = await DeepSeekProvider("deepseek-flash", transport=transport).propose(context())
    # The first call is the proposal; the rest are dropped unexecuted, so one step is one action.
    assert proposal.kind == "call" and proposal.tool == "local.check"
    assert json.loads(proposal.arguments) == {"delay_ms": 0, "fail": False}
