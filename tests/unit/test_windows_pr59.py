"""Permission-bound Windows contracts, native failure mapping and process lifecycle."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.windows_support import WindowsProbe, target

from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.platforms.windows.transport import ProcessBackend
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import register_windows


@pytest.fixture
def audit(tmp_path: Path) -> Any:
    log = AuditLog(tmp_path / "audit.sqlite3")
    yield log
    log.close()


def engine_for(
    probe: WindowsProbe | ProcessBackend, audit: AuditLog
) -> tuple[PermissionEngine, ApprovalStore]:
    registry = ToolRegistry()
    register_windows(registry, probe)
    store = ApprovalStore()
    return PermissionEngine(registry, store, audit), store


def prepared(
    engine: PermissionEngine, name: str, args: object, mode: Mode = Mode.EXECUTE
) -> Action:
    action = engine.prepare("windows." + name, args, mode)
    assert isinstance(action, Action)
    return action


@pytest.mark.asyncio
async def test_type_without_approval_verifies_readback_and_audits(audit: AuditLog) -> None:
    probe = WindowsProbe()
    engine, _ = engine_for(probe, audit)
    args = {"target": target().model_dump(), "text": "Жарвис {ENTER}\nliteral"}
    action = prepared(engine, "type_text", args)
    assert action.risk is Risk.SAFE
    assert (await engine.execute(action)).status is Status.SUCCESS
    assert probe.text == args["text"]
    assert [item.operation for item in probe.calls] == ["check_target", "type", "verify"]
    assert (await engine.execute(action)).status is Status.INVALID
    records = "".join(audit.recent())
    for event in ("prepared", "started", "finished"):
        assert event in records
    assert "approved" not in records
    assert "literal" not in "".join(audit.recent())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("handle", 999),
        ("pid", 888),
        ("process_started", 1235.0),
        ("title", "Another tab"),
        ("runtime_id", [9, 9]),
        ("executable", "C:\\fake\\notepad.exe"),
        ("editor", target().model_dump()["editor"] | {"selected_tabs": [[9]]}),
    ],
)
async def test_changed_safe_target_never_runs(field: str, value: object, audit: AuditLog) -> None:
    import json

    probe = WindowsProbe()
    engine, _ = engine_for(probe, audit)
    action = prepared(engine, "type_text", {"target": target().model_dump(), "text": "test"})
    args = json.loads(action.payload)
    args["target"][field] = value
    changed = replace(action, payload=json.dumps(args))
    assert (await engine.execute(changed)).status is Status.DENIED
    assert not probe.calls
