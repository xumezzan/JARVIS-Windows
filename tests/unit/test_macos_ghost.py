"""macOS browser ghost viewport geometry, using portable fake AX observations."""

import json
import sys
from collections.abc import Callable
from typing import Any, cast

import pytest

from jarvis.observability.diagnostics import Reason
from jarvis.platforms.macos import ghost
from jarvis.platforms.macos.native import NativeDesktop
from jarvis.tools.base import ToolError
from jarvis.tools.windows import Rectangle, WindowTarget


def rect(left: int, top: int, right: int, bottom: int) -> Rectangle:
    return Rectangle(left=left, top=top, right=right, bottom=bottom)


def element(role: str, bounds: Rectangle | None = None, **attributes: Any) -> dict[str, Any]:
    return {"AXRole": role, "rectangle": bounds, **attributes}


class FakeDesktop:
    def __init__(self, root: dict[str, Any], bounds: Rectangle) -> None:
        self.root, self.window_bounds = root, bounds
        self.target = WindowTarget(
            app="Google Chrome",
            handle=99,
            pid=42,
            process_started=100.0,
            executable="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            runtime_id=[42, 99],
            title="Observed page",
        )
        self.on_foreground: Callable[[], None] | None = None

    def active(self) -> tuple[dict[str, Any], WindowTarget]:
        return self.root, self.target

    def attr(self, value: dict[str, Any], name: str, default: Any = None) -> Any:
        return value.get(name, default)

    def password(self, value: dict[str, Any]) -> bool:
        return value.get("AXRole") == "AXSecureTextField"

    def bounds(self, value: dict[str, Any]) -> Rectangle:
        if value.get("rectangle") is None:
            raise ToolError("control_unsupported")
        return cast(Rectangle, value["rectangle"])

    def resolve_row(self, target: WindowTarget) -> dict[str, Any]:
        return {}

    def row_bounds(self, row: dict[str, Any]) -> Rectangle:
        return self.window_bounds

    def require_foreground(self, target: WindowTarget) -> None:
        if self.on_foreground is not None:
            self.on_foreground()


def viewport(desktop: FakeDesktop) -> dict[str, object]:
    return ghost.browser_viewport(cast(NativeDesktop, desktop))


@pytest.mark.parametrize("bundle,binary", list(ghost.BROWSER_BUNDLES.items()))
def test_only_actual_chromium_bundle_executable_is_accepted(bundle: str, binary: str) -> None:
    assert ghost.chromium_executable(f"/Applications/{bundle}/Contents/MacOS/{binary}")
    assert not ghost.chromium_executable(f"/Applications/Other.app/Contents/MacOS/{binary}")
    assert not ghost.chromium_executable(f"/Applications/{bundle}/Contents/MacOS/{binary} Helper")
    assert not ghost.chromium_executable(binary)


def test_retina_and_negative_monitor_coordinates_are_already_logical_points() -> None:
    bounds = rect(-1920, 50, -640, 950)
    scrolling = rect(-1910, 170, -650, 940)
    document = element("AXWebArea", rect(-1910, -1000, -650, 5000))
    area = element("AXScrollArea", scrolling, AXChildren=[document])
    desktop = FakeDesktop(element("AXWindow", bounds, AXChildren=[area]), bounds)
    assert viewport(desktop) == {"window_handle": 99, "rectangle": scrolling.model_dump()}


def test_nested_scroll_ancestor_clips_the_exact_observed_viewport() -> None:
    bounds = rect(100, 100, 1300, 1000)
    outer = rect(110, 200, 1290, 990)
    inner = rect(300, 220, 1250, 950)
    web = element("AXWebArea", rect(0, 0, 5000, 5000))
    root = element(
        "AXWindow",
        bounds,
        AXChildren=[
            element(
                "AXScrollArea", outer, AXChildren=[element("AXScrollArea", inner, AXChildren=[web])]
            )
        ],
    )
    assert viewport(FakeDesktop(root, bounds))["rectangle"] == inner.model_dump()


def test_document_children_and_iframes_are_never_traversed() -> None:
    bounds = rect(0, 0, 1200, 900)
    content = rect(0, 120, 1200, 900)
    # An inaccessible page subtree must not be mistaken for a second viewport, or read.
    web = element("AXWebArea", content, AXChildren=[{}] * 10000)
    root = element(
        "AXWindow", bounds, AXChildren=[element("AXScrollArea", content, AXChildren=[web])]
    )
    assert viewport(FakeDesktop(root, bounds))["rectangle"] == content.model_dump()


