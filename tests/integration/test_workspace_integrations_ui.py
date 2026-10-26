"""Manifest review uses a visible owner gesture; ordinary startup performs no MCP I/O."""

import asyncio
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.unit.test_mcp_bridge import server, tool

from jarvis.config import AppConfig
from jarvis.desktop.service import DesktopService
from jarvis.ui.planner_window import PlannerWindow
from jarvis.ui.workspace_mcp_panel import WorkspaceManifestWorker, WorkspaceMcpPanel
from jarvis.workspace.models import WorkspaceProfile
from jarvis.workspace.store import WorkspaceStore


def test_owner_review_is_required_before_workspace_tools_are_registered(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = WorkspaceStore(tmp_path)
    store.save(WorkspaceProfile(mcp_servers=(server(),)))
    panel = WorkspaceMcpPanel(store)
    qtbot.addWidget(panel)
    assert not panel.save.isEnabled() and not store.load().mcp_servers[0].reviewed

    def run(worker: WorkspaceManifestWorker) -> None:
        worker.tools = (tool(), tool("create_issue"))

    monkeypatch.setattr(WorkspaceManifestWorker, "run", run)
    panel.discover()
    qtbot.waitUntil(panel.save.isEnabled)
    assert "input_schema" in panel.manifest.toPlainText()
    assert not store.load().mcp_servers[0].reviewed
    panel.save.click()  # Only the owner's review gesture persists the pin.
    assert store.load().mcp_servers[0].reviewed
    panel.shutdown()


def test_review_does_not_overwrite_concurrent_workspace_edits(qtbot: QtBot, tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path)
    original = WorkspaceProfile(mcp_servers=(server(),))
    store.save(original)
    panel = WorkspaceMcpPanel(store)
    qtbot.addWidget(panel)
    panel.review = server().with_review((tool(), tool("create_issue")))
    current = WorkspaceProfile(browser_mode="authenticated_workspace", mcp_servers=(server(),))
    store.save(current)
    panel.accept_review()
    assert store.load() == current
    assert "изменён" in panel.status.text()
    panel.shutdown()


def test_main_planner_and_desktop_register_calendar_and_reviewed_workspace_mcp(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    reviewed = server().with_review((tool(), tool("create_issue")))
    profile = WorkspaceProfile(mcp_servers=(reviewed,))
    config = AppConfig(tmp_path, workspace_profile=profile)
    window = PlannerWindow(config)
    qtbot.addWidget(window)
    service = DesktopService(config, lambda event: None)
    expected = {
        "calendar.list_events",
        "calendar.create_draft_event",
        "calendar.create_event",
        "mcp.fixture__search",
        "mcp.fixture__create_issue",
    }
    try:
        for registry in (window.registry, service.registry):
            assert expected <= {row["name"] for row in registry.discover()}
        assert service.mail_session.account is None
    finally:
        window.shutdown()
        asyncio.run(service.shutdown())
