"""Both desktop platforms use the same bounded, disposable protocol transport."""

import asyncio
import sys
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.observability.diagnostics import Reason, failure_message
from jarvis.platforms.windows import transport
from jarvis.tools.base import ExecutionContext, ToolError
from jarvis.tools.windows import NativeRequest, WindowsResult


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "platform,worker",
    [
        ("win32", "jarvis.platforms.windows.worker"),
        ("darwin", "jarvis.platforms.macos.worker"),
    ],
)
async def test_same_protocol_selects_only_the_host_worker(
    platform: str, worker: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_launch = asyncio.create_subprocess_exec
    launches: list[tuple[object, ...]] = []

    async def launch(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        launches.append(args)
        # Run a harmless protocol fixture on the real host, never native input.
        kwargs.pop("creationflags", None)
        kwargs.pop("start_new_session", None)
        return await real_launch(
            sys.executable,
            "-I",
            "-c",
            "import sys; sys.stdin.buffer.readline(); print('{\"result\":{}}')",
            **kwargs,
        )

    monkeypatch.setattr(
        transport, "sys", SimpleNamespace(platform=platform, executable=sys.executable)
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    result = await transport.ProcessBackend().call(
        NativeRequest(operation="list"), ExecutionContext(Event())
    )
    assert result == WindowsResult()
    assert len(launches) == 1
    assert launches[0] == (sys.executable, "-I", "-m", worker)


def test_other_hosts_cannot_select_a_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport, "sys", SimpleNamespace(platform="linux"))
    with pytest.raises(ToolError, match="unsupported_platform"):
        transport.native_worker()


@pytest.mark.parametrize(
    "reason",
    [Reason.MACOS_ACCESSIBILITY_REQUIRED, Reason.MACOS_SCREEN_RECORDING_REQUIRED],
)
def test_permission_diagnostics_cross_the_existing_reply_schema(reason: Reason) -> None:
    reply = transport.NativeReply(error="control_unsupported", reason=reason)
    assert transport.NativeReply.model_validate_json(reply.model_dump_json()) == reply
    assert "Системные настройки" in failure_message("control_unsupported", reason)
