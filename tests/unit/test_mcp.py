"""An MCP server described by itself and decided by the owner. No network or key is used."""

import json
from collections.abc import Iterator
from pathlib import Path
from threading import Event as Flag
from typing import Any

import pytest

from jarvis.connectors.mcp.connector import McpConnector, render, summary
from jarvis.connectors.mcp.manifest import (
    MAX_DESCRIPTION,
    NO_DESCRIPTION,
    McpTool,
    Server,
    capability_name,
    digest,
    load_servers,
    read_manifest,
    save_servers,
)
from jarvis.connectors.mcp.models import McpArguments
from jarvis.connectors.mcp.protocol import McpFailure, route_path
from jarvis.connectors.mcp.tools import register_mcp
from jarvis.core.composition import reviewed_servers
from jarvis.permissions.matrix import PermissionMatrix, Rule
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext
from jarvis.tools.registry import ToolRegistry

KEY = "synthetic-noncredential"
ORIGIN = "https://mcp.example.test"
PATH = "/mcp"

LISTED: dict[str, Any] = {
    "tools": [
        {
            "name": "search-issues",
            "description": "Найти задачи по запросу.",
            "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
        {
            "name": "create_issue",
            "description": "Создать задачу.",
            "inputSchema": {"type": "object", "properties": {"title": {}, "body": {}}},
        },
    ]
}


class FakeServer:
    """One endpoint that answers JSON-RPC, so the transport boundary stays real."""

    def __init__(self, listed: dict[str, Any] | None = None) -> None:
        self.listed = listed if listed is not None else dict(LISTED)
        self.calls: list[dict[str, Any]] = []
        self.answer: dict[str, Any] = {"content": [{"type": "text", "text": "готово"}]}
        self.failure: str = ""

    async def request(
        self,
        credential: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        assert (method, path) == ("POST", PATH) and credential == KEY
        body = dict(payload or {})
        self.calls.append(body)
        name = str(body.get("method"))
        if self.failure:
            return 200, {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32000}}
        result: dict[str, Any] = {}
        if name == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {}}
        elif name == "tools/list":
            result = dict(self.listed)
        elif name == "tools/call":
            result = dict(self.answer)
        return 200, {"jsonrpc": "2.0", "id": body.get("id"), "result": result}


def server(tools: tuple[McpTool, ...] = (), reviewed: str = "") -> Server:
    return Server("mcp_probe", ORIGIN, PATH, reviewed, tools)


@pytest.fixture
def bench(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FakeServer, ExecutionContext]]:
    async def key(provider: str = "openai") -> str:
        assert provider == "mcp_probe"
        return KEY

    monkeypatch.setattr("jarvis.connectors.mcp.connector.load_api_key", key)
    yield FakeServer(), ExecutionContext(Flag())


def connector(fake: FakeServer, entry: Server | None = None) -> McpConnector:
    return McpConnector(entry or server(), fake)  # type: ignore[arg-type]


def test_a_server_name_is_mapped_into_the_registry_rule_never_adopted() -> None:
    assert capability_name("search-issues") == "search_issues"
    assert capability_name("GitHub.Search") == "github_search"
    # Nothing usable is left, so the tool is refused instead of renamed into a guess.
    assert capability_name("__42__") == "" and capability_name("поиск") == ""


def test_a_manifest_keeps_only_what_can_be_used(bench: tuple[FakeServer, ExecutionContext]) -> None:
    tools = read_manifest(
        {
            "tools": [
                {"name": "ok", "description": "Годный.", "inputSchema": {}},
                {"name": "поиск", "description": "Имя не отображается."},
                {"name": "ok2", "description": "x" * 900},
                "not an object",
                {"description": "без имени"},
            ]
        }
    )
    assert [tool.name for tool in tools] == ["ok", "ok2"]
    assert len(tools[1].description) == MAX_DESCRIPTION
    # A description that is not text at all still leaves a readable line for the owner.
    assert read_manifest({"tools": [{"name": "x", "description": 5}]})[0].description == (
        NO_DESCRIPTION
    )


def test_a_description_carries_no_control_characters() -> None:
    tools = read_manifest({"tools": [{"name": "x", "description": "строка\nвторая"}]})
    assert tools[0].description == "строка вторая"


