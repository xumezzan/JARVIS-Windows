"""Workspace MCP: reviewed schemas, engine authorization, bytes and owned stdio only."""

import asyncio
import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from jarvis.connectors.mcp.protocol import PROTOCOL_VERSION, McpFailure
from jarvis.mcp.bridge import register_mcp, tool_risk
from jarvis.mcp.client import HttpWire, McpClient, StdioWire, discover
from jarvis.mcp.models import McpArguments, McpResult, McpServerConfig, McpToolDefinition
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalAuthority, ApprovalStore
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.matrix import PermissionMatrix, Rule
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.tools.registry import ToolRegistry
from jarvis.workspace.models import WorkspaceProfile
from jarvis.workspace.store import WorkspaceStore


def tool(name: str = "search") -> McpToolDefinition:
    return McpToolDefinition(
        name=name,
        description="Untrusted fixture description",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "maxLength": 100}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )


def server(**changes: Any) -> McpServerConfig:
    return McpServerConfig.model_validate(
        {
            "server_id": "fixture",
            "name": "Fixture MCP",
            "transport": "http",
            "url": "https://mcp.example.test/mcp",
            "allowed_tools": ("search", "create_issue"),
            "default_risk": "SAFE",
            **changes,
        }
    )


class FakeWire:
    def __init__(self) -> None:
        self.tools = (tool(), tool("create_issue"))
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self.text = "Fixture answer"
        self.error = False
        self.wrong_id = False
        self.started: asyncio.Event | None = None
        self.release: asyncio.Event | None = None
        self.audit_path: Path | None = None

    async def exchange(self, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(request)
        if "id" not in request:
            return {}
        method = request["method"]
        result: dict[str, Any] = {}
        if method == "initialize":
            result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": row.name,
                        "description": row.description,
                        "inputSchema": row.input_schema,
                    }
                    for row in self.tools
                ]
            }
        elif method == "tools/call":
            if self.audit_path is not None:
                with closing(sqlite3.connect(self.audit_path)) as reader:
                    records = [
                        json.loads(row[0]) for row in reader.execute("SELECT record FROM events")
                    ]
                assert records[-1]["event"] == "started"
            if self.started is not None:
                self.started.set()
            if self.release is not None:
                await self.release.wait()
            result = {"content": [{"type": "text", "text": self.text}], "isError": self.error}
        return {
            "jsonrpc": "2.0",
            "id": "wrong" if self.wrong_id else request["id"],
            "result": result,
        }

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def bench(
    tmp_path: Path,
) -> Iterator[tuple[PermissionEngine, ApprovalAuthority, FakeWire, AuditLog]]:
    wire = FakeWire()
    registry = ToolRegistry()
    config = server().with_review(wire.tools)
    register_mcp(registry, config, factory=lambda entry: McpClient(entry, wire))
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        wire.audit_path = audit.path
        store = ApprovalStore()
        yield (
            PermissionEngine(registry, store, audit),
            store.take_authority(audit.approved),
            wire,
            audit,
        )


def test_reviewed_configuration_roundtrip_and_catalog_export(tmp_path: Path) -> None:
    config = server().with_review((tool(), tool("create_issue")))
    profile = WorkspaceProfile(mcp_servers=(config,))
    store = WorkspaceStore(tmp_path)
    store.save(profile)
    assert store.load() == profile and config.reviewed
    registry = ToolRegistry()
    register_mcp(registry, config)
    catalog = {row["name"]: row for row in registry.discover()}
    assert set(catalog) == {"mcp.fixture__search", "mcp.fixture__create_issue"}
    definition = registry.get("mcp.fixture__search")
    assert definition is not None
    schema = catalog[definition.name]["parameters"]
    assert isinstance(schema, dict)
    assert schema["properties"]["arguments"]["properties"]["query"]["type"] == "string"
    assert "Untrusted fixture description" not in str(catalog)
    for args in ({"query": 1}, {"query": "q", "extra": "x"}, {}, {"query": "x" * 101}):
        with pytest.raises(ValueError):
            definition.normalize({"arguments": args})
    assert "query" in definition.normalize({"arguments": {"query": "hello"}})
    assert not server().reviewed
    assert not config.model_copy(update={"command": ("/different",)}).reviewed
    assert "mcp_servers" not in profile.planner_context().model_dump()


