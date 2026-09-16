"""Shared API transport: fixed origin, allowlisted routes, bounded single-shot requests.

Generalised from the Outlook Graph transport. Every connector that speaks HTTP reuses it,
so no service can widen the surface on its own: the origin is fixed at construction, the
route table is trusted code, and nothing here follows redirects, keeps cookies, reads
environment proxies, or retries. A retry is a decision for the workflow layer, which owns
idempotency keys; a transport that retries by itself can duplicate an external effect.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import aiohttp

from jarvis.browser.network import tls_context

Method = Literal["GET", "POST", "PATCH", "PUT", "DELETE"]
ORIGIN = re.compile(r"https://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?:/[A-Za-z0-9._~-]+)*")

Code = Literal[
    "route_denied",
    "credentials",
    "forbidden",
    "not_found",
    "conflict",
    "rate_limited",
    "rejected",
    "server",
    "redirect",
    "response_limit",
    "response_invalid",
    "response_unparsable",
    "network",
]


class TransportError(Exception):
    """Finite categories only; server text never crosses this boundary."""

    def __init__(self, code: Code) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Route:
    """One allowed request shape. `path` is a regular expression matched in full."""

    method: Method
    path: str

    def __post_init__(self) -> None:
        if self.method not in ("GET", "POST", "PATCH", "PUT", "DELETE"):
            raise ValueError("Unsupported method.")
        if not self.path.startswith("/") or len(self.path) > 300:
            raise ValueError("Route path must be a bounded absolute pattern.")
        re.compile(self.path)


def bearer(credential: str) -> str:
    return "Bearer " + credential


def printable(value: str, limit: int) -> bool:
    return 0 < len(value) <= limit and all(32 <= ord(char) <= 126 for char in value)


def status_code(status: int) -> Code:
    if 300 <= status < 400:
        return "redirect"
    if status == 401:
        return "credentials"
    if status == 403:
        return "forbidden"
    if status == 404:
        return "not_found"
    if status == 409:
        return "conflict"
    if status == 429:
        return "rate_limited"
    if 400 <= status < 500:
        return "rejected"
    if status >= 500:
        return "server"
    return "rejected"


class ServiceTransport:
    def __init__(
        self,
        base_url: str,
        routes: tuple[Route, ...],
        *,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 12,
        max_response_bytes: int = 262144,
        success_statuses: tuple[int, ...] = (200, 201, 202, 204),
        authorization: str = "bearer",
        allow_compression: bool = False,
    ) -> None:
        if not ORIGIN.fullmatch(base_url) or "@" in base_url or base_url.endswith("/"):
            raise ValueError("Base URL must be a fixed https origin without credentials.")
        if not routes:
            raise ValueError("A transport without routes can reach nothing.")
        for name, value in (headers or {}).items():
            # Authorization is built here; a per-connector header may never carry identity.
            if name.title() in ("Authorization", "Cookie", "Proxy-Authorization", "Host"):
                raise ValueError("Identity headers are not connector configuration.")
            if not printable(name, 64) or not printable(value, 300):
                raise ValueError("Invalid header.")
        if not 0 < timeout_seconds <= 60:
            raise ValueError("Timeout must be between 0 and 60 seconds.")
        if not 1024 <= max_response_bytes <= 4194304:
            raise ValueError("Response limit is out of range.")
        if not success_statuses or any(not 200 <= s < 300 for s in success_statuses):
            raise ValueError("Success statuses must be 2xx.")
        if authorization != "bearer":
            raise ValueError("Unsupported authorization scheme.")
        self.base_url = base_url
        self.routes = routes
        self.headers = dict(headers or {})
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.success_statuses = success_statuses
        self.allow_compression = allow_compression

    def allows(self, method: str, path: str) -> bool:
        return any(
            route.method == method and re.fullmatch(route.path, path) is not None
            for route in self.routes
        )

    async def request(
        self,
        credential: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        if not self.allows(method, path):
            raise TransportError("route_denied")
        if not printable(credential, 8192):
            raise TransportError("credentials")
        for key, value in (params or {}).items():
            if not printable(key, 64) or not printable(value, 500):
                raise TransportError("route_denied")
        headers = dict(self.headers)
        headers["Authorization"] = bearer(credential)
        if not self.allow_compression:
            headers["Accept-Encoding"] = "identity"
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
                    trust_env=False,
                    cookie_jar=aiohttp.DummyCookieJar(),
                    connector=aiohttp.TCPConnector(ssl=tls_context(), force_close=True),
                    auto_decompress=self.allow_compression,
                ) as session,
                session.request(
                    method,
                    self.base_url + path,
                    headers=headers,
                    json=payload,
                    params=dict(params) if params else None,
                    allow_redirects=False,
                ) as response,
            ):
                return response.status, await self._body(response)
        except TransportError:
            raise
        except Exception:
            raise TransportError("network") from None

    async def _body(self, response: aiohttp.ClientResponse) -> dict[str, object]:
        raw = bytearray()
        async for chunk in response.content.iter_chunked(16384):
            raw.extend(chunk)
            if len(raw) > self.max_response_bytes:
                raise TransportError("response_limit")
        if response.status not in self.success_statuses:
            raise TransportError(status_code(response.status))
        if not self.allow_compression and response.headers.get("Content-Encoding"):
            raise TransportError("response_invalid")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except Exception:
            raise TransportError("response_unparsable") from None
        if not isinstance(data, dict):
            raise TransportError("response_invalid")
        return data
