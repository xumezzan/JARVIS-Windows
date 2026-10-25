"""EventWatcher integrates with the real permission engine using synthetic mail/clipboard."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from threading import Event

import pytest
import pytest_asyncio
from tests.integration.test_desktop_service import request, until
from tests.mail_support import FakeCredentials, FakeGraph
from tests.unit.test_watchers import TRACE, row

from jarvis.config import AppConfig
from jarvis.desktop.service import DesktopService
from jarvis.desktop.watchers import WatchNotice, fingerprint
from jarvis.mail.session import MailSession
from jarvis.tools.base import ExecutionContext
from jarvis.tools.outlook import register_outlook


class WatchGraph(FakeGraph):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, object]] = []
        self.block = False
        self.entered = asyncio.Event()
        self.stopped = asyncio.Event()

    async def request(
        self,
        token: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        if path.startswith("/me/mailFolders/"):
            assert params is not None and "importance" in params["$select"]
            self.calls.append((method, path))
            if self.block:
                self.entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    self.stopped.set()
            return 200, {"value": self.rows}
        if path.startswith("/me/messages/") and not path.endswith("/attachments"):
            key = path.rsplit("/", 1)[1]
            return 200, next((value for value in self.rows if value["id"] == key), {})
        return await super().request(token, method, path, payload, params)


@pytest_asyncio.fixture
async def watcher_service(
    tmp_path: Path,
) -> AsyncIterator[tuple[DesktopService, WatchGraph, list[dict[str, object]]]]:
    events: list[dict[str, object]] = []
    service = DesktopService(AppConfig(tmp_path), events.append)
    graph = WatchGraph()
    session = MailSession(FakeCredentials(), graph)
    await session.connect("00000000-0000-0000-0000-000000000001", ExecutionContext(Event()))
    service.mail_session = session
    # Re-register the same public tools in an independent registry bound to the synthetic session.
    from jarvis.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_outlook(registry, session)
    for name in ("outlook.list", "outlook.read", "outlook.local_draft"):
        service.registry._tools[name] = registry._tools[name]
    try:
        yield service, graph, events
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_mail_detection_then_explicit_ram_draft_without_remote_write(
    watcher_service: tuple[DesktopService, WatchGraph, list[dict[str, object]]],
) -> None:
    service, graph, events = watcher_service
    service.settings = service.settings.model_copy(update={"watch_mail": True})
    service.watchers.mail_enabled = True
    await service.watchers.tick()
    graph.rows = [row()]
    service.watchers.next_mail = 0
    await service.watchers.tick()
    notice = service.watchers.notices[0]
    assert not graph.writes and not service.mail_session.drafts()
    assert not service.history and service.started_count == 0 and not service.last_speech
    await service.handle(request("watcher_action", notification_id=notice.id))
    await until(lambda: service.auxiliary is None)
    drafts = service.mail_session.drafts()
    assert len(drafts) == 1 and drafts[0][1].to == [notice.recipient]
    assert service.mail_result["state"] == "loaded_draft"
    assert not service.watchers.notices and not graph.writes
    assert not service.conversation.read() and not service.history
    assert all(event.get("event") != "transcript" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["account", "sender", "subject", "expired", "disabled"])
async def test_stale_mail_suggestion_cannot_create_a_draft(
    watcher_service: tuple[DesktopService, WatchGraph, list[dict[str, object]]],
    change: str,
) -> None:
    service, graph, _ = watcher_service
    service.settings = service.settings.model_copy(update={"watch_mail": True})
    account = service.mail_session.account
    assert account is not None
    graph.rows = [row()]
    notice = WatchNotice(
        "mail",
        "Generic offer",
        account=account,
        message_id="new",
        recipient="sender@example.test",
        subject="Synthetic important message",
    )
    service.watchers.notices.append(notice)
    if change == "account":
        service.mail_session.account = account.model_copy(update={"session": "b" * 32})
    elif change == "sender":
        graph.rows[0]["from"] = {"emailAddress": {"address": "changed@example.test"}}
    elif change == "subject":
        graph.rows[0]["subject"] = "Changed"
    elif change == "expired":
        notice.expires_at = 0
    else:
        service.settings = service.settings.model_copy(update={"watch_mail": False})
    await service.handle(request("watcher_action", notification_id=notice.id))
    await until(lambda: service.auxiliary is None)
    assert not service.mail_session.drafts() and not graph.writes


@pytest.mark.asyncio
async def test_foreground_preempts_mail_and_stop_drains_engine_helpers(
    watcher_service: tuple[DesktopService, WatchGraph, list[dict[str, object]]],
) -> None:
    service, graph, _ = watcher_service
    graph.block = True
    await service.handle(request("configure", settings={"watch_mail": True}))
    await asyncio.wait_for(graph.entered.wait(), 2)
    await asyncio.wait_for(service.handle(request("cancel")), 2)
    assert graph.stopped.is_set() and service.watchers.task is None
    assert not service.engine._pending
    assert "приостановлены" in str(service.watchers.snapshot()["status"])
    await service.handle(request("configure", settings={}))
    assert not service.settings.watch_mail


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_clipboard_handoff_needs_live_receipt_and_never_autostarts(
    watcher_service: tuple[DesktopService, WatchGraph, list[dict[str, object]]],
    changed: bool,
) -> None:
    service, _, events = watcher_service
    service.settings = service.settings.model_copy(
        update={"watch_clipboard": True, "auto_start": True}
    )
    notice = WatchNotice("error", "Generic clipboard offer", digest=fingerprint(TRACE))
    service.watchers.notices.append(notice)
    assert TRACE not in json.dumps(service.snapshot())
    await service.handle(request("watcher_action", notification_id=notice.id))
    await until(lambda: service.clipboard_receipt is not None)
    receipt = service.clipboard_receipt
    assert receipt is not None
    await service.handle(request("clipboard_sample", sample_id="f" * 32, clipboard_text=TRACE))
    assert not receipt[1].done()
    await service.handle(
        request(
            "clipboard_sample",
            sample_id=receipt[0],
            clipboard_text=TRACE + ("changed" if changed else ""),
        )
    )
    await until(lambda: service.auxiliary is None)
    transcripts = [e for e in events if e.get("event") == "transcript"]
    assert bool(transcripts) is not changed
    assert service.started_count == 0 and not service.history
    assert not service.watchers.notices
    assert TRACE not in service.config.data_dir.joinpath("audit.sqlite3").read_bytes().decode(
        errors="ignore"
    )


@pytest.mark.asyncio
async def test_watcher_mail_cannot_bypass_durable_audit(
    watcher_service: tuple[DesktopService, WatchGraph, list[dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.observability.audit import AuditEvent

    service, graph, _ = watcher_service

    def fail(event: AuditEvent) -> None:
        raise OSError("synthetic audit failure")

    monkeypatch.setattr(service.audit, "write", fail)
    before = len(graph.calls)
    with pytest.raises(ValueError, match="watcher_unavailable"):
        await service._watch_mail()
    assert len(graph.calls) == before and not graph.writes
