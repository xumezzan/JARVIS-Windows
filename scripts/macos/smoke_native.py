"""Explicit local acceptance probe. No accounts, model calls, or user documents.

The clipboard is restored with all formats, unless its owner changes it meanwhile.
Only a disposable test window is captured; reports contain counts and status codes.
"""

import argparse
import asyncio
import base64
import importlib
import json
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from jarvis.desktop.system_tools import desktop_registry
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.platforms.windows.transport import ProcessBackend
from jarvis.tools.windows import WindowsResult, register_windows

TITLE = "Jarvis macOS acceptance fixture"
SAMPLE = "Jarvis macOS smoke — проверка"


def fixture() -> None:
    from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QVBoxLayout, QWidget

    app = QApplication([])
    window = QWidget()
    window.setWindowTitle(TITLE)
    window.resize(640, 360)
    layout = QVBoxLayout(window)
    field = QLineEdit()
    field.setAccessibleName("Jarvis test input")
    layout.addWidget(field)
    button = QPushButton("Jarvis test button")
    button.setAccessibleName("Jarvis test button")
    layout.addWidget(button)
    window.show()
    window.raise_()
    window.activateWindow()
    app.exec()


class ClipboardSnapshot:
    def __init__(self) -> None:
        self.appkit: Any = importlib.import_module("AppKit")
        self.foundation: Any = importlib.import_module("Foundation")
        self.board = self.appkit.NSPasteboard.generalPasteboard()
        self.items: list[list[tuple[str, bytes]]] = []
        total = 0
        before = self.board.changeCount()
        for item in self.board.pasteboardItems() or ():
            formats = []
            for kind in item.types():
                value = item.dataForType_(kind)
                if value is None or len(value) > 16 * 1024 * 1024:
                    raise RuntimeError("clipboard_snapshot_unavailable")
                total += len(value)
                if total > 32 * 1024 * 1024:
                    raise RuntimeError("clipboard_snapshot_too_large")
                formats.append((str(kind), bytes(value)))
            self.items.append(formats)
        if self.board.changeCount() != before:
            raise RuntimeError("clipboard_changed_during_snapshot")
        self.expected = before

    def restore(self) -> str:
        if self.board.changeCount() != self.expected:
            return "owner_changed_clipboard_preserved"
        items = []
        for formats in self.items:
            item = self.appkit.NSPasteboardItem.alloc().init()
            for kind, data in formats:
                value = self.foundation.NSData.dataWithBytes_length_(data, len(data))
                if not item.setData_forType_(value, kind):
                    raise RuntimeError("clipboard_restore_failed")
            items.append(item)
        self.board.clearContents()
        if items and not self.board.writeObjects_(items):
            raise RuntimeError("clipboard_restore_failed")
        return "restored_all_formats"


