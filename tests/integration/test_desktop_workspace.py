"""Settings travel through the UI protocol, never the planner or approval authority."""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from pytestqt.qtbot import QtBot
from tests.integration.test_desktop_service import request
from tests.unit.test_mcp_bridge import server, tool
from tests.unit.test_workspace import custom_app

from jarvis.config import AppConfig, load_config
from jarvis.desktop.service import DesktopService
from jarvis.desktop.workspace import configure as configure_workspace
from jarvis.desktop.workspace import revision
from jarvis.security.browser_policy import BrowserMode
from jarvis.ui.planner_window import PlannerWindow
from jarvis.workspace.models import UserIdentity, WorkspaceProfile
from jarvis.workspace.presets import omadli_group_preset
from jarvis.workspace.store import WorkspaceFailure, WorkspaceStore


@pytest.mark.parametrize(
    "command",
    [
        {"operation": "read", "revision": "a" * 64},
        {"operation": "save", "profile": {}},
        {"operation": "preset", "revision": "a" * 64, "preset": "unknown"},
        {"operation": "read", "profile": {}},
        {"operation": "read", "approval": True},
    ],
)
def test_workspace_envelope_is_strict(command: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        request("workspace", workspace=command)
    with pytest.raises(ValidationError):
        request("status", workspace={"operation": "read"})


@pytest.mark.asyncio
async def test_read_preset_save_reload_preserves_protected_fields(tmp_path: Path) -> None:
    reviewed = server().with_review((tool(), tool("create_issue")))
    original = WorkspaceProfile(
        user=UserIdentity(name="Владелец", role="Менеджер"),
        apps=(custom_app(),),
        mcp_servers=(reviewed,),
    )
    store = WorkspaceStore(tmp_path)
    store.save(original)
    events: list[dict[str, Any]] = []
    service = DesktopService(AppConfig(tmp_path, workspace_profile=original), events.append)
    registry_before = service.registry.discover()
    try:
        await service.handle(request("workspace", workspace={"operation": "read"}))
        state = service.workspace_state
        assert state is not None and not state["restart_required"]
        assert "mcp_servers" not in state["profile"]  # type: ignore[operator]
        await service.handle(
            request(
                "workspace",
                workspace={
                    "operation": "preset",
                    "preset": "omadli_group",
                    "revision": state["revision"],
                },
            )
        )
        selected = store.load()
        assert len(selected.apps) == 15
        assert selected.apps[-1] == original.apps[0]
        assert selected.user.name == "Владелец" and selected.user.organization == "Omadli Group"
        assert selected.mcp_servers == original.mcp_servers
        editable = selected.model_dump(mode="json", include={"user", "apps", "browser_mode"})
        editable["browser_mode"] = "authenticated_workspace"
        editable["user"]["tasks_description"] = "Проверять счета перед отправкой."
        omadli = next(app for app in editable["apps"] if app["id"] == "omadli_group")
        omadli.update(
            launch_url="https://portal.example.test/internal",
            enabled=True,
            allowed_origins=["https://portal.example.test"],
        )
        await service.handle(
            request(
                "workspace",
                workspace={
                    "operation": "save",
                    "revision": revision(selected),
                    "profile": editable,
                },
            )
        )
        saved = store.load()
        assert saved.browser_mode == "authenticated_workspace"
        assert saved.playbooks == selected.playbooks and saved.mcp_servers == original.mcp_servers
        assert service.workspace_state is not None and service.workspace_state["restart_required"]
        assert service.registry.discover() == registry_before
        assert service.config.workspace_profile == original and service.host._thread is None
        assert not service.audit.recent() and service.prompt is None
        # Profile selection never becomes a tool capability or approval.
        assert service.started_count == 0 and service.runner is None
        config = load_config({"JARVIS_DATA_DIR": str(tmp_path)})
        assert config.workspace_profile == saved and config.browser_mode is BrowserMode.WORKSPACE
        restored = DesktopService(config, lambda event: None)
        try:
            assert "browser.open_workspace_app" in {t["name"] for t in restored.registry.discover()}
            assert restored.config.workspace_profile.user.tasks_description.startswith("Проверять")
        finally:
            await restored.shutdown()
        assert all(len(json.dumps(event).encode()) < 1048576 for event in events)
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_conflict_invalid_url_and_busy_are_recoverable(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path)
    profile = omadli_group_preset()
    store.save(profile)
    events: list[dict[str, Any]] = []
    service = DesktopService(AppConfig(tmp_path, workspace_profile=profile), events.append)
    try:
        editable = profile.model_dump(mode="json", include={"user", "apps", "browser_mode"})
        editable["apps"][0]["launch_url"] = "http://127.0.0.1/private-secret"
        await service.handle(
            request(
                "workspace",
                workspace={
                    "operation": "save",
                    "revision": revision(profile),
                    "profile": editable,
                },
            )
        )
        assert events[-2]["error"] == "workspace_invalid" and store.load() == profile
        assert "private-secret" not in json.dumps(events)
        editable["apps"][0]["launch_url"] = profile.apps[0].launch_url
        editable["mcp_servers"] = []
        await service.handle(
            request(
                "workspace",
                workspace={
                    "operation": "save",
                    "revision": revision(profile),
                    "profile": editable,
                },
            )
        )
        assert events[-2]["error"] == "workspace_invalid"
        del editable["mcp_servers"]
        # Even a change to a hidden MCP review invalidates the visible form revision.
        updated = WorkspaceProfile.model_validate(
            profile.model_dump() | {"mcp_servers": (server(),)}
        )
        store.save(updated)
        await service.handle(
            request(
                "workspace",
                workspace={
                    "operation": "save",
                    "revision": revision(profile),
                    "profile": editable,
                },
            )
        )
        assert events[-2]["error"] == "workspace_changed" and store.load() == updated
        worker = asyncio.create_task(asyncio.sleep(60))
        service.worker = worker
        try:
            await service.handle(
                request(
                    "workspace",
                    workspace={
                        "operation": "preset",
                        "revision": revision(updated),
                        "preset": "omadli_group",
                    },
                )
            )
            assert events[-2]["error"] == "busy" and store.load() == updated
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            service.worker = None
        await service.handle(request("workspace", workspace={"operation": "read"}))
        assert events[-2]["ok"] and service.workspace_state is not None
    finally:
        await service.shutdown()


def test_catalog_labels_cover_desktop_and_main_planner(qtbot: QtBot, tmp_path: Path) -> None:
    labels = (Path(__file__).parents[2] / "desktop/src/runtime.ts").read_text()
    named = set(re.findall(r'"([a-z_]+\.[a-z0-9_]+)":', labels))
    profile = omadli_group_preset(omadli_group_url="https://portal.example.test")
    config = AppConfig(tmp_path, workspace_profile=profile, browser_mode=BrowserMode.WORKSPACE)
    window = PlannerWindow(config)
    qtbot.addWidget(window)
    service = DesktopService(config, lambda event: None)
    try:
        names = {
            str(row["name"]) for r in (window.registry, service.registry) for row in r.discover()
        }
        assert names <= named, f"Missing Electron labels: {names - named}"
    finally:
        window.shutdown()
        asyncio.run(service.shutdown())


def test_preset_refuses_limit_without_losing_custom_apps(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path)
    original = WorkspaceProfile(apps=tuple(custom_app(id=f"app_{i}") for i in range(128)))
    store.save(original)
    command = request(
        "workspace",
        workspace={
            "operation": "preset",
            "preset": "omadli_group",
            "revision": revision(original),
        },
    ).workspace
    assert command is not None
    with pytest.raises(WorkspaceFailure, match="workspace_limit"):
        configure_workspace(store, command)
    assert store.load() == original
