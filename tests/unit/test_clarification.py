"""Bounded optional choices cross both provider transports without authority."""

import json
from typing import Any

import pytest
from pydantic import ValidationError
from tests.unit.test_openai_provider import context

from jarvis.core.planner.contracts import Proposal
from jarvis.core.planner.deepseek_provider import DeepSeekProvider
from jarvis.core.planner.openai_provider import OpenAIProvider


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "finish", "choices": ["Продолжить"]},
        {"kind": "call", "tool": "local.check", "choices": ["Да"]},
        {"kind": "clarify", "question": "Куда?", "choices": ["x"] * 5},
        {"kind": "clarify", "question": "Куда?", "choices": [" "]},
        {"kind": "clarify", "question": "Куда?", "choices": [" Шаблон"]},
        {"kind": "clarify", "question": "Куда?", "choices": ["Шаблон", "шаблон"]},
        {"kind": "clarify", "question": "Куда?", "choices": ["x" * 121]},
        {"kind": "clarify", "question": "Куда?", "choices": [{"label": "Шаблон"}]},
    ],
)
def test_invalid_choices_rejected(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Proposal.model_validate(fields)


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["deepseek", "openai"])
@pytest.mark.parametrize("choices", [[], ["Подготовить шаблон", "Я перечислю дела"]])
async def test_choices_survive_provider_protocol(vendor: str, choices: list[str]) -> None:
    seen: list[dict[str, Any]] = []

    async def transport(payload: dict[str, Any]) -> bytes:
        seen.append(payload)
        control = json.dumps(
            {"kind": "clarify", "question": "Как подготовить?", "choices": choices}
        )
        if vendor == "openai":
            return json.dumps(
                {
                    "status": "completed",
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": control}]}
                    ],
                }
            ).encode()
        return json.dumps(
            {"choices": [{"finish_reason": "stop", "message": {"content": control}}]}
        ).encode()

    provider = (
        OpenAIProvider("test-model", transport=transport)
        if vendor == "openai"
        else DeepSeekProvider("test-model", transport=transport)
    )
    proposal = await provider.propose(context())
    assert proposal.question == "Как подготовить?" and proposal.choices == choices
    if vendor == "openai":
        schema = seen[0]["text"]["format"]["schema"]
        assert "choices" in schema["required"]
        assert schema["properties"]["choices"]["maxItems"] == 4


def test_legacy_clarification_without_choices_remains_usable() -> None:
    assert Proposal.model_validate_json('{"kind":"clarify","question":"Куда?"}').choices == []
