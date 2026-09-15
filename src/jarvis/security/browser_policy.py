"""Exact-origin network policy. Test-only loopback capability is never a tool argument."""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from jarvis.tools.base import ToolError

DEFAULT_ORIGINS = (
    "https://example.com",
    "https://www.python.org",
    "https://docs.python.org",
    "https://html.duckduckgo.com",
)


def normalized_url(value: str) -> str:
    try:
        if (
            not value
            or len(value) > 6000
            or "\\" in value
            or any(ord(char) < 33 or ord(char) > 126 for char in value)
        ):
            raise ValueError
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
            or "%" in parts.netloc
            or parts.hostname.endswith(".")
        ):
            raise ValueError
        host = parts.hostname.lower()
        port = parts.port
        if port is not None and port <= 0:
            raise ValueError
        if ":" in host:
            host = f"[{host}]"
        authority = host + (
            f":{port}" if port and port != (443 if parts.scheme == "https" else 80) else ""
        )
        return urlunsplit((parts.scheme, authority, parts.path or "/", parts.query, ""))
    except ValueError:
        raise ToolError("network_denied") from None


def origin(value: str) -> str:
    parts = urlsplit(normalized_url(value))
    return f"{parts.scheme}://{parts.netloc}"


@dataclass(frozen=True)
class NetworkPolicy:
    allowed_origins: tuple[str, ...] = DEFAULT_ORIGINS
    fixture_origin: str | None = None

    def __post_init__(self) -> None:
        for item in self.allowed_origins:
            if origin(item) != item or urlsplit(item).scheme != "https":
                raise ValueError("Allowlist requires exact HTTPS origins.")
        if self.fixture_origin is not None:
            parts = urlsplit(self.fixture_origin)
            if (
                parts.scheme != "http"
                or parts.hostname != "127.0.0.1"
                or not parts.port
                or origin(self.fixture_origin) != self.fixture_origin
            ):
                raise ValueError("Fixture capability requires an exact IPv4 loopback port.")

    def validate(self, value: str) -> str:
        url = normalized_url(value)
        current = origin(url)
        if self.fixture_origin is not None and current == self.fixture_origin:
            return url
        if current not in self.allowed_origins or urlsplit(url).scheme != "https":
            raise ToolError("network_denied")
        host = urlsplit(url).hostname
        assert host is not None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                raise ToolError("network_denied") from None
        else:
            if not public_address(address):
                raise ToolError("network_denied")
        return url

    async def resolve(self, host: str, port: int) -> list[tuple[int, str]]:
        answers = await asyncio.get_running_loop().getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
        result: list[tuple[int, str]] = []
        for family, _, _, _, address in answers:
            ip = str(address[0])
            # Validation happens on the addresses handed to the connector, not a separate lookup.
            if not public_address(ipaddress.ip_address(ip)):
                raise ToolError("network_denied")
            result.append((family, ip))
        if not result:
            raise ToolError("network_denied")
        return result

    def validate_request(self, url: str, method: str, body: str = "") -> str:
        result = self.validate(url)
        if method == "GET" and not body:
            return result
        # Generic real-world POST effects cannot be classified safely by DOM labels.
        # External writes need a later service-specific registered adapter.
        if (
            method == "POST"
            and self.fixture_origin is not None
            and origin(result) == self.fixture_origin
            and urlsplit(result).path == "/submit"
        ):
            return result
        raise ToolError("network_denied")


def public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address.is_global and not (
        address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
    )
