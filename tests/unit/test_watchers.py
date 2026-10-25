"""No clipboard, credentials, network, microphone or speaker access."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from jarvis.desktop.protocol import Request
from jarvis.desktop.watchers import EventWatcher, WatchNotice, clipboard_kind
from jarvis.mail.models import Account

TRACE = (
    "Traceback (most recent call last):\n"
    + '  File "example.py", line 12, in run\n' * 12
    + "ValueError: invalid input"
)
FOREIGN = "This is a long paragraph written in English for a local translation suggestion. " * 10
ACCOUNT = Account(user_id="fixture", address="owner@example.test", session="a" * 32)


class Fixture:
    def __init__(self) -> None:
        self.text = "initial clipboard"
        self.rows: list[dict[str, object]] = []
        self.connected = True
        self.idle = True
        self.reads = self.polls = self.published = 0
        self.spoken: list[str] = []
        self.watcher = EventWatcher(
            self.clipboard, self.mail, lambda: self.idle, self.publish, self.spoken.append
        )

    async def clipboard(self) -> str:
        self.reads += 1
        return self.text

    async def mail(self) -> tuple[Account, list[dict[str, object]]] | None:
        self.polls += 1
        return (ACCOUNT, self.rows) if self.connected else None

    def publish(self) -> None:
        self.published += 1

    async def tick(self) -> None:
        self.watcher.next_clipboard = self.watcher.next_mail = 0
        await self.watcher.tick()


def row(key: str = "new", **extra: object) -> dict[str, object]:
    return {
        "id": key,
        "importance": "high",
        "isRead": False,
        "subject": "Synthetic important message",
        "receivedDateTime": datetime.now(UTC).isoformat(),
        "from": {"emailAddress": {"address": "sender@example.test"}},
        **extra,
    }


@pytest.mark.parametrize(
    "text,kind",
    [
        (TRACE, "error"),
        (FOREIGN, "foreign"),
        ("Ошибка возникла. " * 70, None),
        ("short", None),
        ("a" * 16001, None),
    ],
)
def test_local_detection(text: str, kind: str | None) -> None:
    assert clipboard_kind(text) == kind


@pytest.mark.asyncio
async def test_disabled_busy_baseline_dedup_expiry_and_speech() -> None:
    f = Fixture()
    await f.tick()
    assert f.reads == f.polls == 0
    f.watcher.clipboard_enabled = True
    f.text = TRACE
    await f.tick()
    assert not f.watcher.notices  # Existing clipboard at enable is not a copy event.
    f.idle = False
    f.text = FOREIGN
    await f.tick()
    assert f.reads == 1
    f.idle = True
    await f.tick()
    assert len(f.watcher.notices) == 1 and not f.spoken
    assert FOREIGN not in str(f.watcher.snapshot()) and FOREIGN not in repr(f.watcher.notices)
    await f.tick()
    assert len(f.watcher.notices) == 1
    f.watcher.notices[0].expires_at = 0
    await f.tick()
    assert not f.watcher.notices
    f.watcher.next_notice = 0
    f.watcher.speech_enabled = True
    f.text = TRACE
    await f.tick()
    assert not f.spoken  # Already seen content never speaks again.
    f.text = TRACE + "changed"
    await f.tick()
    assert len(f.spoken) == 1 and TRACE not in f.spoken[0]
    await f.watcher.close()


@pytest.mark.asyncio
async def test_mail_interval_initial_baseline_importance_dedup_and_disconnect() -> None:
    f = Fixture()
    f.watcher.mail_enabled = True
    f.rows = [row("old")]
    await f.watcher.tick()
    assert not f.watcher.notices and f.polls == 1
    await f.watcher.tick()
    assert f.polls == 1
    f.rows = [row("new")]
    await f.tick()
    assert f.watcher.notices[0].recipient == "sender@example.test"
    assert f.watcher.notices[0].account == ACCOUNT
    await f.tick()
    assert len(f.watcher.notices) == 1
    f.connected = False
    await f.tick()
    assert not f.watcher.notices and not f.watcher.mail_seen
    await f.watcher.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"isRead": True},
        {"importance": "normal"},
        {"receivedDateTime": "2020-01-01T00:00:00Z"},
        {"receivedDateTime": "2026-01-01T00:00:00"},
    ],
)
async def test_non_new_or_non_important_mail_never_notifies(extra: dict[str, object]) -> None:
    f = Fixture()
    f.watcher.mail_enabled = True
    await f.tick()
    f.rows = [row("new", **extra)]
    await f.tick()
    assert not f.watcher.notices
    await f.watcher.close()


@pytest.mark.asyncio
async def test_disable_cancels_in_flight_poll_and_clears_metadata() -> None:
    f = Fixture()
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    f.watcher.mail = slow
    await f.watcher.configure(False, True, False)
    await entered.wait()
    await asyncio.wait_for(f.watcher.configure(False, False, False), 1)
    assert cancelled.is_set() and f.watcher.task is None
    assert not f.watcher.notices


@pytest.mark.asyncio
async def test_account_change_resets_old_notifications_and_baseline() -> None:
    f = Fixture()
    f.watcher.mail_enabled = True
    await f.tick()
    f.rows = [row()]
    await f.tick()
    assert f.watcher.notices
    other = ACCOUNT.model_copy(update={"session": "b" * 32})

    async def switched() -> tuple[Account, list[dict[str, object]]]:
        return other, [row("other")]

    f.watcher.mail = switched
    await f.tick()
    assert not f.watcher.notices and f.watcher.account == other


def test_notice_expiry_and_queue_bounds() -> None:
    f = Fixture()
    for _ in range(10):
        f.watcher.next_notice = 0
        f.watcher.offer(WatchNotice("error", "Fixed local text"))
    assert len(f.watcher.notices) == 3
    f.watcher.notices[0].expires_at = 0
    with pytest.raises(ValueError, match="watcher_expired"):
        f.watcher.get(f.watcher.notices[0].id)


@pytest.mark.parametrize(
    "fields",
    [
        {"operation": "status", "clipboard_text": "unexpected"},
        {"operation": "status", "sample_id": "a" * 32},
        {"operation": "clipboard_sample"},
        {"operation": "clipboard_sample", "sample_id": "a" * 32, "clipboard_text": "x" * 16001},
        {"operation": "watcher_action"},
        {"operation": "run", "command": "test", "notification_id": "a" * 32},
        {"operation": "status", "dismiss": True},
    ],
)
def test_watcher_protocol_is_scoped(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Request.model_validate({"id": str(uuid4()), **fields})


@pytest.mark.asyncio
async def test_malformed_mail_does_not_hide_valid_mail_and_cooldown_does_not_drop_mail() -> None:
    f = Fixture()
    f.watcher.mail_enabled = True
    await f.tick()
    f.watcher.offer(WatchNotice("error", "Generic clipboard offer"))
    f.rows = [row("bad", receivedDateTime="not-a-date"), row("new")]
    await f.tick()
    assert any(n.kind == "mail" for n in f.watcher.notices)
    assert not f.spoken
    await f.watcher.close()
