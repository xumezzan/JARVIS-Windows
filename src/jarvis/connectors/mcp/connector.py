"""An MCP server as an ordinary connector, with one extra promise before every call.

The other connectors know their own surface because it is written in their code. This one
knows it only because the owner reviewed it, so each call first confirms that the server
still offers exactly the tools that were reviewed. A server that added, removed or reworded
a tool has changed the thing the owner agreed to, and nothing runs until they look again.

That confirmation is cached for the session: one extra listing per server per run, not per
call. A listing that cannot be made at all - no key, no network, an answer that is not a
manifest - fails the precondition, because "I could not check" is not "it is unchanged".
"""

from jarvis.connectors.base import AuthState, Capability, Health
from jarvis.connectors.http import ServiceTransport, TransportError
from jarvis.connectors.mcp.manifest import McpTool, Server, digest, read_manifest
from jarvis.connectors.mcp.models import MAX_DATA, McpArguments, McpResult
from jarvis.connectors.mcp.protocol import (
    McpFailure,
    initialize,
    rpc,
    translate,
    transport,
)
from jarvis.core.planner.contracts import ProviderError
from jarvis.security.credentials import load_api_key
from jarvis.tools.base import ExecutionContext

MAX_BLOCK = 4000
MAX_BLOCKS = 16


def summary(tool: McpTool) -> str:
    """What the planner reads: the owner-approved description and the argument names."""
    named = ", ".join(tool.arguments)
    text = tool.description + (f" Аргументы: {named}." if named else " Без аргументов.")
    return text[:200]


def render(content: object) -> tuple[str, bool]:
    """Text blocks only, bounded. Anything else is left behind and reported as truncated."""
    if not isinstance(content, list):
        return "", True
    parts: list[str] = []
    dropped = len(content) > MAX_BLOCKS
    for block in content[:MAX_BLOCKS]:
        text = block.get("text") if isinstance(block, dict) else None
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(text, str):
            parts.append(text[:MAX_BLOCK])
            dropped = dropped or len(text) > MAX_BLOCK
        else:
            dropped = True
    joined = "\n".join(parts)
    return joined[:MAX_DATA], dropped or len(joined) > MAX_DATA


class McpConnector:
    def __init__(self, server: Server, session: ServiceTransport | None = None) -> None:
        self.server = server
        self.service = server.service
        self.session = session or transport(server.origin, server.path)
        self.unchanged: bool | None = None

    def capabilities(self) -> tuple[Capability, ...]:
        """Exactly the reviewed tools, at exactly the levels the owner set."""
        return tuple(
            Capability(tool.name, summary(tool), tool.risk, False) for tool in self.server.tools
        )

    def tool(self, name: str) -> McpTool | None:
        return next((tool for tool in self.server.tools if tool.name == name), None)

    async def _key(self) -> str:
        try:
            return await load_api_key(self.service)
        except ProviderError:
            raise McpFailure("mcp_credentials") from None

    async def authenticate(self) -> AuthState:
        try:
            await self._key()
        except McpFailure:
            return AuthState("disconnected")
        return AuthState("connected", self.service)

    async def health_check(self) -> Health:
        try:
            await self.manifest()
        except McpFailure as failure:
            if failure.code == "mcp_credentials":
                return Health("unauthenticated", "credentials")
            if failure.code in ("mcp_response", "mcp_request", "mcp_refused"):
                return Health("unavailable", "configuration")
            return Health("unavailable", "network")
        return Health("ready")

    async def manifest(self) -> tuple[McpTool, ...]:
        """Read the server's current tool list. Used for review and for the check before use."""
        key = await self._key()
        try:
            await initialize(self.session, key, self.server.path)
            result = await rpc(self.session, key, self.server.path, "tools/list", {})
        except TransportError as error:
            raise translate(error) from None
        return read_manifest(result)

    async def still_reviewed(self, context: ExecutionContext) -> bool:
        """The precondition: the server offers what the owner passed through, or nothing runs."""
        await context.checkpoint()
        if self.unchanged is None:
            try:
                live = await self.manifest()
            except McpFailure:
                self.unchanged = False
            else:
                current = digest(self.service, self.server.origin, self.server.path, live)
                self.unchanged = bool(self.server.reviewed) and current == self.server.reviewed
        return self.unchanged

    async def call(self, tool: McpTool, args: McpArguments, context: ExecutionContext) -> McpResult:
        await context.checkpoint()
        key = await self._key()
        try:
            result = await rpc(
                self.session,
                key,
                self.server.path,
                "tools/call",
                {"name": tool.remote, "arguments": dict(args.arguments)},
            )
        except TransportError as error:
            raise translate(error) from None
        if result.get("isError"):
            # The server refused the work; reporting it as an answer would be a false success.
            raise McpFailure("mcp_refused")
        data, truncated = render(result.get("content"))
        return McpResult(tool=tool.remote, data=data, truncated=truncated)
