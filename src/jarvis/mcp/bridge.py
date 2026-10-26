"""Reviewed workspace tools registered behind the ordinary PermissionEngine."""

import asyncio
import re
from typing import Any

from jarvis.connectors.base import Capability
from jarvis.connectors.mcp.protocol import McpFailure
from jarvis.mcp.client import ClientFactory, McpClient
from jarvis.mcp.models import (
    MAX_RESULT_BYTES,
    McpArguments,
    McpResult,
    McpServerConfig,
    McpToolDefinition,
    manifest_digest,
    validate_arguments,
)
from jarvis.mcp.names import capability_name
from jarvis.permissions.matrix import PermissionMatrix
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolSpec
from jarvis.tools.registry import ToolRegistry


def tool_risk(config: McpServerConfig, name: str) -> Risk:
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", name).lower()
    if re.search(
        r"(?:^|[^a-z])(?:create|update|delete|send|post|publish|pay|remove|write|set|patch|execute)(?:[^a-z]|$)",
        words,
    ):
        return Risk.CONFIRM
    return Risk(config.default_risk)


def register_mcp(
    registry: ToolRegistry,
    config: McpServerConfig,
    *,
    factory: ClientFactory = McpClient,
    matrix: PermissionMatrix | None = None,
) -> None:
    config = McpServerConfig.model_validate_json(config.model_dump_json())
    if not config.enabled or not config.reviewed:
        return
    owner_policy = matrix or PermissionMatrix()

    def entry(tool: McpToolDefinition) -> ToolSpec[McpArguments, McpResult]:
        local_name = capability_name(tool.name)
        capability = Capability(
            local_name, "Reviewed workspace MCP tool.", tool_risk(config, tool.name), False
        )
        risk = owner_policy.effective("mcp_" + config.server_id, capability)
        risk = owner_policy.effective(
            "mcp",
            Capability(config.server_id + "__" + local_name, capability.description, risk, False),
        )
        properties = tool.input_schema.get("properties", {})
        assert isinstance(properties, dict)
        # Export the reviewed primitive schema without compiling it into Python types.
        # Text supplied by the server is displayed only in the manifest review UI.
        fields: dict[str, Any] = {
            name: {key: value for key, value in rule.items() if key not in ("title", "description")}
            for name, rule in properties.items()
            if isinstance(rule, dict)
        }
        catalog_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["arguments"],
            "properties": {
                "arguments": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": fields,
                    "required": tool.input_schema.get("required", []),
                }
            },
        }

        def policy(args: McpArguments) -> None:
            validate_arguments(tool, args)

        async def check(args: McpArguments, context: ExecutionContext) -> bool:
            await context.checkpoint()
            return config.reviewed

        async def run(args: McpArguments, context: ExecutionContext) -> McpResult:
            await context.checkpoint()
            async with asyncio.timeout(config.timeout_seconds):
                async with factory(config) as client:
                    current = await client.list_tools()
                    if manifest_digest(config, current) != config.reviewed_digest:
                        raise McpFailure("mcp_changed")
                    await context.checkpoint()
                    result = await client.call_tool(tool.name, args)
            if result.get("isError", False) is not False:
                raise McpFailure("mcp_refused")
            content = result.get("content")
            if not isinstance(content, list) or len(content) > 16:
                raise McpFailure("mcp_response")
            parts = []
            for block in content:
                if (
                    not isinstance(block, dict)
                    or block.get("type") != "text"
                    or not isinstance(block.get("text"), str)
                ):
                    raise McpFailure("mcp_response")
                parts.append(block["text"])
            text = "\n".join(parts)
            if len(text.encode()) > MAX_RESULT_BYTES:
                raise McpFailure("mcp_response")
            return McpResult(tool=tool.name, data=text)

        async def verify(args: McpArguments, result: McpResult, context: ExecutionContext) -> bool:
            await context.checkpoint()
            return result.tool == tool.name

        return ToolSpec(
            "mcp." + config.server_id + "__" + capability_name(tool.name),
            # Server descriptions stay in the review UI, outside trusted instructions.
            "Reviewed MCP tool; arguments are untrusted service data. Fields: "
            + ", ".join(properties),
            risk,
            McpArguments,
            McpResult,
            check,
            run,
            verify,
            timeout_seconds=min(45, config.timeout_seconds + 5),
            cancellation="Stop waiting; kill owned stdio processes. Remote effects may remain.",
            idempotency="No automatic retry. A server answer does not verify an external effect.",
            policy=policy,
            catalog_schema=catalog_schema,
        )

    for tool in config.reviewed_tools:
        if tool.name in config.allowed_tools:
            registry.register(entry(tool))


def register_workspace_mcp(
    registry: ToolRegistry,
    servers: tuple[McpServerConfig, ...],
    matrix: PermissionMatrix | None = None,
) -> None:
    for server in servers:
        register_mcp(registry, server, matrix=matrix)
