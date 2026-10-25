"""What an interrupted task is allowed to remember about itself.

A run survives the process; its observations do not. The journal keeps identity and
outcome — which tool ran, under which key, with what status — and deliberately never keeps
arguments, results or recipients, because a record kept to make retrying safe must not
become a second copy of the user's content. Resuming therefore restores facts, not sight:
the task knows that it opened the calendar and that the call succeeded, and it does not
know what the calendar said.

That has a consequence worth stating plainly, because it looks like a limitation and is
actually the invariant. A target observed before the restart is not observed now, so the
resumed task must look again before it may act on anything (invariant И5 in `AGENTS.md`).
`Runner._observed_target` enforces this on its own: a restored step carries no result, so
nothing can be addressed through it.

A step whose outcome never arrived is restored as a timeout that may have had effects. It
is the honest reading — a service may well have accepted the work — and it keeps the
duplicate guard in front of the same key on the next attempt.
"""

from uuid import UUID

from jarvis.core.planner.contracts import Step
from jarvis.core.workflow.models import RunRecord
from jarvis.observability.audit import ErrorCode
from jarvis.permissions.engine import Outcome
from jarvis.permissions.policies import Status

# A restored step ran in a process that is gone, so no live request stands behind it. The
# nil identifier says that, rather than inventing a plausible one that never existed.
EARLIER_PROCESS = UUID(int=0)


def restore(record: RunRecord) -> tuple[Step, ...]:
    """The steps of an unfinished run, as the resumed task may know them."""
    return tuple(
        Step(
            step.tool,
            Outcome(
                EARLIER_PROCESS,
                Status.TIMEOUT if step.unresolved else Status(step.status),
                ErrorCode.TIMEOUT if step.unresolved else ErrorCode(step.error),
                # An unresolved step counts as possibly done; that is the whole point of
                # writing it before the call rather than after it.
                True if step.unresolved else step.may_have_effects,
            ),
        )
        for step in record.steps
    )


__all__ = ["EARLIER_PROCESS", "restore"]
