"""Strict origin allowlist or public-HTTPS live policy; loopback fixtures stay test-only."""

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit, urlunsplit

from jarvis.tools.base import ToolError

if TYPE_CHECKING:
    from jarvis.workspace.models import WorkspaceProfile

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


class BrowserMode(StrEnum):
    STRICT = "strict"
    LIVE = "live"
    WORKSPACE = "authenticated_workspace"


def validate_cdp_endpoint(value: str) -> str:
    """Trusted configuration only; never let model/page data select a debugger."""
    try:
        parts = urlsplit(value)
        if (
            parts.scheme != "http"
            or parts.hostname not in ("localhost", "127.0.0.1", "::1")
            or not parts.port
            or parts.username is not None
            or parts.password is not None
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
            or "\\" in value
            or any(ord(char) < 33 or ord(char) > 126 for char in value)
        ):
            raise ValueError
    except ValueError:
        raise ValueError("CDP requires an HTTP loopback endpoint with an explicit port.") from None
    return value.rstrip("/")


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
    parts = urlsplit(normalized_url(value.split("#", 1)[0]))
    return f"{parts.scheme}://{parts.netloc}"


@dataclass(frozen=True)
class NetworkPolicy:
    allowed_origins: tuple[str, ...] = DEFAULT_ORIGINS
    fixture_origin: str | None = None
    # Named by domain rather than by exact origin, so a console that lives on a regional
    # subdomain is covered without listing every region by hand.
    blocked_domains: tuple[str, ...] = PASSWORD_MANAGERS
    read_only_domains: tuple[str, ...] = field(default=DEFAULT_READ_ONLY)
    mode: BrowserMode = BrowserMode.STRICT
    private_origins: tuple[str, ...] = ()

    @classmethod
    def from_workspace(
        cls,
        profile: "WorkspaceProfile",
        *,
        additional_origins: tuple[str, ...] = (),
        mode: BrowserMode | None = None,
    ) -> "NetworkPolicy":
        """Trusted startup only. App metadata never lowers site tiers or tool risks."""
        origins = dict.fromkeys(
            (
                *additional_origins,
                *(item for app in profile.apps if app.enabled for item in app.allowed_origins),
            )
        )
        selected = (
            BrowserMode.STRICT
            if profile.browser_mode == "strict_sandbox"
            else BrowserMode(profile.browser_mode)
        )
        return cls(
            allowed_origins=tuple(origins),
            mode=mode if mode is not None else selected,
            private_origins=tuple(
                dict.fromkeys(
                    item
                    for app in profile.apps
                    if app.enabled and app.allow_private_network
                    for item in app.allowed_origins
                )
            ),
        )

    def __post_init__(self) -> None:
        if not isinstance(self.mode, BrowserMode):
            raise ValueError("Invalid browser mode.")
        for item in self.allowed_origins:
            if origin(item) != item or urlsplit(item).scheme != "https":
                raise ValueError("Allowlist requires exact HTTPS origins.")
        for item in self.private_origins:
            if item not in self.allowed_origins or not corporate_origin(item):
                raise ValueError("Private access requires an explicitly allowed corporate domain.")
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
        fragment = ""
        if self.mode is not BrowserMode.STRICT:
            # Fragments are browser-local and needed by SPA routers.
            if len(value) > 6000 or any(ord(char) < 33 or ord(char) > 126 for char in value):
                raise ToolError("network_denied")
            value, separator, tail = value.partition("#")
            fragment = separator + tail
        url = normalized_url(value)
        current = origin(url)
        # Checked before the allowlist and before the fixture, so no later capability can
        # reopen what this refuses.
        if self.tier(url) == "blocked":
            raise ToolError("network_denied")
        if self.fixture_origin is not None and current == self.fixture_origin:
            return url + fragment
        if urlsplit(url).scheme != "https" or (
            self.mode is not BrowserMode.LIVE and current not in self.allowed_origins
        ):
            raise ToolError("network_denied")
        host = urlsplit(url).hostname
        assert host is not None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if (
                host == "localhost"
                or host.endswith(".localhost")
                or (host.endswith((".local", ".internal")) and current not in self.private_origins)
            ):
                raise ToolError("network_denied") from None
        else:
            if not public_address(address):
                raise ToolError("network_denied")
        return url + fragment

    async def validate_live_request(self, value: str, method: str = "GET") -> str:
        """Network-time preflight for each Chromium request, including redirects.

        Chromium owns TLS/cookies and resolves independently. This is not DNS pinning
        or an OS firewall; Strict Sandbox retains its pinned aiohttp connector.
        """
        url = self.validate(value)
        if not self.writable(url) and method not in ("GET", "HEAD", "OPTIONS"):
            raise ToolError("network_denied")
        parts = urlsplit(url)
        if self.fixture_origin is None or origin(url) != self.fixture_origin:
            assert parts.hostname is not None
            async with asyncio.timeout(5):
                await self.resolve(parts.hostname, parts.port or 443)
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
            resolved_address = ipaddress.ip_address(ip)
            authority = f"[{host}]" if ":" in host else host
            requested_origin = "https://" + authority + (f":{port}" if port != 443 else "")
            private_allowed = requested_origin in self.private_origins and any(
                resolved_address in network
                for network in CORPORATE_NETWORKS
                if resolved_address.version == network.version
            )
            if not public_address(resolved_address) and not private_allowed:
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
        if self.mode is not BrowserMode.STRICT and method == "POST":
            return result  # Exact observed form; registered browser.click remains CONFIRM.
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


CORPORATE_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


def corporate_origin(value: str) -> bool:
    """Exact HTTPS DNS name, never an IP, localhost, wildcard or metadata endpoint."""
    try:
        parts = urlsplit(normalized_url(value))
        host = parts.hostname or ""
        if origin(value) != value or parts.scheme != "https" or "*" in host or "." not in host:
            return False
        if host == "localhost" or host.endswith(".localhost"):
            return False
        if len(host) > 253 or not any("a" <= char <= "z" for char in host.rsplit(".", 1)[-1]):
            return False
        if any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.split(".")
        ):
            return False
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return True
        return False
    except ToolError:
        return False
