"""Common connector contract.

A connector describes what a service can do and how to reach it. It never becomes the
planner's interface: capabilities are turned into registered tools with strict schemas,
and every call still passes through the PermissionEngine. Capability metadata is trusted
code, never a value supplied by a model, a service response or a tool argument.
"""

import re
from dataclasses import dataclass
from typing import Literal, Protocol

from jarvis.permissions.policies import Risk

NAME = re.compile(r"[a-z][a-z0-9_]*")

EntityType = Literal[
    "person",
    "company",
    "project",
    "task",
    "meeting",
    "invoice",
    "document",
    "conversation",
    "customer",
    "account",
]


def tool_name(service: str, capability: str) -> str:
    """The registry's own name rule, so a capability cannot invent a tool namespace."""
    if not NAME.fullmatch(service) or not NAME.fullmatch(capability):
        raise ValueError("Invalid connector or capability name.")
    return f"{service}.{capability}"


@dataclass(frozen=True)
class Capability:
    """One declared operation. `idempotent` decides whether a retry may ever repeat it."""

    name: str
    description: str
    risk: Risk
    idempotent: bool
    reads: tuple[EntityType, ...] = ()
    writes: tuple[EntityType, ...] = ()

    def __post_init__(self) -> None:
        if not NAME.fullmatch(self.name):
            raise ValueError("Invalid capability name.")
        if not 1 <= len(self.description) <= 200:
            raise ValueError("Capability needs a short description.")
        if not isinstance(self.risk, Risk) or not isinstance(self.idempotent, bool):
            raise ValueError("Capability metadata must be trusted values.")
        if self.writes and self.risk is Risk.SAFE:
            raise ValueError("A writing capability is never SAFE.")


@dataclass(frozen=True)
class AuthState:
    state: Literal["connected", "disconnected"]
    account: str = ""  # Display identity only, never a token, cookie or session handle.

    def __post_init__(self) -> None:
        if len(self.account) > 254 or any(ord(c) < 32 for c in self.account):
            raise ValueError("Invalid account label.")


@dataclass(frozen=True)
class Health:
    state: Literal["ready", "unauthenticated", "unavailable"]
    reason: Literal["none", "credentials", "network", "configuration"] = "none"


class BaseConnector(Protocol):
    """Not every service implements every capability; each one reports its own."""

    service: str

    def capabilities(self) -> tuple[Capability, ...]: ...

    async def authenticate(self) -> AuthState: ...

    async def health_check(self) -> Health: ...


class ConnectorRegistry:
    """Connection inventory for the UI. Holds no credentials and executes no work."""

    def __init__(self) -> None:
        self._connectors: dict[str, BaseConnector] = {}

    def add(self, connector: BaseConnector) -> None:
        if not NAME.fullmatch(connector.service):
            raise ValueError("Invalid connector name.")
        if connector.service in self._connectors:
            raise ValueError("Duplicate connector.")
        names = [capability.name for capability in connector.capabilities()]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate capability.")
        self._connectors[connector.service] = connector

    def get(self, service: str) -> BaseConnector | None:
        return self._connectors.get(service)

    def services(self) -> tuple[str, ...]:
        return tuple(self._connectors)

    def describe(self) -> list[dict[str, object]]:
        return [
            {
                "service": connector.service,
                "capabilities": [
                    {
                        "name": capability.name,
                        "tool": tool_name(connector.service, capability.name),
                        "description": capability.description,
                        "risk": capability.risk.value,
                        "idempotent": capability.idempotent,
                        "reads": list(capability.reads),
                        "writes": list(capability.writes),
                    }
                    for capability in connector.capabilities()
                ],
            }
            for connector in self._connectors.values()
        ]
