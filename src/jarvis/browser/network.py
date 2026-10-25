"""Only network transport: validated DNS answers, no cookies/auth/proxy/redirects/retries."""

import socket
import ssl
from dataclasses import dataclass
from importlib.metadata import version

import aiohttp
import certifi
from aiohttp.abc import AbstractResolver, ResolveResult
from yarl import URL

from jarvis.security.browser_policy import NetworkPolicy
from jarvis.tools.base import ToolError
from jarvis.tools.browser import RequestIntent

# Name the client honestly: an unidentified request is refused by common origins.
# This never imitates a browser and never answers an anti-bot challenge.
USER_AGENT = f"Jarvis/{version('jarvis-windows')} (+local assistant)"


class PolicyResolver(AbstractResolver):
    def __init__(self, policy: NetworkPolicy) -> None:
        self.policy = policy

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[ResolveResult]:
        return [
            ResolveResult(
                hostname=host,
                host=ip,
                port=port,
                family=af,
                proto=socket.IPPROTO_TCP,
                flags=socket.AI_NUMERICHOST,
            )
            for af, ip in await self.policy.resolve(host, port)
        ]

    async def close(self) -> None:
        pass


@dataclass(frozen=True)
class DocumentResponse:
    body: bytes
    content_type: str
    status: int


def tls_context() -> ssl.SSLContext:
    # Explicit construction avoids create_default_context's SSLKEYLOGFILE side effect.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_default_certs()
    if not context.get_ca_certs():
        # Some python.org macOS installations have no CA bundle. Preserve native roots
        # when present; otherwise use Mozilla roots, never disable certificate checks.
        context.load_verify_locations(cafile=certifi.where())
    return context


async def fetch(intent: RequestIntent, policy: NetworkPolicy) -> DocumentResponse:
    url = policy.validate_request(intent.url, intent.method, intent.body)
    if intent.method == "GET" and intent.body:
        raise ToolError("network_denied")
    connector = aiohttp.TCPConnector(
        resolver=PolicyResolver(policy), use_dns_cache=False, force_close=True, ssl=tls_context()
    )
    sent = False

    async def once(
        request: aiohttp.ClientRequest, handler: aiohttp.ClientHandlerType
    ) -> aiohttp.ClientResponse:
        nonlocal sent
        if sent:
            raise ToolError("network_denied")
        sent = True
        return await handler(request)

    async with (
        aiohttp.ClientSession(
            connector=connector,
            cookie_jar=aiohttp.DummyCookieJar(),
            trust_env=False,
            timeout=aiohttp.ClientTimeout(total=8),
            auto_decompress=False,
            middlewares=[once],
        ) as session,
        session.request(
            intent.method,
            URL(url, encoded=True),
            data=intent.body.encode("utf-8") if intent.method == "POST" else None,
            headers={
                "Accept": "text/html",
                "Accept-Encoding": "identity",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
            allow_redirects=False,
        ) as response,
    ):
        if 300 <= response.status < 400:
            raise ToolError("network_denied")
        # Only a complete 200 document is an observation. A search origin answers an
        # anti-bot challenge with 202, and 204/206 carry no full document; none of them
        # may be presented as the page the user asked for.
        if response.status != 200:
            raise ToolError("browser_failure")
        content_type = response.headers.get("Content-Type", "")
        if not content_type.lower().startswith("text/html") or response.headers.get(
            "Content-Encoding"
        ):
            raise ToolError("network_denied")
        data = bytearray()
        async for chunk in response.content.iter_chunked(16384):
            data.extend(chunk)
            if len(data) > 1_000_000:
                raise ToolError("browser_failure")
        return DocumentResponse(bytes(data), content_type, response.status)
