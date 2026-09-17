"""Deterministic policy values, never supplied by tool-call arguments."""

from enum import StrEnum


class Risk(StrEnum):
    SAFE = "SAFE"
    # Changes something, but reversibly and without leaving the owner's machine, so it runs
    # without approval. It is prepared, audited and journalled like every other action, and
    # the owner's matrix can raise it to CONFIRM.
    ROUTINE = "ROUTINE"
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
    if risk in (Risk.SAFE, Risk.ROUTINE):
        return Decision.ALLOW
    if risk is Risk.CONFIRM:
        return Decision.REQUIRE_APPROVAL
    return Decision.DENY