def test_an_unreviewed_tool_is_blocked_and_levels_stay_the_owners() -> None:
    fresh = read_manifest(LISTED)
    assert all(tool.risk is Risk.BLOCKED for tool in fresh)
    entry = server().with_tools(fresh)
    assert not entry.current and not entry.reviewed
    approved = entry.with_levels({"search-issues": Risk.ROUTINE, "create_issue": Risk.CONFIRM})
    assert approved.current
    # A level is the owner's decision, so changing one must not invalidate the review.
    relevelled = approved.with_levels({"search-issues": Risk.CONFIRM})
    assert relevelled.reviewed == approved.reviewed and relevelled.current


def test_only_three_levels_exist_for_a_server_tool() -> None:
    fresh = read_manifest(LISTED)
    for refused in (Risk.SAFE, Risk.CRITICAL):
        with pytest.raises(ValueError):
            server().with_tools(fresh).with_levels({"search-issues": refused})


def test_a_changed_manifest_drops_the_review() -> None:
    approved = (
        server().with_tools(read_manifest(LISTED)).with_levels({"search-issues": Risk.ROUTINE})
    )
    assert approved.current
    reworded = dict(LISTED)
    reworded["tools"] = [
        dict(LISTED["tools"][0], description="Теперь удаляет задачи."),
        *LISTED["tools"][1:],
    ]
    changed = digest("mcp_probe", ORIGIN, PATH, read_manifest(reworded))
    assert changed != approved.reviewed
    # Keeping the levels for the tools that survived is fine; the pin is gone regardless.
    again = approved.with_tools(read_manifest(reworded))
    assert not again.current and again.tools[0].risk is Risk.ROUTINE


@pytest.mark.asyncio
async def test_a_call_reaches_the_server_under_its_own_name(
    bench: tuple[FakeServer, ExecutionContext],
) -> None:
    fake, context = bench
    approved = (
        server().with_tools(read_manifest(LISTED)).with_levels({"search-issues": Risk.ROUTINE})
    )
    client = connector(fake, approved)
    result = await client.call(
        approved.tools[0], McpArguments(arguments={"query": "джон"}), context
    )
    call = fake.calls[-1]
    assert call["method"] == "tools/call"
    assert call["params"] == {"name": "search-issues", "arguments": {"query": "джон"}}
    assert result.tool == "search-issues" and result.data == "готово"


@pytest.mark.asyncio
async def test_nothing_runs_while_the_server_offers_something_else(
    bench: tuple[FakeServer, ExecutionContext],
) -> None:
    fake, context = bench
    approved = (
        server().with_tools(read_manifest(LISTED)).with_levels({"search-issues": Risk.ROUTINE})
    )
    assert await connector(fake, approved).still_reviewed(context)
    fake.listed = {"tools": [dict(LISTED["tools"][0], description="Другое описание.")]}
    assert not await connector(fake, approved).still_reviewed(context)
    # An unreviewed server never passes the check even when the server answers perfectly.
    assert not await connector(fake, server().with_tools(read_manifest(LISTED))).still_reviewed(
        context
    )


@pytest.mark.asyncio
async def test_a_failed_listing_is_not_an_unchanged_server(
    bench: tuple[FakeServer, ExecutionContext],
) -> None:
    fake, context = bench
    approved = (
        server().with_tools(read_manifest(LISTED)).with_levels({"search-issues": Risk.ROUTINE})
    )
    fake.failure = "refused"
    client = connector(fake, approved)
    assert not await client.still_reviewed(context)
    # The answer is cached for the session: one listing per server, not one per call.
    assert not await client.still_reviewed(context)
    assert sum(1 for call in fake.calls if call["method"] == "tools/list") <= 1


@pytest.mark.asyncio
async def test_a_refusal_is_reported_as_a_failure_not_as_an_answer(
    bench: tuple[FakeServer, ExecutionContext],
) -> None:
    fake, context = bench
    approved = (
        server().with_tools(read_manifest(LISTED)).with_levels({"search-issues": Risk.ROUTINE})
    )
    fake.answer = {"content": [{"type": "text", "text": "нет прав"}], "isError": True}
    with pytest.raises(McpFailure) as refusal:
        await connector(fake, approved).call(approved.tools[0], McpArguments(), context)
    assert refusal.value.code == "mcp_refused"


def test_an_answer_is_bounded_text_and_never_markup_or_a_handle() -> None:
    data, truncated = render(
        [{"type": "text", "text": "x" * 9000}, {"type": "image", "data": "..."}]
    )
    assert len(data) == 4000 and truncated
    assert render("не список") == ("", True)
    assert render([{"type": "resource", "resource": {"uri": "file:///etc/passwd"}}]) == ("", True)


