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


def proposable(risk: str) -> bool:
    """Whether a model may be shown this tool at all, decided once for every provider.

    A catalogue is built by each provider, so the question was answered twice and went
    stale twice: both were written when the levels were SAFE and CONFIRM, and neither was
    revisited when ROUTINE arrived between them. The assistant kept its ability to type
    text, write a file and drive the browser, and simply stopped offering it to the model.

    The answer lives here, next to the levels themselves, so a level added later cannot be
    forgotten in two files. It is stated as a list of what may be shown rather than what may
    not: an unknown level is then hidden from the model instead of offered to it.
    """
    return risk in (Risk.SAFE, Risk.ROUTINE, Risk.CONFIRM)
