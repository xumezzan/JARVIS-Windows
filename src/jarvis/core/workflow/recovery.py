"""What to do after a step fails.

Two rules come before every category. A step that may already have reached a service is
never retried automatically — a timeout is not proof that nothing happened, and repeating
it is how one task becomes two. A step whose tool did not declare itself idempotent is
never retried either, for the same reason.

Only after both hold does the error category matter, and even then the answer is one of
three plain words: try it again, ask the owner, or stop and report what did happen.
"""

from typing import Literal

from jarvis.observability.audit import ErrorCode

Recovery = Literal["retry", "ask", "stop"]

DEFAULT_LIMIT = 2

# Conditions that pass on their own: the service was slow or briefly unavailable.
TRANSIENT = frozenset(
    {
        ErrorCode.TIMEOUT,
        ErrorCode.NATIVE_TIMEOUT,
        ErrorCode.BROWSER_TIMEOUT,
        ErrorCode.BROWSER_UNAVAILABLE,
        ErrorCode.EXECUTION,
    }
)

# Conditions only the owner can clear: approve again, open the application, allow the folder.
OWNER = frozenset(
    {
        ErrorCode.APPROVAL,
        ErrorCode.APPLICATION_MISSING,
        ErrorCode.APPLICATION_AMBIGUOUS,
        ErrorCode.PATH_DENIED,
        ErrorCode.FILE_MISSING,
        ErrorCode.FILE_CONFLICT,
    }
)


def decide(
    error: ErrorCode,
    *,
    idempotent: bool,
    may_have_effects: bool,
    attempts: int,
    limit: int = DEFAULT_LIMIT,
) -> Recovery:
    """`attempts` counts the tries already made, including the one that just failed."""
    if not isinstance(error, ErrorCode) or error is ErrorCode.NONE:
        raise ValueError("A successful step has nothing to recover.")
    if error in OWNER:
        return "ask"
    if may_have_effects or not idempotent:
        # The world may already have changed; only a human may decide to do it again.
        return "stop"
    if error in TRANSIENT and attempts < max(1, limit):
        return "retry"
    return "stop"
