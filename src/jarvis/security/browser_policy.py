"""Exact-origin network policy. Test-only loopback capability is never a tool argument."""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from jarvis.tools.base import ToolError

DEFAULT_ORIGINS = (
    "https://example.com",
    "https://www.python.org",
    "https://docs.python.org",
    "https://lite.duckduckgo.com",
)

Tier = Literal["ordinary", "read_only", "blocked"]

# A password manager is the one place an assistant holding the owner's live sessions must
# never reach. Everything it stores is exactly what this product promises never to see, so
# it is refused before anything else is considered - by navigation, by a link on a page and
# by a redirect alike, because all three arrive at the same check.
PASSWORD_MANAGERS = (
    "1password.com",
    "1password.eu",
    "1password.ca",
    "bitwarden.com",
    "lastpass.com",
    "dashlane.com",
    "keepersecurity.com",
)

# Money, infrastructure and other people's permissions. These are `CRITICAL` in the risk
# matrix, and `CRITICAL` stays disabled until the reinforced confirmation of decision Р5
# exists, so until then the assistant may look at them and change nothing.
DEFAULT_READ_ONLY = (
    "intuit.com",
    "quickbooks.com",
    "console.aws.amazon.com",
    "aws.amazon.com",
    "admin.microsoft.com",
    "admin.cloud.microsoft",
    "portal.azure.com",
)


def covers(domain: str, host: str) -> bool:
    """Whether a listed domain covers this host, including its subdomains.

    Matching is deliberately looser here than in the allowlist, and the asymmetry is the
    point: an allowlist matched loosely would grant more than the owner allowed, while a
    denial matched loosely denies more than the owner named. Only one of those errs safely.
    """
    domain = domain.lower().strip(".")
    return bool(domain) and (host == domain or host.endswith("." + domain))


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
    # Named by domain rather than by exact origin, so a console that lives on a regional
    # subdomain is covered without listing every region by hand.
    blocked_domains: tuple[str, ...] = PASSWORD_MANAGERS
    read_only_domains: tuple[str, ...] = field(default=DEFAULT_READ_ONLY)

    def __post_init__(self) -> None:
        for item in self.allowed_origins:
            if origin(item) != item or urlsplit(item).scheme != "https":
                raise ValueError("Allowlist requires exact HTTPS origins.")
        for group in (self.blocked_domains, self.read_only_domains):
            for name in group:
                if not name or name != name.lower().strip(".") or "/" in name or ":" in name:
                    raise ValueError("A tier lists bare lowercase domains.")
        if self.fixture_origin is not None:
            parts = urlsplit(self.fixture_origin)
            if (
                parts.scheme != "http"
                or parts.hostname != "127.0.0.1"
                or not parts.port
                or origin(self.fixture_origin) != self.fixture_origin
            ):
                raise ValueError("Fixture capability requires an exact IPv4 loopback port.")

    def tier(self, value: str) -> Tier:
        """What this site is allowed to be, before any question of which operation it is."""
        host = urlsplit(normalized_url(value)).hostname
        assert host is not None
        if any(covers(name, host) for name in self.blocked_domains):
            return "blocked"
        if any(covers(name, host) for name in self.read_only_domains):
            return "read_only"
        return "ordinary"

    def validate(self, value: str) -> str:
        url = normalized_url(value)
        current = origin(url)
        # Checked before the allowlist and before the fixture, so no later capability can
        # reopen what this refuses.
        if self.tier(url) == "blocked":
            raise ToolError("network_denied")
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

    def writable(self, value: str) -> bool:
        """Whether this site may be changed at all, whatever the operation asks for."""
        return self.tier(value) == "ordinary"

    def validate_request(self, url: str, method: str, body: str = "") -> str:
        result = self.validate(url)
        if method == "GET" and not body:
            return result
        if not self.writable(result):
            # Reading such a site is looking at it; sending it anything is not.
            raise ToolError("network_denied")
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
