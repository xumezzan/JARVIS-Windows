"""Opt-in local event detection. Notifications are hints, never planner authority."""

import asyncio
import hashlib
import re
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic, time
from typing import Literal
from uuid import uuid4

from jarvis.mail.models import Account, ReadInput, validate_address

CLIPBOARD_LIMIT = 16000
MAIL_INTERVAL = 300
NOTICE_TTL = 300


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def clipboard_kind(text: str) -> Literal["error", "foreign"] | None:
    if not 300 <= len(text) <= CLIPBOARD_LIMIT:
        return None
    # Require a stack structure as well as an error marker; prose alone is insufficient.
    if len(text.splitlines()) >= 4 and (
        "Traceback (most recent call last):" in text
        or (
            re.search(r"(?:Exception|Error|panic:|Caused by:)", text)
            and len(re.findall(r"(?m)^\s*(?:at |File \"|#\d+ )", text)) >= 2
        )
    ):
        return "error"
    letters = [char for char in text if char.isalpha()]
    foreign = sum(not ("а" <= char.lower() <= "я" or char.lower() == "ё") for char in letters)
    if len(letters) >= 250 and foreign / len(letters) >= 0.85:
        return "foreign"
    return None


@dataclass
class WatchNotice:
    kind: str
    text: str
    id: str = field(default_factory=lambda: uuid4().hex)
    expires_at: float = field(default_factory=lambda: time() + NOTICE_TTL)
    digest: str = field(default="", repr=False)
    account: Account | None = field(default=None, repr=False)
    message_id: str = field(default="", repr=False)
    recipient: str = ""
    subject: str = ""

    def wire(self) -> dict[str, object]:
        return {
            key: getattr(self, key)
            for key in ("id", "kind", "text", "expires_at", "recipient", "subject")
        }