@pytest.mark.parametrize(
    "name",
    [
        "create",
        "createIssue",
        "update_issue",
        "delete",
        "send",
        "post",
        "read_and_send",
        "publish",
        "pay",
    ],
)
def test_mutations_cannot_inherit_safe_from_server_or_base_config(name: str) -> None:
    assert tool_risk(server(), name) is Risk.CONFIRM
    assert tool_risk(server(), "search") is Risk.SAFE
    assert tool_risk(server(default_risk="CONFIRM"), "search") is Risk.CONFIRM


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array"},
        {"type": "object", "$ref": "https://untrusted.example/schema"},
        {"type": "object", "properties": {"x": {"type": "string", "pattern": "(a+)+$"}}},
        {"type": "object", "properties": {"x": {"type": "object"}}},
        {"type": "object", "additionalProperties": True},
    ],
)
def test_unsupported_remote_schemas_are_rejected(schema: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        McpToolDefinition(name="bad", input_schema=schema)


def test_unreviewed_or_disabled_server_registers_nothing() -> None:
    registry = ToolRegistry()
    register_mcp(registry, server())
    register_mcp(registry, server(enabled=False).with_review((tool(), tool("create_issue"))))
    assert not registry.discover()


def test_manifest_hash_ignores_json_object_key_order() -> None:
    first = tool()
    schema = first.input_schema
    reordered = McpToolDefinition(
        name=first.name,
        description=first.description,
        input_schema={
            "additionalProperties": False,
            "required": ["query"],
            "properties": {"query": {"maxLength": 100, "type": "string"}},
            "type": "object",
        },
    )
    assert schema == reordered.input_schema
    config = server(allowed_tools=("search",))
    assert (
        config.with_review((first,)).reviewed_digest
        == config.with_review((reordered,)).reviewed_digest
    )


def test_existing_owner_matrix_can_block_a_workspace_alias() -> None:
    registry = ToolRegistry()
    config = server().with_review((tool(), tool("create_issue")))
    register_mcp(
        registry, config, matrix=PermissionMatrix({"mcp_fixture": {"search": Rule(allowed=False)}})
    )
    entry = registry.get("mcp.fixture__search")
    assert entry is not None and entry.risk is Risk.BLOCKED


@pytest.mark.asyncio
async def test_effect_requires_exact_approval_and_durable_redacted_audit(
    bench: tuple[PermissionEngine, ApprovalAuthority, FakeWire, AuditLog],
) -> None:
    engine, authority, wire, audit = bench
    args = {"arguments": {"query": "synthetic-private-argument"}}
    denied = engine.prepare("mcp.fixture__create_issue", args, Mode.EXECUTE)
    assert isinstance(denied, Action) and denied.risk is Risk.CONFIRM
    assert (await engine.execute(denied)).status is Status.DENIED
    assert not wire.calls
    action = engine.prepare("mcp.fixture__create_issue", args, Mode.EXECUTE)
    assert isinstance(action, Action)
    token = authority.approve(action)
    result = await engine.execute(action, token)
    assert result.status is Status.SUCCESS and wire.closed
    assert McpResult.model_validate_json(result.result_json or "").effect_verified is False
    assert [row["method"] for row in wire.calls] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]
    with closing(sqlite3.connect(audit.path)) as reader:
        records = [json.loads(row[0]) for row in reader.execute("SELECT record FROM events")]
    assert all(row["arguments"] == "[REDACTED]" for row in records)
    assert "synthetic-private-argument" not in json.dumps(records)
    assert (await engine.execute(action, token)).status is Status.INVALID


