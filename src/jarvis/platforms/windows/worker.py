"""Private fixed-protocol helper. No approval authority or planner interface."""

import contextlib
import sys

from jarvis.platforms.windows.transport import NativeReply, is_windows
from jarvis.tools.base import ToolError
from jarvis.tools.windows import MAX_REPLY_BYTES, NativeRequest, Rectangle


def main() -> None:
    output = sys.stdout.buffer

    def preview(rectangle: Rectangle) -> None:
        # Parent bounds the wait and kills this helper on cancellation/disconnection.
        output.write(
            ('{"event":"target","rectangle":' + rectangle.model_dump_json() + "}\n").encode()
        )
        output.flush()
        if sys.stdin.buffer.readline(32) != b"presented\n":
            raise ToolError("native_failure")

    try:
        if not is_windows():
            raise ToolError("unsupported_platform")
        raw = sys.stdin.buffer.readline(65537)
        if len(raw) > 65536 or not raw.endswith(b"\n"):
            raise ToolError("native_failure")
        request = NativeRequest.model_validate_json(raw)
        # Native libraries may print diagnostics; never mix them with the protocol.
        with contextlib.redirect_stdout(sys.stderr):
            from jarvis.platforms.windows.native import NativeDesktop

            desktop = NativeDesktop()
            if sys.argv[1:] == ["--visual-target"] and request.operation == "click":
                desktop.before_click = preview
            reply = NativeReply(result=desktop.dispatch(request))
    except ToolError as error:
        reply = NativeReply.model_validate({"error": error.code})
    except Exception:
        reply = NativeReply(error="native_failure")
    encoded = reply.model_dump_json()
    if len(encoded.encode()) > MAX_REPLY_BYTES:
        encoded = NativeReply(error="native_failure").model_dump_json()
    output.write(encoded.encode("utf-8") + b"\n")
    output.flush()


if __name__ == "__main__":
    main()
