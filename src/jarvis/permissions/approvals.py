"""Opaque capabilities: exact snapshot, UI-only issuer, atomic consume, no actor strings."""

import hashlib
import json
import math
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock
from time import monotonic
from uuid import UUID

from jarvis.permissions.policies import Mode, Risk


@dataclass(frozen=True)
class Action:
    request_id: UUID
    tool: str
    payload: str = field(repr=False)
    risk: Risk
    mode: Mode

    @property
    def signature(self) -> str:
        content = json.dumps(
            [str(self.request_id), self.tool, self.payload, self.risk.value, self.mode.value],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(content.encode()).hexdigest()


class Channel(StrEnum):
    """Which verified event issued the approval. Recorded for audit; it grants nothing."""

    UI = "ui"
    VOICE = "voice"


@dataclass(frozen=True)
class ApprovalToken:
    secret: str = field(repr=False)
    channel: Channel = Channel.UI


@dataclass
class _Grant:
    signature: str
    expires_at: float


class ApprovalAuthority:
    """A capability handed only to trusted UI composition, never to tool discovery/planners."""

    def __init__(
        self,
        approve: Callable[[Action, Channel], ApprovalToken],
        remaining: Callable[[Action], float],
    ) -> None:
        self._approve = approve
        self._remaining = remaining

    def approve(self, action: Action, channel: Channel = Channel.UI) -> ApprovalToken:
        return self._approve(action, channel)

    def remaining(self, action: Action) -> float:
        """Seconds left to answer, so the owner can be told. Reading is not approving."""
        return self._remaining(action)


class ApprovalStore:
    def __init__(self, ttl: float = 60, clock: Callable[[], float] = monotonic) -> None:
        if not math.isfinite(ttl) or not 0 < ttl <= 60:
            raise ValueError("Approval lifetime must be at most 60 seconds.")
        self._ttl = ttl
        self._clock = clock
        self._pending: dict[UUID, tuple[Action, float]] = {}
        self._tokens: dict[str, _Grant] = {}
        self._lock = RLock()
        self._issuer_taken = False

    def take_authority(self, on_issue: Callable[[Action, Channel], None]) -> ApprovalAuthority:
        """Composition root calls once; issuance must durably audit before returning a token."""
        with self._lock:
            if self._issuer_taken:
                raise ValueError("Approval issuer already assigned.")
            self._issuer_taken = True

        def issue(action: Action, channel: Channel) -> ApprovalToken:
            with self._lock:
                self._purge()
                pending = self._pending.get(action.request_id)
                if pending is None or pending[0] != action or action.risk is not Risk.CONFIRM:
                    raise ValueError("Approval request is unavailable or changed.")
                if not isinstance(channel, Channel):
                    raise ValueError("Unknown approval channel.")
                on_issue(action, channel)
                self._pending.pop(action.request_id)
                token = ApprovalToken(secrets.token_urlsafe(32), channel)
                self._tokens[token.secret] = _Grant(action.signature, pending[1])
                return token

        def remaining(action: Action) -> float:
            with self._lock:
                pending = self._pending.get(action.request_id)
                if pending is None or pending[0] != action:
                    return 0
                return max(0.0, pending[1] - self._clock())

        return ApprovalAuthority(issue, remaining)

    def request(self, action: Action) -> None:
        with self._lock:
            self._purge()
            if action.request_id in self._pending:
                raise ValueError("Approval request already exists.")
            self._pending[action.request_id] = (action, self._clock() + self._ttl)

    def consume(self, token: ApprovalToken | None, action: Action) -> bool:
        with self._lock:
            self._purge()
            if not isinstance(token, ApprovalToken):
                return False
            # Even a mismatch consumes the presented capability: changing it back cannot reuse it.
            grant = self._tokens.pop(token.secret, None)
            return grant is not None and secrets.compare_digest(grant.signature, action.signature)

    def is_valid(self, token: ApprovalToken | None, action: Action) -> bool:
        with self._lock:
            self._purge()
            if not isinstance(token, ApprovalToken):
                return False
            grant = self._tokens.get(token.secret)
            valid = grant is not None and secrets.compare_digest(grant.signature, action.signature)
            if not valid:
                self._tokens.pop(token.secret, None)
            return valid

    def invalidate(self, action: Action) -> None:
        with self._lock:
            self._pending.pop(action.request_id, None)
            self._tokens = {
                token: grant
                for token, grant in self._tokens.items()
                if grant.signature != action.signature
            }

    def _purge(self) -> None:
        now = self._clock()
        self._pending = {key: value for key, value in self._pending.items() if value[1] > now}
        self._tokens = {key: value for key, value in self._tokens.items() if value.expires_at > now}
