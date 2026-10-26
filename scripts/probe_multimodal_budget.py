"""No-network probe of actual desktop/Qt catalogs with maximal SoM name/image fields."""

import asyncio
import base64
import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from jarvis.config import AppConfig
from jarvis.core.planner.contracts import PlannerInput, Step
from jarvis.core.planner.openai_provider import OpenAIProvider, payload_bytes
from jarvis.desktop.service import DesktopService
from jarvis.mcp.models import McpServerConfig, McpToolDefinition
from jarvis.permissions.engine import Outcome
from jarvis.permissions.policies import Mode, Status
from jarvis.security.browser_policy import BrowserMode
from jarvis.tools.base import canonical
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import (
    MAX_SCREENSHOT_JPEG_BYTES,
    MAX_UIA_OBSERVATION_BYTES,
    ElementTarget,
    Rectangle,
    WindowsResult,
    WindowTarget,
    uia_metadata_bytes,
)
from jarvis.workspace.models import UserIdentity, WorkspaceProfile
from jarvis.workspace.presets import omadli_group_preset


def filled_profile() -> WorkspaceProfile:
    """Synthetic owner data and one reviewed MCP schema; never contacts the service."""
    tool = McpToolDefinition(
        name="lookup",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    server = McpServerConfig(
        server_id="budget_probe",
        name="Budget probe",
        transport="http",
        url="https://example.com/mcp",
        allowed_tools=("lookup",),
    ).with_review((tool,))
    profile = omadli_group_preset(
        omadli_group_url="https://portal.example.com/operations",
        user=UserIdentity(
            name="Тестовый пользователь",
            organization="Omadli Group",
            role="Менеджер проектов",
            tasks_description="Готовить отчёты, встречи и ответы; публикации подтверждать.",
            email_tone="Деловой, краткий",
            social_comments_tone="Дружелюбный",
        ),
    )
    return WorkspaceProfile.model_validate(
        profile.model_dump() | {"mcp_servers": (server,), "browser_mode": "authenticated_workspace"}
    )


def worst_case(registry: ToolRegistry, profile: WorkspaceProfile | None = None) -> PlannerInput:
    """96 maximal 4-byte Unicode names; 48 KiB image is size data, not a claimed capture."""
    bounds = Rectangle(left=-1920, top=100, right=0, bottom=1180)
    target = WindowTarget(
        app="budget-probe",
        handle=100,
        pid=200,
        process_started=1234.0,
        executable="C:\\Program Files\\BudgetProbe\\probe.exe",
        runtime_id=[1, 2],
        title="Probe",
    )
    observation = WindowsResult(
        target=target,
        window_rectangle=bounds,
        width=1280,
        height=720,
        image_format="JPEG",
        elements=[
            ElementTarget(
                mark_id=i,
                runtime_id=[100, i],
                name="😀" * 120,
                control_type="Button",
                automation_id="action",
                rectangle=Rectangle(left=-1800, top=200, right=-1600, bottom=250),
            )
            for i in range(1, 97)
        ],
    )
    assert uia_metadata_bytes(observation) <= MAX_UIA_OBSERVATION_BYTES
    return PlannerInput(
        "budget probe",
        (),
        (
            Step(
                "windows.screenshot",
                Outcome(
                    uuid4(),
                    Status.SUCCESS,
                    result_json=canonical(observation),
                ),
            ),
        ),
        Mode.EXECUTE,
        json.dumps(registry.discover(), ensure_ascii=False),
        screenshot_b64=base64.b64encode(bytes(MAX_SCREENSHOT_JPEG_BYTES)).decode(),
        workspace=(profile or filled_profile()).planner_context(),
    )


def measure(registry: ToolRegistry, *, ceiling: int = 200_000) -> int:
    payload, _ = OpenAIProvider("probe-no-network")._request_payload(worst_case(registry))
    size = len(payload_bytes(payload))
    assert size < ceiling, f"Payload budget regression: {size} bytes >= {ceiling}"
    return size


async def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Avoid configuring any model or audio device just to discover tool schemas.
    os.environ.pop("JARVIS_VOSK_MODEL", None)
    from PySide6.QtWidgets import QApplication

    from jarvis.ui.compact_planner import PlannerWindow as CompactPlannerWindow
    from jarvis.ui.planner_window import PlannerWindow

    app = QApplication([])
    with TemporaryDirectory(prefix="jarvis-budget-") as directory:
        root = Path(directory)
        config = AppConfig(
            root, workspace_profile=filled_profile(), browser_mode=BrowserMode.WORKSPACE
        )
        qt_root = root / "qt"
        qt_root.mkdir()
        window = PlannerWindow(replace(config, data_dir=qt_root))
        compact_root = root / "compact"
        compact_root.mkdir()
        compact = CompactPlannerWindow(replace(config, data_dir=compact_root))
        service = DesktopService(replace(config, data_dir=root / "desktop"), lambda event: None)
        try:
            for name, registry in (
                ("PlannerWindow + connectors", window.registry),
                ("CompactPlannerWindow", compact.registry),
                ("DesktopService", service.registry),
            ):
                print(
                    f"{name}: {measure(registry)} bytes < 200000 < 262144; "
                    f"tools={len(registry.discover())}; workspace_apps=14; "
                    "96 x 120 Unicode names; base64=65536"
                )
        finally:
            window.shutdown()
            compact.shutdown()
            await service.shutdown()
            window.deleteLater()
            compact.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    asyncio.run(main())