def test_arguments_are_a_small_flat_map() -> None:
    McpArguments(arguments={"query": "джон", "limit": 5, "exact": True})
    for refused in (
        {"плохое имя": "x"},
        {"query": "x" * 3000},
        {"query": {"nested": "no"}},
        {f"a{index}": index for index in range(20)},
    ):
        with pytest.raises(ValueError):
            McpArguments(arguments=refused)  # type: ignore[arg-type]


def test_only_reviewed_arguments_reach_the_server() -> None:
    registry = ToolRegistry()
    approved = (
        server().with_tools(read_manifest(LISTED)).with_levels({"search-issues": Risk.ROUTINE})
    )
    register_mcp(registry, McpConnector(approved, FakeServer()))  # type: ignore[arg-type]
    tool = registry.get("mcp_probe.search_issues")
    assert tool is not None
    tool.normalize({"arguments": {"query": "джон"}})
    with pytest.raises(ValueError):
        # The policy hook is pure, so simulation refuses exactly what execution would.
        tool.normalize({"arguments": {"query": "джон", "callback_url": "http://x.test"}})


def test_the_registry_shows_the_owner_s_level_and_the_matrix_may_only_tighten() -> None:
    approved = (
        server()
        .with_tools(read_manifest(LISTED))
        .with_levels({"search-issues": Risk.ROUTINE, "create_issue": Risk.CONFIRM})
    )
    loose = ToolRegistry()
    register_mcp(loose, McpConnector(approved, FakeServer()))  # type: ignore[arg-type]
    search = loose.get("mcp_probe.search_issues")
    assert search is not None and search.risk is Risk.ROUTINE
    strict = ToolRegistry()
    register_mcp(
        strict,
        McpConnector(approved, FakeServer()),  # type: ignore[arg-type]
        PermissionMatrix({"mcp_probe": {"search_issues": Rule(allowed=False)}}),
    )
    blocked = strict.get("mcp_probe.search_issues")
    assert blocked is not None and blocked.risk is Risk.BLOCKED


def test_the_planner_sees_the_reviewed_words_and_the_argument_names() -> None:
    tools = read_manifest(LISTED)
    assert summary(tools[0]) == "Найти задачи по запросу. Аргументы: query."
    assert summary(McpTool("x", "x", "Без полей.")).endswith("Без аргументов.")


def test_a_server_lives_at_a_fixed_https_origin() -> None:
    for origin in ("http://mcp.example.test", "https://user@mcp.example.test", ORIGIN + "/"):
        with pytest.raises(ValueError):
            Server("mcp_probe", origin, PATH)
    for path in ("mcp", "/mcp?query=1", "/../etc"):
        with pytest.raises(ValueError):
            route_path(path)
    for name in ("probe", "outlook", "mcp_Probe", "mcp_"):
        with pytest.raises(ValueError):
            Server(name, ORIGIN, PATH)


def test_a_review_survives_a_restart_and_a_broken_file_leaves_no_servers(
    tmp_path: Path,
) -> None:
    approved = (
        server()
        .with_tools(read_manifest(LISTED))
        .with_levels({"search-issues": Risk.ROUTINE, "create_issue": Risk.CONFIRM})
    )
    path = tmp_path / "mcp.json"
    save_servers(path, (approved,))
    restored = load_servers(path)[0]
    assert restored == approved and restored.current
    assert reviewed_servers(path) == (approved,)
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_servers(path)
    # A file that cannot be read costs the session its MCP tools, never its start.
    assert reviewed_servers(path) == ()
    assert reviewed_servers(tmp_path / "absent.json") == ()


def test_a_hand_edited_file_that_adds_a_tool_is_not_reviewed(tmp_path: Path) -> None:
    approved = (
        server().with_tools(read_manifest(LISTED)).with_levels({"search-issues": Risk.ROUTINE})
    )
    path = tmp_path / "mcp.json"
    save_servers(path, (approved,))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["servers"][0]["tools"].append(
        {
            "name": "delete_everything",
            "remote": "delete-everything",
            "description": "Добавлено мимо проверки.",
            "arguments": [],
            "risk": "ROUTINE",
        }
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    smuggled = load_servers(path)[0]
    assert len(smuggled.tools) == 3 and not smuggled.current
    assert reviewed_servers(path) == ()
