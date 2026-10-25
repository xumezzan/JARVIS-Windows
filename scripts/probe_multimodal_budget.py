"""No-network probe of actual desktop/Qt catalogs with maximal SoM name/image fields."""

import asyncio
import base64
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from jarvis.config import AppConfig
from jarvis.core.planner.contracts import PlannerInput, Step
from jarvis.core.planner.openai_provider import OpenAIProvider, payload_bytes
from jarvis.desktop.service import DesktopService
from jarvis.permissions.engine import Outcome
from jarvis.permissions.policies import Mode, Status
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


def worst_case(registry: ToolRegistry) -> PlannerInput:
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
    )


def measure(registry: ToolRegistry, *, ceiling: int = 180_000) -> int:
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
        qt_root = root / "qt"
        qt_root.mkdir()
        window = PlannerWindow(AppConfig(qt_root))
        compact_root = root / "compact"
        compact_root.mkdir()
        compact = CompactPlannerWindow(AppConfig(compact_root))
        service = DesktopService(AppConfig(root / "desktop"), lambda event: None)
        try:
            for name, registry, ceiling in (
                ("PlannerWindow + connectors", window.registry, 262_144),
                ("CompactPlannerWindow", compact.registry, 180_000),
                ("DesktopService", service.registry, 180_000),
            ):
                print(
                    f"{name}: {measure(registry, ceiling=ceiling)} bytes < {ceiling}; "
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
