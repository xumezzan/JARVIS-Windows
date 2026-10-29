"""Bounded macOS native helper; shared protocol, no approval authority."""

import contextlib
import sys

from jarvis.platforms.windows.ghost import CursorTarget
from jarvis.platforms.windows.transport import NativeReply, TargetPreview
from jarvis.tools.base import ToolError
from jarvis.tools.windows import MAX_REPLY_BYTES, NativeRequest


def main() -> None:
    output = sys.stdout.buffer

    def preview(target: CursorTarget) -> None:
        output.write(
            (TargetPreview(event="target", **target.model_dump()).model_dump_json() + "\n").encode()
        )
        output.flush()
        if sys.stdin.buffer.readline(32) != b"presented\n":
            raise ToolError("native_failure")

    try:
        if sys.platform != "darwin":
            raise ToolError("unsupported_platform")
        raw = sys.stdin.buffer.readline(65537)
        if len(raw) > 65536 or not raw.endswith(b"\n"):
            raise ToolError("native_failure")
        request = NativeRequest.model_validate_json(raw)
        with contextlib.redirect_stdout(sys.stderr):
            from jarvis.platforms.macos.native import NativeDesktop

            desktop = NativeDesktop()
            if sys.argv[1:] == ["--visual-target"] and request.operation in {"click", "type"}:
                desktop.before_action = preview
            reply = NativeReply(result=desktop.dispatch(request))
    except ToolError as error:
        reply = NativeReply.model_validate({"error": error.code, "reason": error.reason})
    except Exception:
        reply = NativeReply(error="native_failure")
    encoded = reply.model_dump_json()
    if len(encoded.encode()) > MAX_REPLY_BYTES:
        encoded = NativeReply(error="native_failure").model_dump_json()
    output.write(encoded.encode("utf-8") + b"\n")
    output.flush()


if __name__ == "__main__":
    main()
