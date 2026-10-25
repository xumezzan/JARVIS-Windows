"""Portable observation probe, not evidence of Windows behavior."""

import asyncio
from dataclasses import dataclass, field

from jarvis.tools.base import ExecutionContext, ToolError
from jarvis.tools.windows import (
    EditorTarget,
    NativeRequest,
    WindowsResult,
    WindowTarget,
    text_digest,
)


def target() -> WindowTarget:
    return WindowTarget(
        app="notepad",
        handle=100,
        pid=200,
        process_started=1234.0,
        executable="C:\\Windows\\System32\\notepad.exe",
        runtime_id=[1, 2],
        title="Untitled - Notepad",
        empty=True,
        editor=EditorTarget(
            runtime_id=[3, 4],
            control_type="Edit",
            automation_id="15",
            class_name="Edit",
            handle=101,
            selected_tabs=[[5, 6]],
        ),
    )


@dataclass
class WindowsProbe:
    calls: list[NativeRequest] = field(default_factory=list)
    text: str = ""
    wrong_readback: bool = False
    changed: bool = False
    delay: float = 0
    exited: bool = False

    async def call(self, request: NativeRequest, context: ExecutionContext) -> WindowsResult:
        self.calls.append(request)
        try:
            await asyncio.sleep(self.delay)
            await context.checkpoint()
            if self.changed:
                raise ToolError("target_changed")
            match request.operation:
                case "list":
                    return WindowsResult(windows=[target()])
                case "check_open":
                    if request.app != "notepad":
                        raise ToolError("application_missing")
                    return WindowsResult()
                case "open":
                    return WindowsResult(target=target())
                case "fields":
                    editor = target().editor
                    assert editor is not None
                    return WindowsResult(target=request.target, editors=[editor])
                case "check_target":
                    if request.text is not None and self.text:
                        raise ToolError("target_changed")
                    return WindowsResult(target=request.target)
                case "focus":
                    return WindowsResult(target=request.target, focused=True)
                case "type":
                    self.text = request.text or ""
                    return WindowsResult(target=request.target, text_sha256=text_digest(self.text))
                case "verify":
                    return WindowsResult(
                        target=request.target,
                        focused=True,
                        text_sha256=text_digest("wrong" if self.wrong_readback else self.text),
                    )
        finally:
            self.exited = True
        raise AssertionError("Unexpected operation")