def test_hidden_background_tab_is_not_the_selected_viewport() -> None:
    bounds = rect(0, 0, 1200, 900)
    content = rect(0, 120, 1200, 900)
    root = element(
        "AXWindow",
        bounds,
        AXChildren=[element("AXWebArea", content, AXHidden=True), element("AXWebArea", content)],
    )
    assert viewport(FakeDesktop(root, bounds))["rectangle"] == content.model_dump()


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_ambiguous_webarea_is_refused(count: int) -> None:
    bounds = rect(0, 0, 1200, 900)
    root = element(
        "AXWindow", bounds, AXChildren=[element("AXWebArea", bounds) for _ in range(count)]
    )
    with pytest.raises(ToolError, match="control_unsupported"):
        viewport(FakeDesktop(root, bounds))


def test_outside_viewport_is_refused_instead_of_guessing_toolbar_height() -> None:
    bounds = rect(0, 0, 1200, 900)
    root = element(
        "AXWindow", bounds, AXChildren=[element("AXWebArea", rect(2000, 2000, 3000, 3000))]
    )
    with pytest.raises(ToolError, match="control_unsupported"):
        viewport(FakeDesktop(root, bounds))


def test_scrolled_document_without_observed_scroll_viewport_is_refused() -> None:
    bounds = rect(0, 0, 1200, 900)
    root = element(
        "AXWindow", bounds, AXChildren=[element("AXWebArea", rect(0, -1000, 1200, 5000))]
    )
    with pytest.raises(ToolError, match="control_unsupported"):
        viewport(FakeDesktop(root, bounds))


def test_incomplete_browser_chrome_tree_does_not_claim_unique_area(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ghost, "MAX_NODES", 3)
    bounds = rect(0, 0, 1200, 900)
    root = element("AXWindow", bounds, AXChildren=[element("AXWebArea", bounds)] * 5)
    with pytest.raises(ToolError, match="control_unsupported"):
        viewport(FakeDesktop(root, bounds))


@pytest.mark.parametrize("change", ["window", "geometry", "viewport"])
def test_foreground_and_viewport_are_reobserved_before_publishing(change: str) -> None:
    bounds = rect(0, 0, 1200, 900)
    content = rect(0, 120, 1200, 900)
    web = element("AXWebArea", content)
    desktop = FakeDesktop(element("AXWindow", bounds, AXChildren=[web]), bounds)

    def changed() -> None:
        if change == "window":
            desktop.target = desktop.target.model_copy(update={"handle": 100})
        elif change == "geometry":
            desktop.window_bounds = rect(10, 10, 1210, 910)
        else:
            web["rectangle"] = rect(0, 150, 1200, 900)

    desktop.on_foreground = changed
    with pytest.raises(ToolError, match="target_changed"):
        viewport(desktop)


def test_nonbrowser_active_application_is_refused() -> None:
    bounds = rect(0, 0, 1200, 900)
    desktop = FakeDesktop(element("AXWebArea", bounds), bounds)
    desktop.target = desktop.target.model_copy(
        update={"executable": "/System/Applications/TextEdit.app/Contents/MacOS/TextEdit"}
    )
    with pytest.raises(ToolError, match="control_unsupported"):
        viewport(desktop)


def test_permission_error_propagates_without_geometry_guess() -> None:
    class Denied(FakeDesktop):
        def active(self) -> tuple[dict[str, Any], WindowTarget]:
            raise ToolError("control_unsupported")

    with pytest.raises(ToolError, match="control_unsupported"):
        viewport(Denied({}, rect(0, 0, 1, 1)))


def test_helper_returns_finite_permission_reason_over_shared_protocol(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "argv", ["ghost", "--viewport"])

    def denied() -> dict[str, object]:
        raise ToolError("control_unsupported", reason=Reason.MACOS_ACCESSIBILITY_REQUIRED)

    monkeypatch.setattr(ghost, "browser_viewport", denied)
    ghost.main()
    response = json.loads(capsys.readouterr().out)
    assert response["error"] == "control_unsupported"
    assert response["reason"] == "macos_accessibility_required"