@pytest.mark.asyncio
async def test_simulation_never_starts_a_client(
    bench: tuple[PermissionEngine, ApprovalAuthority, FakeWire, AuditLog],
) -> None:
    engine, _, wire, _ = bench
    action = engine.prepare("mcp.fixture__search", {"arguments": {"query": "q"}}, Mode.SIMULATION)
    assert isinstance(action, Action)
    assert (await engine.execute(action)).status is Status.SIMULATED
    assert not wire.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["changed", "is-error", "result-bytes", "wire-bytes", "wrong-id"]
)
async def test_changed_manifest_and_oversized_answers_fail_closed(
    bench: tuple[PermissionEngine, ApprovalAuthority, FakeWire, AuditLog],
    failure: str,
) -> None:
    engine, _, wire, _ = bench
    if failure == "changed":
        wire.tools = (tool().model_copy(update={"description": "Changed"}), tool("create_issue"))
    elif failure == "is-error":
        wire.error = True
    elif failure == "result-bytes":
        wire.text = "😀" * 4001
    elif failure == "wire-bytes":
        wire.text = "x" * 262144
    else:
        wire.wrong_id = True
    action = engine.prepare("mcp.fixture__search", {"arguments": {"query": "q"}}, Mode.EXECUTE)
    assert isinstance(action, Action)
    assert (await engine.execute(action)).status is Status.ERROR
    assert wire.closed
    if failure in ("changed", "wrong-id"):
        assert not any(row["method"] == "tools/call" for row in wire.calls)


@pytest.mark.asyncio
async def test_engine_cancellation_closes_the_client(
    bench: tuple[PermissionEngine, ApprovalAuthority, FakeWire, AuditLog],
) -> None:
    engine, _, wire, _ = bench
    wire.started, wire.release = asyncio.Event(), asyncio.Event()
    action = engine.prepare("mcp.fixture__search", {"arguments": {"query": "q"}}, Mode.EXECUTE)
    assert isinstance(action, Action)
    pending = asyncio.create_task(engine.execute(action))
    await asyncio.wait_for(wire.started.wait(), 2)
    engine.cancel(action)
    assert (await pending).status is Status.CANCELLED
    assert wire.closed


def test_utf8_arguments_and_result_limits_are_bytes() -> None:
    with pytest.raises(ValidationError):
        McpArguments(arguments={"a": "😀" * 2000})
    with pytest.raises(ValidationError):
        McpResult(tool="read", data="😀" * 4001)
    for value in (float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            McpArguments(arguments={"a": value})


def test_http_route_is_fixed_and_configuration_has_no_shell_or_credentials() -> None:
    wire = HttpWire(server())
    assert wire.transport.allows("POST", "/mcp")
    assert not wire.transport.allows("GET", "/mcp")
    assert wire.transport.headers["MCP-Protocol-Version"] == PROTOCOL_VERSION
    for url in (
        "http://localhost:3000/mcp",
        "https://127.0.0.1/mcp",
        "https://u:p@example.com/mcp",
        "https://example.com/mcp?token=synthetic",
    ):
        with pytest.raises(ValidationError):
            server(url=url)
    with pytest.raises(ValidationError):
        server(transport="stdio", url=None, command=("python", "-m", "server"))


@pytest.mark.asyncio
async def test_real_stdio_lifecycle_and_timeout_cleanup(tmp_path: Path) -> None:
    script = tmp_path / "fixture_mcp.py"
    script.write_text(
        "import json,sys,time,os\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line)\n"
        " if 'id' not in r: continue\n"
        " m=r['method']\n"
        " if m=='initialize': x={'protocolVersion':'2025-06-18','capabilities':{'tools':{}}}\n"
        " elif m=='tools/list': x={'tools':[{'name':'search','inputSchema':"
        "{'type':'object','properties':{'query':{'type':'string'}},'required':['query']}}]}\n"
        " else:\n"
        "  if r['params']['arguments'].get('query')=='slow': time.sleep(10)\n"
        "  x={'content':[{'type':'text','text':'synthetic stdio answer'}]}\n"
        " print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':x}),flush=True)\n",
        encoding="utf-8",
    )
    config = server(
        transport="stdio",
        url=None,
        command=(sys.executable, "-I", str(script)),
        allowed_tools=("search",),
        timeout_seconds=1,
    )
    manifest = await discover(config)
    assert manifest[0].name == "search"
    wire = StdioWire(config)
    async with McpClient(config, wire) as client:
        result = await client.call_tool("search", McpArguments(arguments={"query": "q"}))
        assert result["content"][0]["text"] == "synthetic stdio answer"
    assert wire.process is None
    slow_wire = StdioWire(config)
    with pytest.raises(McpFailure):
        async with McpClient(config, slow_wire) as client:
            await client.call_tool("search", McpArguments(arguments={"query": "slow"}))
    assert slow_wire.process is None
