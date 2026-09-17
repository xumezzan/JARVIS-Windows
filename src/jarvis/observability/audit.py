"""Durable, finite-schema SQLite tool audit with no payloads or bearer tokens."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4

from jarvis.permissions.approvals import Action, Channel
from jarvis.permissions.policies import Decision, Mode, Risk, Status


class AuditKind(StrEnum):
    PREPARED = "prepared"
    APPROVED = "approved"
    STARTED = "started"
    FINISHED = "finished"


class ErrorCode(StrEnum):
    NONE = "none"
    INVALID_REQUEST = "invalid_request"
    POLICY = "policy_denied"
    APPROVAL = "approval_invalid"
    REPLAY = "request_unavailable"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    PRECONDITION = "precondition_failed"
    EXECUTION = "execution_failed"
    VERIFICATION = "verification_failed"
    AUDIT = "audit_unavailable"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    APPLICATION_MISSING = "application_missing"
    APPLICATION_AMBIGUOUS = "application_ambiguous"
    TARGET_CHANGED = "target_changed"
    CONTROL_UNSUPPORTED = "control_unsupported"
    NATIVE_TIMEOUT = "native_timeout"
    NATIVE_FAILURE = "native_failure"
    NETWORK_DENIED = "network_denied"
    PAGE_CHANGED = "page_changed"
    BROWSER_UNAVAILABLE = "browser_unavailable"
    BROWSER_TIMEOUT = "browser_timeout"
    BROWSER_FAILURE = "browser_failure"
    BROWSER_CLEANUP = "browser_cleanup"
    PATH_DENIED = "path_denied"
    FILE_MISSING = "file_missing"
    FILE_CONFLICT = "file_conflict"
    FILE_UNSUPPORTED = "file_unsupported"
    FILE_TOO_LARGE = "file_too_large"
    FILE_FAILURE = "file_failure"


@dataclass(frozen=True)
class AuditEvent:
    kind: AuditKind
    request_id: UUID
    tool: str  # Only registered tool names; untrusted/unknown names become "unknown".
    risk: Risk | None
    mode: Mode
    decision: Decision
    status: Status | None = None
    duration_ms: int = 0
    error: ErrorCode = ErrorCode.NONE
    may_have_effects: bool = False
    actor: str = "permission_engine"


class AuditLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.session_id = uuid4()
        self._lock = Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, record TEXT NOT NULL)"
        )
        self._connection.commit()

    def write(self, event: AuditEvent) -> None:
        # Payload/content/account/recipient/result text are omitted, rather than regex-redacted.
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": str(self.session_id),
            "request_id": str(event.request_id),
            "actor": event.actor,
            "tool": event.tool,
            "arguments": "[REDACTED]",
            "risk": event.risk.value if event.risk else None,
            "mode": event.mode.value,
            "permission_decision": event.decision.value,
            "event": event.kind.value,
            "status": event.status.value if event.status else None,
            "duration_ms": event.duration_ms,
            "error": event.error.value,
            "result_summary": event.status.value if event.status else event.kind.value,
            "may_have_effects": event.may_have_effects,
        }
        with self._lock, self._connection:
            self._connection.execute("INSERT INTO events(record) VALUES (?)", (json.dumps(record),))

    def approved(self, action: Action, channel: Channel) -> None:
        # The channel is recorded because voice is the weakest of them: an approval that
        # arrived by microphone has to be distinguishable afterwards from a button press.
        self.write(
            AuditEvent(
                AuditKind.APPROVED,
                action.request_id,
                action.tool,
                action.risk,
                action.mode,
                Decision.REQUIRE_APPROVAL,
                actor="user_" + channel.value,
            )
        )

    def recent(self, limit: int = 200) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT record FROM events ORDER BY id DESC LIMIT ?", (max(1, min(limit, 1000)),)
            ).fetchall()
        return [str(row[0]) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()
