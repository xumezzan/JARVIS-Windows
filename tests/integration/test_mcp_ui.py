"""Reviewing a server in the real interface: read, decide, and only then a usable tool."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot
from tests.unit.test_mcp import KEY, LISTED, ORIGIN, PATH, FakeServer

from jarvis.config import AppConfig
from jarvis.connectors.mcp.connector import McpConnector
from jarvis.connectors.mcp.manifest import Server, load_servers
from jarvis.permissions.policies import Risk
from jarvis.ui.mcp_panel import McpPanel
from jarvis.ui.planner_window import PlannerWindow

TOOL = "mcp_probe.search_issues"


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    server = FakeServer()

    async def key(provider: str = "openai") -> str:
        return KEY

    def client(entry: Server) -> McpConnector:
        return McpConnector(entry, server)  # type: ignore[arg-type]

    monkeypatch.setattr("jarvis.connectors.mcp.connector.load_api_key", key)
    monkeypatch.setattr("jarvis.ui.mcp_panel.McpConnector", client)
    return server


def configure(panel: McpPanel) -> None:
    panel.name.setText("mcp_probe")
    panel.origin.setText(ORIGIN)
    panel.endpoint.setText(PATH)
    QTest.mouseClick(panel.save_button, Qt.MouseButton.LeftButton)


def review(panel: McpPanel, qtbot: QtBot, levels: dict[str, Risk]) -> None:
    QTest.mouseClick(panel.read_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.worker is None)
    for remote, risk in levels.items():
        box = panel.levels[remote]
        box.setCurrentIndex(box.findData(risk.name))
    QTest.mouseClick(panel.approve_button, Qt.MouseButton.LeftButton)


def test_a_server_becomes_usable_only_after_the_owner_sets_the_levels(
    qtbot: QtBot, tmp_path: Path, fake: FakeServer
) -> None:
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    window.show()
    panel = window.mcp
    window.tabs.setCurrentIndex(4)
    try:
        configure(panel)
        assert load_servers(tmp_path / "mcp.json")[0].origin == ORIGIN
        review(panel, qtbot, {"search-issues": Risk.ROUTINE, "create_issue": Risk.CONFIRM})
        assert "перезапуска" in panel.status.text()
        saved = load_servers(tmp_path / "mcp.json")[0]
        assert saved.current and {tool.risk for tool in saved.tools} == {
            Risk.ROUTINE,
            Risk.CONFIRM,
        }
        # The session that did the reviewing does not gain the tools behind its own back.
        assert window.registry.get(TOOL) is None
    finally:
        window.shutdown()
    reopened = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(reopened)
    try:
        tool = reopened.registry.get(TOOL)
        assert tool is not None and tool.risk is Risk.ROUTINE
        assert "mcp_probe" in reopened.bench.connectors.services()
        # The description the planner reads is the one the owner passed, plus the fields.
        assert tool.description == "Найти задачи по запросу. Аргументы: query."
    finally:
        reopened.shutdown()


def test_an_unreviewed_server_gives_the_planner_nothing(
    qtbot: QtBot, tmp_path: Path, fake: FakeServer
) -> None:
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    panel = window.mcp
    try:
        configure(panel)
        QTest.mouseClick(panel.read_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: panel.worker is None)
        assert panel.draft is not None and not panel.draft.current
        # Read but not approved: the file holds no pin, so the tools do not exist.
        assert not load_servers(tmp_path / "mcp.json")[0].current
    finally:
        window.shutdown()
    reopened = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(reopened)
    try:
        assert reopened.registry.get(TOOL) is None
    finally:
        reopened.shutdown()


def test_a_reworded_tool_sends_the_server_back_for_review(
    qtbot: QtBot, tmp_path: Path, fake: FakeServer
) -> None:
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    panel = window.mcp
    try:
        configure(panel)
        review(panel, qtbot, {"search-issues": Risk.ROUTINE})
        assert load_servers(tmp_path / "mcp.json")[0].current
        fake.listed = {
            "tools": [dict(LISTED["tools"][0], description="Теперь ещё и удаляет задачи.")]
        }
        QTest.mouseClick(panel.read_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: panel.worker is None)
        assert panel.draft is not None and not panel.draft.current
        # The level chosen before is offered again, but nothing is approved by itself.
        assert panel.draft.tools[0].risk is Risk.ROUTINE
        assert not load_servers(tmp_path / "mcp.json")[0].current
    finally:
        window.shutdown()


def test_the_owner_can_take_a_server_back(qtbot: QtBot, tmp_path: Path, fake: FakeServer) -> None:
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    panel = window.mcp
    try:
        configure(panel)
        review(panel, qtbot, {"search-issues": Risk.ROUTINE})
        QTest.mouseClick(panel.revoke_button, Qt.MouseButton.LeftButton)
        assert not load_servers(tmp_path / "mcp.json")[0].current
        QTest.mouseClick(panel.remove_button, Qt.MouseButton.LeftButton)
        assert load_servers(tmp_path / "mcp.json") == ()
    finally:
        window.shutdown()


def test_a_server_that_cannot_be_read_says_so_and_changes_nothing(
    qtbot: QtBot, tmp_path: Path, fake: FakeServer
) -> None:
    window = PlannerWindow(AppConfig(tmp_path))
    qtbot.addWidget(window)
    panel = window.mcp
    try:
        configure(panel)
        fake.failure = "refused"
        QTest.mouseClick(panel.read_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: panel.worker is None)
        assert "отклонил" in panel.status.text()
        assert not panel.levels and not load_servers(tmp_path / "mcp.json")[0].tools
    finally:
        window.shutdown()
