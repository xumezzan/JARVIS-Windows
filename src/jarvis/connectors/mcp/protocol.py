"""Talking to an MCP server: one POST, one JSON-RPC message, finite failures.

MCP is how a service describes itself to an assistant, which is exactly why the transport
here is narrow. Only https is reachable, through the shared connector transport: fixed
origin, one allowlisted route, no redirects, no cookies, no proxies, no retries. The
process-spawning stdio transport of the specification is deliberately absent - starting a
program because a configuration file named it is not something this assistant does.

The session-based and event-stream parts of the specification are absent too: every call
is a single request that stands on its own, and a server that insists on a session or
answers with a stream is reported as unavailable rather than half-supported.

Nothing a server returns becomes authority. It comes back as bounded, finite-category
data: an answer, or one of a small set of failures with no server text in it.
"""

from typing import Any, Literal
from uuid import uuid4

from jarvis.connectors.http import Route, ServiceTransport, TransportError

MAX_RESPONSE_BYTES = 262144
PROTOCOL_VERSION = "2025-06-18"
CLIENT = {"name": "jarvis", "version": "0.0.1"}

Code = Literal[
    "mcp_credentials",
    "mcp_denied",
    "mcp_missing",
    "mcp_rate_limited",
    "mcp_network",
    "mcp_response",
    "mcp_request",
    "mcp_refused",
    "mcp_changed",
]


class McpFailure(Exception):
    """Finite public categories; no server text reaches the interface or the audit."""

    def __init__(self, code: Code) -> None:
        self.code = code
        super().__init__(code)


FAILURES: dict[str, Code] = {
    "route_denied": "mcp_request",
    "credentials": "mcp_credentials",
    "forbidden": "mcp_denied",
    "not_found": "mcp_missing",
    "rate_limited": "mcp_rate_limited",
    "response_invalid": "mcp_response",
    "response_limit": "mcp_response",
    "response_unparsable": "mcp_response",
}


def translate(error: TransportError) -> McpFailure:
    return McpFailure(FAILURES.get(error.code, "mcp_network"))


def transport(origin: str, path: str) -> ServiceTransport:
    """One origin, one route. A server cannot widen its own surface after review."""
    return ServiceTransport(
        origin,
        (Route("POST", route_path(path)),),
        # JSON only: an event stream is a session protocol this client does not hold.
        headers={"Accept": "application/json", "MCP-Protocol-Version": PROTOCOL_VERSION},
        timeout_seconds=20,
        max_response_bytes=MAX_RESPONSE_BYTES,
        success_statuses=(200,),
    )


def route_path(path: str) -> str:
    """The endpoint as a literal route pattern: no query, no host, nothing to widen."""
    if not path.startswith("/") or len(path) > 120:
        raise ValueError("An endpoint path is a short absolute path.")
    if not all(char.isalnum() or char in "/._-" for char in path):
        raise ValueError("An endpoint path holds no query, host or escape.")
    if ".." in path or "//" in path:
        # A parent segment is what a proxy resolves into a different endpoint.
        raise ValueError("An endpoint path does not climb or double a separator.")
    return "".join("\\" + char if char in "." else char for char in path)


async def rpc(
    session: ServiceTransport, credential: str, path: str, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    """One JSON-RPC call. The envelope is checked before anything inside it is believed."""
    request: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": uuid4().hex,
        "method": method,
        "params": params,
    }
    try:
        _, body = await session.request(credential, "POST", path, request)
    except TransportError as error:
        raise translate(error) from None
    if body.get("jsonrpc") != "2.0":
        raise McpFailure("mcp_response")
    if "error" in body:
        # A protocol-level error names a method or an argument, never a reason to retry.
        raise McpFailure("mcp_refused")
    result = body.get("result")
    if not isinstance(result, dict):
        raise McpFailure("mcp_response")
    return result


async def initialize(session: ServiceTransport, credential: str, path: str) -> str:
    """Announce the client and learn the server's protocol version, or stop here."""
    result = await rpc(
        session,
        credential,
        path,
        "initialize",
        {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT},
    )
    version = result.get("protocolVersion")
    if not isinstance(version, str) or not 1 <= len(version) <= 40:
        raise McpFailure("mcp_response")
    return version
