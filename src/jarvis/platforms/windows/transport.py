"""Fixed private stdio protocol, no shell, payload on stdin rather than process argv."""

import asyncio
import sys
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Literal

from jarvis.observability.diagnostics import Reason
from jarvis.tools.base import ExecutionContext, ToolError, ToolModel
from jarvis.tools.windows import MAX_REPLY_BYTES, NativeRequest, Rectangle, WindowsResult


class TargetPreview(ToolModel):
    event: Literal["target"]
    rectangle: Rectangle


class NativeReply(ToolModel):
    result: WindowsResult | None = None
    reason: Reason | None = None
    error: (
        Literal[
            "unsupported_platform",
            "application_missing",
            "application_ambiguous",
            "target_changed",
            "control_unsupported",
            "native_timeout",
            "native_failure",
            "network_denied",
        ]
        | None
    ) = None


def is_windows() -> bool:
    return sys.platform == "win32"


class ProcessBackend:
    def __init__(
        self,
        timeout_seconds: float = 8.0,
        *,
        before_click: Callable[[Rectangle], Awaitable[None]] | None = None,
    ) -> None:
        if not 0 < timeout_seconds <= 10:
            raise ValueError("Invalid native timeout.")
        self.timeout_seconds = timeout_seconds
        self.before_click = before_click

    async def call(self, request: NativeRequest, context: ExecutionContext) -> WindowsResult:
        if not is_windows():
            raise ToolError("unsupported_platform")
        await context.checkpoint()
        return await self._exchange(request, context)

    async def _exchange(self, request: NativeRequest, context: ExecutionContext) -> WindowsResult:
        preview = self.before_click is not None and request.operation == "click"
        previewed = False
        # Shield creation so cancellation cannot orphan a process during startup.
        launch = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-m",
                "jarvis.platforms.windows.worker",
                *(["--visual-target"] if preview else []),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=MAX_REPLY_BYTES + 1,
                creationflags=0x08000000 if is_windows() else 0,
            )
        )
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.shield(launch)
            await context.checkpoint()
            async with asyncio.timeout(self.timeout_seconds):
                assert process.stdin is not None and process.stdout is not None
                process.stdin.write(request.model_dump_json().encode() + b"\n")
                await process.stdin.drain()
                if not preview:
                    process.stdin.close()
                # The helper emits one bounded JSON line. No unbounded communicate buffer.
                raw = await process.stdout.readuntil(b"\n")
                if len(raw) > MAX_REPLY_BYTES:
                    raise ToolError("native_failure")
                if preview and b'"event"' in raw:
                    target = TargetPreview.model_validate_json(raw)
                    if request.element is not None:
                        expected = request.element.rectangle
                    elif request.x is not None and request.y is not None:
                        expected = Rectangle(
                            left=request.x - 12,
                            top=request.y - 12,
                            right=request.x + 12,
                            bottom=request.y + 12,
                        )
                    else:
                        raise ToolError("native_failure")
                    if target.rectangle != expected or self.before_click is None:
                        raise ToolError("target_changed")
                    await self.before_click(target.rectangle)
                    previewed = True
                    await context.checkpoint()
                    process.stdin.write(b"presented\n")
                    await process.stdin.drain()
                    process.stdin.close()
                    raw = await process.stdout.readuntil(b"\n")
                    if len(raw) > MAX_REPLY_BYTES:
                        raise ToolError("native_failure")
                await process.wait()
                if process.returncode != 0:
                    raise ToolError("native_failure")
                reply = NativeReply.model_validate_json(raw)
                if reply.error is not None:
                    raise ToolError(reply.error, reason=reply.reason)
                if reply.result is None:
                    raise ToolError("native_failure")
                if preview and not previewed:
                    raise ToolError("native_failure")
                return reply.result
        except TimeoutError:
            raise ToolError("native_timeout") from None
        except ToolError:
            raise
        except Exception:
            raise ToolError("native_failure") from None
        finally:
            if process is None:
                process = await launch
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                # Drain only after termination: bounded pipe buffers, no live producer.
                # A paused stdout pipe otherwise can prevent asyncio.wait() from finishing.
                await process.communicate()
            await process.wait()