class EventWatcher:
    def __init__(
        self,
        clipboard: Callable[[], Awaitable[str]],
        mail: Callable[[], Awaitable[tuple[Account, list[dict[str, object]]] | None]],
        idle: Callable[[], bool],
        publish: Callable[[], None],
        speak: Callable[[str], None],
    ) -> None:
        self.clipboard, self.mail = clipboard, mail
        self.idle, self.publish, self.speak = idle, publish, speak
        self.clipboard_enabled = self.mail_enabled = self.speech_enabled = False
        self.task: asyncio.Task[None] | None = None
        self.notices: deque[WatchNotice] = deque(maxlen=3)
        self.clipboard_seen: deque[str] = deque(maxlen=32)
        self.clipboard_baseline = False
        self.clipboard_digest = ""
        self.mail_seen: deque[str] = deque(maxlen=256)
        self.account: Account | None = None
        self.mail_baseline = False
        self.mail_since = time()
        self.next_mail = self.next_clipboard = self.next_notice = 0.0
        self.status = ""

    async def configure(self, clipboard: bool, mail: bool, speech: bool) -> None:
        await self.pause()
        if clipboard != self.clipboard_enabled:
            self.clipboard_seen.clear()
            self.clipboard_baseline = False
            self.clipboard_digest = ""
            self.next_clipboard = 0
            self.notices = deque((n for n in self.notices if n.kind == "mail"), maxlen=3)
        if mail != self.mail_enabled:
            self.reset_mail()
        self.clipboard_enabled, self.mail_enabled, self.speech_enabled = clipboard, mail, speech
        self.resume()

    def reset_mail(self) -> None:
        self.account = None
        self.mail_seen.clear()
        self.mail_baseline = False
        self.mail_since = time()
        self.next_mail = 0
        self.notices = deque((n for n in self.notices if n.kind != "mail"), maxlen=3)
        self.status = ""

    def resume(self) -> None:
        if self.task is None and (self.clipboard_enabled or self.mail_enabled):
            self.task = asyncio.create_task(self._run())

    async def pause(self) -> None:
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        await self.pause()
        self.notices.clear()
        self.clipboard_seen.clear()
        self.reset_mail()

    def snapshot(self) -> dict[str, object]:
        return {
            "status": (
                "Фоновые проверки приостановлены до следующей операции."
                if self.task is None and (self.clipboard_enabled or self.mail_enabled)
                else self.status
            ),
            "notifications": [n.wire() for n in self.notices if n.expires_at > time()],
        }

    def get(self, notice_id: str) -> WatchNotice:
        for item in self.notices:
            if item.id == notice_id and item.expires_at > time():
                return item
        raise ValueError("watcher_expired")

    def dismiss(self, notice_id: str) -> None:
        self.notices = deque((n for n in self.notices if n.id != notice_id), maxlen=3)
        self.publish()

    def offer(self, notice: WatchNotice, *, urgent: bool = False) -> None:
        quiet = monotonic() < self.next_notice
        if quiet and not urgent:
            return
        if not quiet:
            self.next_notice = monotonic() + 60
        self.notices.appendleft(notice)
        self.publish()
        if not quiet and self.speech_enabled and self.idle():
            self.speak(notice.text)

    async def tick(self) -> None:
        expired = [n for n in self.notices if n.expires_at <= time()]
        for item in expired:
            self.dismiss(item.id)
        if not self.idle():
            return
        if self.clipboard_enabled and monotonic() >= self.next_clipboard:
            self.next_clipboard = monotonic() + 2
            text = await self.clipboard()
            digest = fingerprint(text)
            if digest != self.clipboard_digest:
                self.clipboard_digest = digest
                kind = (
                    clipboard_kind(text)
                    if self.clipboard_baseline and digest not in self.clipboard_seen
                    else None
                )
                self.clipboard_seen.append(digest)
                self.clipboard_baseline = True
                # A changed clipboard invalidates the old suggestion, even for an empty value.
                self.notices = deque((n for n in self.notices if n.kind == "mail"), maxlen=3)
                if kind:
                    self.offer(
                        WatchNotice(
                            kind,
                            (
                                "Вижу ошибку в буфере обмена, разобрать причину?"
                                if kind == "error"
                                else "В буфере большой текст на другом языке. Подготовить перевод?"
                            ),
                            digest=digest,
                        )
                    )
                self.publish()
        if self.mail_enabled and self.idle() and monotonic() >= self.next_mail:
            self.next_mail = monotonic() + MAIL_INTERVAL
            result = await self.mail()
            if result is None:
                self.reset_mail()
                self.next_mail = monotonic() + MAIL_INTERVAL
                return
            account, rows = result
            if self.account != account:
                self.reset_mail()
                self.account = account
                self.next_mail = monotonic() + MAIL_INTERVAL
            candidates: list[WatchNotice] = []
            for row in rows:
                key = row.get("id")
                if not isinstance(key, str) or key in self.mail_seen:
                    continue
                try:
                    ReadInput(account=account, message_id=key)
                    self.mail_seen.append(key)
                    if (
                        not self.mail_baseline
                        or row.get("importance") != "high"
                        or row.get("isRead") is not False
                    ):
                        continue
                    received = datetime.fromisoformat(
                        str(row.get("receivedDateTime")).replace("Z", "+00:00")
                    )
                    if received.tzinfo is None or received.timestamp() < self.mail_since:
                        continue
                    sender = row.get("from")
                    email = sender.get("emailAddress") if isinstance(sender, dict) else None
                    address = email.get("address") if isinstance(email, dict) else None
                    if not isinstance(address, str):
                        continue
                    validate_address(address)
                    subject = row.get("subject")
                    subject = subject if isinstance(subject, str) else ""
                    if len(subject) > 500 or re.search(r"[\r\n\x00]", subject):
                        continue
                    candidates.append(
                        WatchNotice(
                            "mail",
                            "Пришло важное письмо в Outlook. Подготовить черновик ответа?",
                            account=account,
                            message_id=key,
                            recipient=address,
                            subject=subject,
                        )
                    )
                except (ValueError, TypeError):
                    continue
            self.mail_baseline = True
            self.status = "Входящие проверены: " + datetime.now(UTC).isoformat(timespec="seconds")
            for notice in reversed(candidates[:3]):
                self.offer(notice, urgent=True)
            self.publish()

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.status = "Фоновая проверка недоступна. Следующая попытка — по расписанию."
                self.publish()
            await asyncio.sleep(1)
