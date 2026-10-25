"""Actual image rendering and protocol bounds; no claim of native Windows capture."""

import base64
import importlib
import io
import random
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from tests.unit.test_windows_cross_app import adapter, control
from tests.windows_support import target

from jarvis.config import AppConfig
from jarvis.core.planner.openai_provider import OpenAIProvider
from jarvis.desktop.service import DesktopService
from jarvis.permissions.policies import Risk
from jarvis.platforms.windows.native import NativeDesktop
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import (
    MAX_UIA_OBSERVATION_BYTES,
    ClickElement,
    ElementTarget,
    Rectangle,
    WindowsResult,
    register_windows,
    uia_metadata_bytes,
)


def test_som_schemas_reject_oversized_names_marks_and_element_lists() -> None:
    element = ElementTarget(
        runtime_id=[1],
        name="x" * 120,
        control_type="Button",
        automation_id="",
        rectangle=Rectangle(left=0, top=0, right=10, bottom=10),
    )
    for fields in ({"mark_id": 0}, {"mark_id": 97}, {"name": "x" * 121}):
        with pytest.raises(ValidationError):
            ElementTarget.model_validate(element.model_dump() | fields)
    with pytest.raises(ValidationError):
        WindowsResult(elements=[element] * 97)
    with pytest.raises(ValidationError):
        ClickElement(target=target(), element=element, mark_id=1)


def test_inspection_truncates_labels_without_downgrading_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = adapter()
    window = control("Window")
    button = control(name="x" * 120 + " Delete")
    window.children.return_value = [button]
    monkeypatch.setattr(desktop, "active", lambda: (window, target()))
    monkeypatch.setattr(desktop, "resolve", lambda *args: window)
    result = desktop.inspect(5)
    assert result.elements[0].label_truncated
    assert len(result.elements[0].name) == 120
    assert result.elements[0].name_sha256 is not None
    button.window_text.return_value = "x" * 120 + " Send"
    changed = desktop.element_snapshot(button)
    assert changed.name == result.elements[0].name
    assert changed.name_sha256 != result.elements[0].name_sha256
    assert (
        changed != result.elements[0]
    )  # Native click revalidation must reject the changed suffix.
    assert uia_metadata_bytes(result) <= MAX_UIA_OBSERVATION_BYTES
    registry = ToolRegistry()
    register_windows(registry, Mock())
    tool = registry.get("windows.click_element")
    assert tool is not None
    normalized = tool.normalize(
        {"target": target().model_dump(), "element": result.elements[0].model_dump()}
    )
    assert tool.classify_risk(normalized) is Risk.CONFIRM


def test_inspection_bounds_long_unicode_metadata_not_only_element_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = adapter()
    window = control("Window")
    children = [control(name="😀" * 120) for _ in range(96)]
    for i, child in enumerate(children):
        child.element_info.automation_id = "😀" * 500
        child.element_info.runtime_id = [1, i]
    window.children.return_value = children
    monkeypatch.setattr(desktop, "active", lambda: (window, target()))
    monkeypatch.setattr(desktop, "resolve", lambda *args: window)
    result = desktop.inspect(5)
    assert 0 < len(result.elements) < 96 and result.truncated
    assert uia_metadata_bytes(result) <= MAX_UIA_OBSERVATION_BYTES


def test_rendered_marks_use_window_offset_and_resized_coordinates() -> None:
    from PIL import Image

    image = Image.new("RGB", (800, 400), "white")
    observed = WindowsResult(
        window_rectangle=Rectangle(left=-1600, top=100, right=0, bottom=900),
        elements=[
            ElementTarget(
                mark_id=7,
                runtime_id=[7],
                name="Open",
                control_type="Button",
                automation_id="open",
                rectangle=Rectangle(left=-1400, top=300, right=-1000, bottom=700),
            ),
        ],
    )
    result = NativeDesktop._mark_image(image, observed)
    # x=(screen_x-window_left)*0.5; y=(screen_y-window_top)*0.5.
    assert result.getpixel((100, 150)) == (255, 230, 0)
    assert result.getpixel((300, 150)) == (255, 230, 0)
    assert image.getpixel((100, 150)) == (255, 255, 255)
    assert result.crop((100, 100, 130, 118)).getextrema()[0][0] == 0  # Black [7] text.


@pytest.mark.parametrize("noisy", [False, True])
def test_active_capture_contains_current_marks_and_bounded_real_jpeg(
    monkeypatch: pytest.MonkeyPatch, noisy: bool
) -> None:
    from PIL import Image

    desktop = adapter()
    window = control("Window")
    window.children.return_value = [control()]
    monkeypatch.setattr(desktop, "active", lambda: (window, target()))
    monkeypatch.setattr(desktop, "resolve", lambda *args: window)
    grab = Mock()
    grab.grab.return_value = (
        Image.frombytes("RGB", (1800, 1200), random.Random(42).randbytes(1800 * 1200 * 3))
        if noisy
        else Image.new("RGB", (1800, 1200), "white")
    )
    real_import = importlib.import_module
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: grab if name == "PIL.ImageGrab" else real_import(name),
    )
    result = desktop.capture("active_window")
    assert result.target == target() and result.window_rectangle is not None
    assert len(result.elements) == 1 and result.elements[0].mark_id == 1
    assert result.image_base64 is not None
    jpeg = base64.b64decode(result.image_base64, validate=True)
    assert len(jpeg) <= 49152
    assert Image.open(io.BytesIO(jpeg)).format == "JPEG"
    grab.grab.assert_called_once()


@pytest.mark.asyncio
async def test_maximal_som_image_and_actual_catalog_fit_180k(tmp_path: Path) -> None:
    # Import the same no-network probe used for manual verification.
    import runpy

    probe = runpy.run_path(str(Path(__file__).parents[2] / "scripts/probe_multimodal_budget.py"))
    service = DesktopService(AppConfig(tmp_path), lambda event: None)
    try:
        size = probe["measure"](service.registry)
        assert size < 180_000
        payload, _ = OpenAIProvider("probe")._request_payload(probe["worst_case"](service.registry))
        assert len(payload["input"][0]["content"][1]["image_url"].split(",")[1]) == 65536
    finally:
        await service.shutdown()
