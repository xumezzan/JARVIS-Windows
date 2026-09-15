"""One bounded asyncio execution in a Qt worker thread; UI only receives typed outcomes."""

import asyncio

from PySide6.QtCore import QObject, QThread

from jarvis.observability.audit import ErrorCode
from jarvis.permissions.approvals import Action, ApprovalToken
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Status


class ToolWorker(QThread):
    def __init__(
        self,
        engine: PermissionEngine,
        action: Action,
        token: ApprovalToken | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.action = action
        self._token = token
        self.outcome = Outcome(action.request_id, Status.ERROR, ErrorCode.EXECUTION)

    def run(self) -> None:
        try:
            self.outcome = asyncio.run(self.engine.execute(self.action, self._token))
        except Exception:
            self.outcome = Outcome(
                self.action.request_id,
                Status.ERROR,
                ErrorCode.EXECUTION,
                may_have_effects=True,
            )
        finally:
            self._token = None

    def cancel(self) -> None:
        self.engine.cancel(self.action)
