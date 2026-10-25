"""One account operation, with the audit records it must leave behind.

Connecting an account, adding a surface to it and disconnecting are not tool calls: they
pass no arguments through the permission engine and produce no snapshot. They still have to
be in the audit log, because they are the moments when what the assistant can reach changes.

This lives on its own so that every screen offering those buttons writes the same two
records. A second screen that wrote its own would be a second definition of what happened,
and the first time they disagreed the log would stop being evidence.

A log that cannot be written ends the operation: an account the audit cannot account for is
detached rather than kept.
"""

import asyncio
from collections.abc import Awaitable, Callable
from threading import Event
from uuid import uuid4

from PySide6.QtCore import QThread

from jarvis.mail.credentials import MailFailure
from jarvis.observability.audit import AuditEvent, AuditKind, AuditLog, ErrorCode
from jarvis.permissions.policies import Decision, Mode, Status
from jarvis.tools.base import ExecutionContext

TOOL = "outlook.account"


def audited(
    audit: AuditLog,
    work: Callable[[ExecutionContext], Awaitable[object]],
    on_audit_failure: Callable[[], None],
) -> Callable[[ExecutionContext], Awaitable[object]]:
    """The same work, bracketed by the two records that say it was asked for and how it ended."""

    async def run(context: ExecutionContext) -> object:
        request_id = uuid4()
        try:
            audit.write(
                AuditEvent(
                    AuditKind.STARTED,
                    request_id,
                    TOOL,
                    None,
                    Mode.EXECUTE,
                    Decision.ALLOW,
                    actor="user_ui_connection",
                )
            )
        except Exception:
            raise MailFailure("mail_audit") from None
        status = Status.ERROR
        try:
            result = await work(context)
            status = Status.SUCCESS
            return result
        except asyncio.CancelledError:
            status = Status.CANCELLED
            raise
        finally:
            try:
                audit.write(
                    AuditEvent(
                        AuditKind.FINISHED,
                        request_id,
                        TOOL,
                        None,
                        Mode.EXECUTE,
                        Decision.ALLOW,
                        status,
                        error=ErrorCode.NONE if status is Status.SUCCESS else ErrorCode.EXECUTION,
                        may_have_effects=True,
                        actor="user_ui_connection",
                    )
                )
            except Exception:
                on_audit_failure()
                raise MailFailure("mail_audit") from None

    return run


class AccountWorker(QThread):
    """One account operation off the interface thread, cancellable between its steps.

    Sign-in waits for a person in a browser, so the budget here is minutes rather than
    seconds, and the wait is watched rather than blocked: a cancelled operation stops
    without leaving a thread holding the window open.
    """

    def __init__(self, work: Callable[[ExecutionContext], Awaitable[object]]) -> None:
        super().__init__()
        self.work = work
        self.cancelled = Event()
        self.result: object = None
        self.error = ""

    def cancel(self) -> None:
        self.cancelled.set()

    def run(self) -> None:
        async def run() -> object:
            job = asyncio.ensure_future(self.work(ExecutionContext(self.cancelled)))

            async def watch() -> None:
                while not self.cancelled.is_set():
                    await asyncio.sleep(0.02)

            watcher = asyncio.create_task(watch())
            try:
                done, _ = await asyncio.wait(
                    (job, watcher), timeout=160, return_when=asyncio.FIRST_COMPLETED
                )
                if job in done:
                    return await job
                raise MailFailure("mail_cancelled")
            finally:
                job.cancel()
                watcher.cancel()
                await asyncio.gather(job, watcher, return_exceptions=True)

        try:
            self.result = asyncio.run(run())
        except MailFailure as error:
            self.error = error.code
        except Exception:
            self.error = "mail_unavailable"
