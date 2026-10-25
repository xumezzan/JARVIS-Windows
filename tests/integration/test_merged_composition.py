"""The combined product keeps both executable surfaces and their safety boundaries."""

import json
import runpy
from pathlib import Path

import pytest

from jarvis.config import AppConfig
from jarvis.core.composition import build
from jarvis.core.planner.openai_provider import OpenAIProvider, payload_bytes, strict_schema
from jarvis.security.browser_policy import BrowserMode, NetworkPolicy
from jarvis.tools.base import ToolError


def test_connector_workbench_keeps_local_tools_and_fits_multimodal_transport(
    tmp_path: Path,
) -> None:
    bench = build(AppConfig(tmp_path))
    try:
        names = {tool["name"] for tool in bench.registry.discover()}
        assert {
            "calendar.list",
            "files.read_text",
            "windows.get_text_fields",
            "windows.screenshot",
            "windows.click_element",
            "windows.clipboard_get",
            "fs.read_document",
            "fs.write_file",
            "python.sandbox_eval",
            "memory.scratchpad_write",
        } <= names
        probe = runpy.run_path(
            str(Path(__file__).parents[2] / "scripts/probe_multimodal_budget.py")
        )
        payload, _ = OpenAIProvider("fixture-model")._request_payload(
            probe["worst_case"](bench.registry)
        )
        assert len(payload_bytes(payload)) < 262_144
        decoded = json.loads(payload_bytes(payload))
        assert decoded == payload and len(payload["tools"]) > 30
    finally:
        bench.shutdown()


def test_schema_compaction_retains_fields_named_like_schema_metadata() -> None:
    schema = strict_schema(
        {
            "type": "object",
            "title": "Fixture parameters",
            "description": "Repeated class documentation",
            "properties": {
                "title": {"type": "string", "description": "The literal user title"},
                "description": {"type": "string"},
            },
        }
    )
    assert set(schema["properties"]) == {"title", "description"}
    assert set(schema["required"]) == {"title", "description"}
    assert schema["properties"]["title"]["description"] == "The literal user title"
    assert schema["additionalProperties"] is False


def test_main_positional_configuration_contracts_remain_compatible(tmp_path: Path) -> None:
    config = AppConfig(tmp_path, 2500, 120, ("https://example.com",), (), "model", "fast")
    assert config.task_timeout_ms == 2500 and config.activity_limit == 120
    assert config.planner_model == "model" and config.fast_model == "fast"
    policy = NetworkPolicy(("https://example.com",), None, ("blocked.example",), ())
    assert policy.mode is BrowserMode.STRICT and policy.blocked_domains == ("blocked.example",)


@pytest.mark.parametrize("mode", list(BrowserMode))
def test_read_only_site_cannot_become_writable_in_live_mode(mode: BrowserMode) -> None:
    policy = NetworkPolicy(("https://console.aws.amazon.com",), mode=mode)
    assert policy.validate_request("https://console.aws.amazon.com/", "GET")
    with pytest.raises(ToolError, match="network_denied"):
        policy.validate_request("https://console.aws.amazon.com/", "POST", "change=1")
