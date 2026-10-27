"""Real SQLite durability/redaction with large synthetic Windows observations."""

import base64
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import pytest
from tests.unit.test_planner import Scripted
from tests.windows_support import target

from jarvis.core.planner.offline import call
from jarvis.core.planner.runner import Runner
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore, ApprovalToken
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.policies import Mode
from jarvis.tools.base import ExecutionContext
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import (
    MAX_SCREENSHOT_JPEG_BYTES,
    ElementTarget,
    NativeRequest,
    Rectangle,
    WindowsResult,
    clipboard_digest,
    register_windows,
)

AUDIT_FIELDS = {
    "timestamp",
    "session_id",
    "request_id",
    "actor",
    "tool",
    "arguments",
    "risk",
    "mode",
    "permission_decision",
    "event",
    "status",
    "duration_ms",
    "error",
    "result_summary",
    "may_have_effects",
    "stage",
    "reason",
}


@pytest.mark.asyncio
async def test_two_thirty_step_tasks_commit_only_metadata_before_adapter(tmp_path: Path) -> None:
    path = tmp_path / "audit.sqlite3"
    observed = target().model_copy(update={"title": "PRIVATE_WINDOW_TITLE_SENTINEL"})
    rect = Rectangle(left=0, top=0, right=1280, bottom=800)
    elements = [
        ElementTarget(
            mark_id=index,
            runtime_id=[index],
            name="PRIVATE_UIA_TREE_SENTINEL".ljust(120, "x"),
            automation_id="private-control",
            control_type="Button",
            rectangle=rect,
        )
        for index in range(1, 97)
    ]
    text = "PRIVATE_ARGUMENT_SENTINEL".ljust(4000, "x")
    image = base64.b64encode(
        b"PRIVATE_SCREENSHOT_SENTINEL".ljust(MAX_SCREENSHOT_JPEG_BYTES, b"x")
    ).decode()
    adapter_calls = 0

    class Backend:
        async def call(self, request: NativeRequest, context: ExecutionContext) -> WindowsResult:
            nonlocal adapter_calls
            await context.checkpoint()
            # A separate connection cannot observe an uncommitted STARTED row.
            with closing(sqlite3.connect(path)) as reader:
                rows = reader.execute(
                    "SELECT record FROM events ORDER BY id DESC LIMIT 3"
                ).fetchall()
            latest = json.loads(rows[0][0])
            assert latest["event"] == "started"
            assert latest["status"] is None and latest["arguments"] == "[REDACTED]"
            assert (
                latest["tool"]
                == {
                    "inspect": "windows.inspect_active_window",
                    "clipboard_set": "windows.clipboard_set",
                    "screenshot": "windows.screenshot",
                    "hotkey": "windows.send_hotkey",
                }[request.operation]
            )
            previous = [json.loads(row[0]) for row in rows[1:]]
            assert previous[0]["request_id"] == latest["request_id"]
            assert previous[0]["event"] == (
                "approved" if request.operation == "hotkey" else "prepared"
            )
            adapter_calls += 1
            match request.operation:
                case "inspect":
                    return WindowsResult(target=observed, window_rectangle=rect, elements=elements)
                case "clipboard_set":
                    assert request.text == text
                    return WindowsResult(text=text, clipboard_sha256=clipboard_digest(text))
                case "screenshot":
                    return WindowsResult(
                        target=observed,
                        image_base64=image,
                        image_format="JPEG",
                        width=1280,
                        height=800,
                    )
                case "hotkey":
                    assert request.hotkey == "Enter" and request.target == observed
                    return WindowsResult(target=observed, performed=True)
            raise AssertionError("Unexpected native operation")

    proposals = [
        proposal
        for _ in range(7)
        for proposal in (
            call("windows.inspect_active_window", {}),
            call("windows.clipboard_set", {"text": text}),
            call("windows.screenshot", {}),
            call("windows.send_hotkey", {"target": observed.model_dump(), "hotkey": "Enter"}),
        )
    ] + [call("windows.screenshot", {})] * 2
    registry = ToolRegistry()
    register_windows(registry, Backend())
    with closing(AuditLog(path)) as audit:
        assert audit._connection.execute("PRAGMA synchronous").fetchone() == (2,)  # FULL
        store = ApprovalStore()
        authority = store.take_authority(audit.approved)
        engine = PermissionEngine(registry, store, audit)

        async def approve(action: Action) -> ApprovalToken:
            return authority.approve(action)

        async def clarify(question: str) -> None:
            pytest.fail("Unexpected clarification")

        for _ in range(2):
            runner = Runner(registry, engine, Scripted(list(proposals)), approve, clarify)
            result = await runner.run("PRIVATE_COMMAND_SENTINEL", Mode.EXECUTE)
            assert result.status == "finished" and len(result.steps) == 30
        assert adapter_calls == 60

    # Inspect every persisted row after closing the writer, without AuditLog.recent's limit.
    with closing(sqlite3.connect(path)) as reader:
        serialized = [row[0] for row in reader.execute("SELECT record FROM events ORDER BY id")]
        assert reader.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert len(serialized) == 194  # 60 * (prepared/started/finished) + 14 approvals
    lifecycles: dict[str, list[str]] = defaultdict(list)
    for row in serialized:
        event = json.loads(row)
        assert set(event) == AUDIT_FIELDS
        assert event["arguments"] == "[REDACTED]"
        assert event["result_summary"] == (event["status"] or event["event"])
        assert len(row.encode()) < 1024
        lifecycles[event["request_id"]].append(event["event"])
    assert len(lifecycles) == 60
    assert all(
        events
        in (["prepared", "started", "finished"], ["prepared", "approved", "started", "finished"])
        for events in lifecycles.values()
    )
    raw_database = path.read_bytes()
    for marker in (
        b"PRIVATE_COMMAND_SENTINEL",
        b"PRIVATE_ARGUMENT_SENTINEL",
        b"PRIVATE_WINDOW_TITLE_SENTINEL",
        b"PRIVATE_UIA_TREE_SENTINEL",
        b"PRIVATE_SCREENSHOT_SENTINEL",
        image.encode(),
        image[:128].encode(),
        b"image_base64",
        b"screenshot_b64",
        b"elements",
        b"window_rectangle",
        b"clipboard_sha256",
    ):
        assert marker not in raw_database
    assert len(raw_database) < 256 * 1024