async def probe(image: Path | None) -> dict[str, object]:
    report: dict[str, object] = {"platform": sys.platform, "checks": {}}
    checks: dict[str, object] = {}
    report["checks"] = checks
    with tempfile.TemporaryDirectory(prefix="jarvis-macos-smoke-") as temporary:
        root = Path(temporary)
        registry, _ = desktop_registry(root)
        register_windows(registry, ProcessBackend())
        with closing(AuditLog(root / "audit.sqlite3")) as audit:
            engine = PermissionEngine(registry, ApprovalStore(), audit)

            async def run(name: str, arguments: object) -> Outcome:
                prepared = engine.prepare(name, arguments, Mode.EXECUTE)
                result = (
                    await engine.execute(prepared) if isinstance(prepared, Action) else prepared
                )
                checks[name] = {
                    "status": result.status.value,
                    "error": result.error.value,
                    "reason": result.reason.value if result.reason else None,
                }
                return result

            await run("system.info", {})
            snapshot = ClipboardSnapshot()
            try:
                written = await run("windows.clipboard_set", {"text": SAMPLE})
                if written.status is Status.SUCCESS:
                    snapshot.expected = snapshot.board.changeCount()
                    read = await run("windows.clipboard_get", {})
                    result = WindowsResult.model_validate_json(read.result_json or "{}")
                    report["clipboard_roundtrip"] = result.text == SAMPLE
            finally:
                report["clipboard_restore"] = snapshot.restore()
            listed = await run("windows.get_open_windows", {})
            report["window_count"] = len(
                WindowsResult.model_validate_json(listed.result_json or "{}").windows
            )
            ax: Any = importlib.import_module("ApplicationServices")
            quartz: Any = importlib.import_module("Quartz")
            report["accessibility"] = bool(ax.AXIsProcessTrusted())
            report["screen_recording"] = bool(quartz.CGPreflightScreenCaptureAccess())
            if not report["accessibility"]:
                # Exercise the real denial protocol. It opens only the fixed settings pane.
                await run("windows.screenshot", {})
                report["fixture"] = "requires_accessibility"
                return report
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--fixture"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = asyncio.get_running_loop().time() + 10
                target = None
                while target is None and asyncio.get_running_loop().time() < deadline:
                    listed = await run("windows.get_open_windows", {})
                    windows = WindowsResult.model_validate_json(listed.result_json or "{}").windows
                    target = next(
                        (w for w in windows if w.pid == process.pid and w.title == TITLE), None
                    )
                    if target is None:
                        await asyncio.sleep(0.05)
                if target is None:
                    report["fixture"] = "window_not_observed"
                    return report
                report["fixture"] = "observed"
                focused = await run("windows.focus_app", {"target": target.model_dump()})
                if focused.status is not Status.SUCCESS:
                    return report
                fields = await run("windows.get_text_fields", {"target": target.model_dump()})
                observed_fields = WindowsResult.model_validate_json(fields.result_json or "{}")
                if len(observed_fields.editors) == 1:
                    await run(
                        "windows.type_text",
                        {
                            "target": target.model_dump(),
                            "editor": observed_fields.editors[0].model_dump(),
                            "text": SAMPLE,
                        },
                    )
                    await run(
                        "windows.send_hotkey", {"target": target.model_dump(), "hotkey": "Ctrl+A"}
                    )
                captured = await run("windows.screenshot", {})
                if captured.status is Status.SUCCESS:
                    result = WindowsResult.model_validate_json(captured.result_json or "{}")
                    if result.target is None or result.target.identity != target.identity:
                        report["fixture"] = "foreground_changed"
                        return report
                    report["marked_elements"] = len(result.elements)
                    report["image_dimensions"] = [result.width, result.height]
                    if image is not None and captured.screenshot_b64:
                        # Explicit local destination, never part of the committed report.
                        image.write_bytes(base64.b64decode(captured.screenshot_b64, validate=True))
                    button = next(
                        (e for e in result.elements if e.name == "Jarvis test button"), None
                    )
                    if button is not None and result.target is not None:
                        await run(
                            "windows.click_element",
                            {
                                "target": result.target.model_dump(),
                                "element": button.model_dump(),
                            },
                        )
            finally:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--image", type=Path, help="Optional local image of the test window only")
    parser.add_argument("--report", type=Path, help="Sanitized local JSON acceptance report")
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("This live probe requires macOS.")
    if args.fixture:
        fixture()
        return
    result = asyncio.run(probe(args.image))
    required = {
        "system.info",
        "windows.clipboard_set",
        "windows.clipboard_get",
        "windows.get_open_windows",
        "windows.focus_app",
        "windows.get_text_fields",
        "windows.type_text",
        "windows.send_hotkey",
        "windows.screenshot",
        "windows.click_element",
    }
    checks = result.get("checks")
    passed = (
        isinstance(checks, dict)
        and all(
            isinstance(checks.get(name), dict)
            and checks[name].get("status") == Status.SUCCESS.value
            for name in required
        )
        and result.get("clipboard_roundtrip") is True
    )
    result["acceptance"] = "passed" if passed else "incomplete"
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(text, end="")
    if not passed:
        raise SystemExit(2 if not result.get("accessibility") else 1)


if __name__ == "__main__":
    main()
