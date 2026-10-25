"""The active profile follows the real Qt and Electron composition paths."""

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from jarvis.config import AppConfig
from jarvis.core.planner.contracts import PlannerInput, Proposal
from jarvis.desktop.service import DesktopService
from jarvis.permissions.policies import Mode
from jarvis.ui.compact_planner import PlannerWindow as CompactPlannerWindow
from jarvis.ui.planner_window import PlannerWindow
from jarvis.workspace.presets import omadli_group_preset


class CaptureProvider:
    def __init__(self) -> None:
        self.inputs: list[PlannerInput] = []

    async def propose(self, data: PlannerInput) -> Proposal:
        self.inputs.append(data)
        return Proposal(kind="finish")


@pytest.mark.parametrize("surface", [PlannerWindow, CompactPlannerWindow])
def test_qt_profile_reaches_worker_and_browser(
    qtbot: QtBot, tmp_path: Path, surface: type[PlannerWindow] | type[CompactPlannerWindow]
) -> None:
    profile = omadli_group_preset(omadli_group_url="https://erp.example.com")
    provider = CaptureProvider()
    window = surface(AppConfig(tmp_path, workspace_profile=profile), provider=provider)
    qtbot.addWidget(window)
    window.show()
    try:
        qtbot.waitUntil(
            lambda: window.memory.profile_read and window.memory.worker is None, timeout=15000
        )
        assert "https://erp.example.com" in window.host.policy.allowed_origins
        assert "профиль" in window.cloud_consent.text()
        window.command.setPlainText("Подготовь план на день")
        with qtbot.waitSignal(window.task_finished, timeout=15000):
            window.start()
        assert len(provider.inputs) == 1
        assert provider.inputs[0].workspace == profile.planner_context()
        assert window.host._thread is None, "Loading metadata must not open a real browser."
    finally:
        window.shutdown()


@pytest.mark.asyncio
async def test_desktop_profile_reaches_runner_and_browser(tmp_path: Path) -> None:
    profile = omadli_group_preset(omadli_group_url="https://erp.example.com")
    provider = CaptureProvider()
    service = DesktopService(
        AppConfig(tmp_path, workspace_profile=profile), lambda event: None, provider=provider
    )
    try:
        assert "https://erp.example.com" in service.host.policy.allowed_origins
        assert service.start("Подготовь план на день", Mode.SIMULATION)
        assert service.worker is not None
        await service.worker
        assert provider.inputs[0].workspace == profile.planner_context()
        assert service.host._thread is None
    finally:
        await service.shutdown()
