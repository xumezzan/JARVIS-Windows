"""Preview timing/cancellation and stale targets; fixtures are not Windows acceptance."""

import asyncio
import json
import sys
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import Mock

import pytest
from tests.unit.test_windows_cross_app import ELEMENT, RECT, adapter, control
from tests.windows_support import target

from jarvis.config import AppConfig
from jarvis.desktop.protocol import Request
from jarvis.desktop.service import DesktopService
from jarvis.platforms.windows.transport import ProcessBackend
from jarvis.tools.base import ExecutionContext, ToolError
from jarvis.tools.windows import NativeRequest, Rectangle, WindowsResult


def test_moved_control_after_preview_is_never_invoked(monkeypatch: pytest.MonkeyPatch) -> None:
    desktop = adapter()
    window, button = control("Window"), control()
    monkeypatch.setattr(desktop, "activate", lambda *args: window)
    monkeypatch.setattr(desktop, "require_foreground", lambda *args: None)
    monkeypatch.setattr(desktop, "walk", lambda *args: ([button], False))

    def move(rectangle: Rectangle) -> None:
        assert rectangle == RECT
        button.rectangle.return_value = Mock(left=11, top=20, right=611, bottom=420)

    desktop.before_click = move
    with pytest.raises(ToolError, match="target_changed"):
        desktop.dispatch(NativeRequest(operation="click", target=target(), element=ELEMENT))
    button.iface_invoke.Invoke.assert_not_called()
    button.click_input.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_helper_waits_for_preview_and_is_reaped_on_cancel(
    monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    launch = asyncio.create_subprocess_exec
    processes: list[asyncio.subprocess.Process] = []
    preview = json.dumps({"event": "target", "rectangle": RECT.model_dump()})
    reply = json.dumps({"result": WindowsResult(target=target(), performed=True).model_dump()})
    script = (
        "import sys,json\n"
        "json.loads(sys.stdin.readline())\n"
        f"print({preview!r},flush=True)\n"
        "assert sys.stdin.readline() == 'presented\\n'\n"
        f"print({reply!r},flush=True)\n"
    )

    async def spawn(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        assert "--visual-target" in args
        process = await launch(sys.executable, "-I", "-c", script, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    entered, release = asyncio.Event(), asyncio.Event()

    async def before_click(rectangle: Rectangle) -> None:
        assert rectangle == RECT
        entered.set()
        await release.wait()

    task = asyncio.create_task(
        ProcessBackend(before_click=before_click)._exchange(
            NativeRequest(operation="click", target=target(), element=ELEMENT),
            ExecutionContext(Event()),
        )
    )
    await asyncio.wait_for(entered.wait(), 2)
    assert not task.done()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        release.set()
        assert (await task).performed
    assert processes[0].returncode is not None


@pytest.mark.asyncio
async def test_receipt_is_exact_single_use_and_not_approval(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []
    service = DesktopService(AppConfig(tmp_path), events.append)
    try:
        display = asyncio.create_task(service._highlight(RECT))
        await asyncio.sleep(0)
        receipt = service.target_receipt
        assert receipt is not None
        from uuid import uuid4

        def request(key: str) -> Request:
            return Request(id=uuid4(), operation="target_presented", target_id=key)

        await service.handle(request("0" * 32))
        assert not display.done() and not receipt[1].done()
        await service.handle(request(receipt[0]))
        await display
        assert service.prompt is None and service.target_receipt is None
        await service.handle(request(receipt[0]))
        assert any(item.get("ok") is False for item in events)
    finally:
        await service.shutdown()
