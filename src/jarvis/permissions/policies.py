"""Deterministic policy values, never supplied by tool-call arguments."""

from enum import StrEnum


class Risk(StrEnum):
    SAFE = "SAFE"
    CONFIRM = "CONFIRM"
    CRITICAL = "CRITICAL"
    BLOCKED = "BLOCKED"


class Mode(StrEnum):
    SIMULATION = "simulation"
    EXECUTE = "execute"


class Status(StrEnum):
    SUCCESS = "SUCCESS"
    SIMULATED = "SIMULATED"
    DENIED = "DENIED"
    INVALID = "INVALID"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


class Decision(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


def decide(risk: Risk) -> Decision:
    if risk is Risk.SAFE:
        return Decision.ALLOW
    if risk is Risk.CONFIRM:
        return Decision.REQUIRE_APPROVAL
    return Decision.DENY
