"""What a server says it can do, and what the owner decided about it.

Every other connector declares its capabilities in code, so their risk levels are trusted
by construction. An MCP server describes itself, and a self-description is a value from a
service - exactly what `connectors/base.py` says capability metadata must never be. The
gap is closed by the owner rather than by cleverness: **an MCP tool does not assign itself
a level**. The server's answer becomes a manifest, the owner passes the manifest once in
the interface and sets a level per tool, and nothing outside that reviewed manifest exists
for the planner at all.

The manifest is pinned by a hash over what the owner actually read: the server's own tool
names, the descriptions and the argument names. A server that changes any of them comes
back for review instead of quietly inheriting the levels of the tools it used to have.
Levels are deliberately outside the hash - they are the owner's decision, not the server's
description, and changing one must not invalidate the review.

The default at review is `CONFIRM`, and the owner lowers to `ROUTINE` what they consider
routine. Being wrong towards one question too many is cheaper than being wrong towards a
silent payment. `SAFE` is not offered at all: nothing here is verified well enough for it.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from jarvis.connectors.http import ORIGIN
from jarvis.connectors.mcp.protocol import route_path
from jarvis.permissions.policies import Risk

MAX_TOOLS = 32
MAX_ARGUMENTS = 16
MAX_DESCRIPTION = 160
MAX_BYTES = 262144
MAX_SERVERS = 8

SERVICE = re.compile(r"mcp_[a-z][a-z0-9_]{0,31}")
REMOTE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:/-]{0,79}")
ARGUMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
LEVELS = (Risk.ROUTINE, Risk.CONFIRM, Risk.BLOCKED)
UNREVIEWED = Risk.BLOCKED
NO_DESCRIPTION = "Инструмент MCP-сервера без описания."


def capability_name(remote: str) -> str:
    """The registry's name rule wins: a server's own name is mapped, never adopted."""
    folded = unicodedata.normalize("NFKC", remote).casefold()
    mapped = "".join(char if char.isascii() and char.isalnum() else "_" for char in folded)
    trimmed = re.sub(r"_+", "_", mapped).strip("_")
    if not trimmed or not trimmed[0].isalpha():
        # Nothing usable is left, so this tool is refused rather than renamed into a guess.
        return ""
    return trimmed[:60]


def describe(value: object) -> str:
    """A description is shown to the owner and to the planner, so it stays short data."""
    if not isinstance(value, str):
        return NO_DESCRIPTION
    text = unicodedata.normalize("NFKC", value)
    printable = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in text)
    collapsed = " ".join(printable.split())
    return collapsed[:MAX_DESCRIPTION] or NO_DESCRIPTION


@dataclass(frozen=True)
class McpTool:
    """One tool as the owner reviewed it. `risk` is theirs; everything else is the server's."""

    name: str
    remote: str
    description: str
    arguments: tuple[str, ...] = ()
    risk: Risk = UNREVIEWED

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.name):
            raise ValueError("Invalid capability name.")
        if not REMOTE.fullmatch(self.remote):
            raise ValueError("Invalid remote tool name.")
        if not 1 <= len(self.description) <= MAX_DESCRIPTION:
            raise ValueError("A tool needs a short description.")
        if len(self.arguments) > MAX_ARGUMENTS or len(set(self.arguments)) != len(self.arguments):
            raise ValueError("Invalid argument list.")
        if any(ARGUMENT.fullmatch(name) is None for name in self.arguments):
            raise ValueError("Invalid argument name.")
        if self.risk not in LEVELS:
            raise ValueError("An MCP tool is ROUTINE, CONFIRM or BLOCKED.")

    def pinned(self) -> dict[str, object]:
        """Exactly what the owner read; the level they chose is theirs and stays out."""
        return {
            "remote": self.remote,
            "name": self.name,
            "description": self.description,
            "arguments": list(self.arguments),
        }


@dataclass(frozen=True)
class Server:
    """One configured server: where it is, what was reviewed, and at what levels."""

    service: str
    origin: str
    path: str
    reviewed: str = ""
    tools: tuple[McpTool, ...] = ()

    def __post_init__(self) -> None:
        if not SERVICE.fullmatch(self.service):
            raise ValueError("A server's name is its own namespace: mcp_<label>.")
        if not ORIGIN.fullmatch(self.origin) or "@" in self.origin or self.origin.endswith("/"):
            raise ValueError("A server lives at a fixed https origin.")
        route_path(self.path)
        if self.reviewed and not re.fullmatch(r"[a-f0-9]{64}", self.reviewed):
            raise ValueError("Invalid manifest digest.")
        names = [tool.name for tool in self.tools]
        remotes = [tool.remote for tool in self.tools]
        if len(self.tools) > MAX_TOOLS or len(set(names)) != len(names):
            raise ValueError("Duplicate or excessive tools.")
        if len(set(remotes)) != len(remotes):
            raise ValueError("Duplicate remote tool.")

    @property
    def digest(self) -> str:
        return digest(self.service, self.origin, self.path, self.tools)

    @property
    def current(self) -> bool:
        """True when the tools on file are the ones the owner passed through."""
        return bool(self.reviewed) and self.reviewed == self.digest

    def with_tools(self, tools: tuple[McpTool, ...]) -> "Server":
        """A freshly read manifest keeps the levels already chosen for tools that survived."""
        chosen = {tool.remote: tool.risk for tool in self.tools}
        kept = tuple(
            McpTool(
                name=tool.name,
                remote=tool.remote,
                description=tool.description,
                arguments=tool.arguments,
                risk=chosen.get(tool.remote, tool.risk),
            )
            for tool in tools
        )
        return Server(self.service, self.origin, self.path, "", kept)

    def with_levels(self, levels: dict[str, Risk]) -> "Server":
        """The owner's decision, and the pin taken at the same moment."""
        tools = tuple(
            McpTool(
                name=tool.name,
                remote=tool.remote,
                description=tool.description,
                arguments=tool.arguments,
                risk=levels.get(tool.remote, tool.risk),
            )
            for tool in self.tools
        )
        return Server(
            self.service,
            self.origin,
            self.path,
            digest(self.service, self.origin, self.path, tools),
            tools,
        )


def digest(service: str, origin: str, path: str, tools: tuple[McpTool, ...]) -> str:
    payload = json.dumps(
        {
            "service": service,
            "origin": origin,
            "path": path,
            "tools": [tool.pinned() for tool in sorted(tools, key=lambda tool: tool.remote)],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def read_manifest(result: dict[str, Any]) -> tuple[McpTool, ...]:
    """Turn a server's `tools/list` answer into a manifest, refusing what cannot be used."""
    listed = result.get("tools")
    if not isinstance(listed, list):
        return ()
    tools: list[McpTool] = []
    seen: set[str] = set()
    for entry in listed[:MAX_TOOLS]:
        if not isinstance(entry, dict):
            continue
        remote = entry.get("name")
        if not isinstance(remote, str) or REMOTE.fullmatch(remote) is None:
            continue
        name = capability_name(remote)
        # A tool whose name collides after mapping is dropped, never silently renamed.
        if not name or name in seen or remote in {tool.remote for tool in tools}:
            continue
        try:
            tools.append(
                McpTool(
                    name=name,
                    remote=remote,
                    description=describe(entry.get("description")),
                    arguments=arguments_of(entry.get("inputSchema")),
                )
            )
        except ValueError:
            continue
        seen.add(name)
    return tuple(tools)


def arguments_of(schema: object) -> tuple[str, ...]:
    """Only the argument names, bounded. A server's schema is not compiled or trusted."""
    if not isinstance(schema, dict):
        return ()
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return ()
    names = [
        name
        for name in list(properties)[:MAX_ARGUMENTS]
        if isinstance(name, str) and ARGUMENT.fullmatch(name)
    ]
    return tuple(names)


def load_servers(path: Path) -> tuple[Server, ...]:
    """A missing file means no MCP servers. A broken one is refused, never half-read."""
    if not path.exists():
        return ()
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError("Файл серверов MCP недоступен.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise ValueError("Файл серверов MCP не читается как JSON.") from None
    entries = data.get("servers") if isinstance(data, dict) else None
    if not isinstance(entries, list) or len(entries) > MAX_SERVERS:
        raise ValueError("Ожидается объект со списком серверов.")
    servers = tuple(_server(entry) for entry in entries)
    if len({server.service for server in servers}) != len(servers):
        raise ValueError("Два сервера с одним именем.")
    return servers


def _server(entry: object) -> Server:
    if not isinstance(entry, dict):
        raise ValueError("Сервер описывается объектом.")
    listed = entry.get("tools", [])
    if not isinstance(listed, list):
        raise ValueError("Список инструментов повреждён.")
    tools = []
    for item in listed:
        if not isinstance(item, dict):
            raise ValueError("Инструмент описывается объектом.")
        risk = item.get("risk")
        if not isinstance(risk, str) or risk not in Risk.__members__:
            raise ValueError("Неизвестный уровень у инструмента MCP.")
        arguments = item.get("arguments", [])
        if not isinstance(arguments, list) or any(not isinstance(a, str) for a in arguments):
            raise ValueError("Аргументы описываются списком имён.")
        tools.append(
            McpTool(
                name=str(item.get("name", "")),
                remote=str(item.get("remote", "")),
                description=str(item.get("description", "")),
                arguments=tuple(arguments),
                risk=Risk[risk],
            )
        )
    return Server(
        service=str(entry.get("service", "")),
        origin=str(entry.get("origin", "")),
        path=str(entry.get("path", "")),
        reviewed=str(entry.get("reviewed", "")),
        tools=tuple(tools),
    )


def save_servers(path: Path, servers: tuple[Server, ...]) -> None:
    """Written whole, through a temporary file, so a crash cannot leave half a review."""
    if len(servers) > MAX_SERVERS:
        raise ValueError("Слишком много серверов MCP.")
    payload = {
        "servers": [
            {
                "service": server.service,
                "origin": server.origin,
                "path": server.path,
                "reviewed": server.reviewed,
                "tools": [dict(tool.pinned(), risk=tool.risk.name) for tool in server.tools],
            }
            for server in servers
        ]
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(path)
