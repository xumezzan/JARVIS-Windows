"""Observed Chromium viewport geometry for the display-only ghost overlay.

This module does not generate input. The browser executor still owns all DOM
actions, target checks and approvals. Geometry is in macOS logical screen points.
"""

import json
import sys
from collections import deque
from pathlib import Path
from typing import Any

from jarvis.platforms import is_macos
from jarvis.platforms.macos.native import MAX_NODES, NativeDesktop
from jarvis.platforms.windows.transport import NativeReply
from jarvis.tools.base import ToolError
from jarvis.tools.windows import MAX_UIA_DEPTH, Rectangle, WindowTarget

BROWSER_BUNDLES = {
    "Google Chrome.app": "Google Chrome",
    "Microsoft Edge.app": "Microsoft Edge",
    "Brave Browser.app": "Brave Browser",
    "Chromium.app": "Chromium",
}


def chromium_executable(value: str) -> bool:
    """Match the browser's own bundle executable, never a helper or a title."""
    path = Path(value)
    return (
        path.is_absolute()
        and path.parent.name == "MacOS"
        and path.parent.parent.name == "Contents"
        and BROWSER_BUNDLES.get(path.parent.parent.parent.name) == path.name
    )


def intersection(first: Rectangle, second: Rectangle) -> Rectangle:
    left, top = max(first.left, second.left), max(first.top, second.top)
    right, bottom = min(first.right, second.right), min(first.bottom, second.bottom)
    if right <= left or bottom <= top:
        raise ToolError("control_unsupported")
    return Rectangle(left=left, top=top, right=right, bottom=bottom)


def viewport_geometry(
    desktop: NativeDesktop, window: Any, target: WindowTarget, bounds: Rectangle
) -> tuple[Rectangle, tuple[int, ...]]:
    """Find one visible top-level WebArea without traversing page content.

    AXWebArea may describe a scrolled document larger than its viewport. Its
    observed ancestor scroll areas supply the clipping rectangles. No toolbar
    height, physical pixel scale or browser outerHeight is inferred.
    """
    pending: deque[tuple[Any, tuple[int, ...], int, Rectangle, bool]] = deque(
        [(window, (), 0, bounds, False)]
    )
    candidates: list[tuple[Rectangle, tuple[int, ...]]] = []
    visited = 0
    while pending:
        value, path, depth, clipped, scroll_viewport = pending.popleft()
        visited += 1
        if visited > MAX_NODES:
            raise ToolError("control_unsupported")
        if desktop.password(value) or desktop.attr(value, "AXHidden", False):
            continue
        role = desktop.attr(value, "AXRole")
        if role == "AXWebArea":
            if desktop.attr(value, "AXVisible", True):
                document_bounds = desktop.bounds(value)
                visible_bounds = intersection(clipped, document_bounds)
                if not scroll_viewport and visible_bounds != document_bounds:
                    # A scrolled document alone does not identify where browser chrome
                    # ends. Require an observed ancestor viewport instead of guessing.
                    raise ToolError("control_unsupported")
                candidates.append((visible_bounds, path))
            # Iframes are descendants of this area, not alternative browser viewports.
            continue
        if role == "AXScrollArea":
            clipped = intersection(clipped, desktop.bounds(value))
            scroll_viewport = True
        children = desktop.attr(value, "AXChildren", ())
        if children and (
            depth >= MAX_UIA_DEPTH or len(children) + len(pending) + visited > MAX_NODES
        ):
            raise ToolError("control_unsupported")
        for index, child in enumerate(children):
            pending.append((child, (*path, index), depth + 1, clipped, scroll_viewport))
    if len(candidates) != 1:
        raise ToolError("control_unsupported")
    return candidates[0]


def browser_viewport(desktop: NativeDesktop | None = None) -> dict[str, object]:
    """Return a reobserved native content viewport through the existing JSON shape."""
    desktop = desktop or NativeDesktop()
    window, target = desktop.active()
    if not chromium_executable(target.executable):
        raise ToolError("control_unsupported")
    bounds = desktop.row_bounds(desktop.resolve_row(target))
    viewport, path = viewport_geometry(desktop, window, target, bounds)
    desktop.require_foreground(target)
    current_window, current = desktop.active()
    current_bounds = desktop.row_bounds(desktop.resolve_row(current))
    if current.identity != target.identity or current_bounds != bounds:
        raise ToolError("target_changed")
    after, after_path = viewport_geometry(desktop, current_window, current, current_bounds)
    if after != viewport or after_path != path:
        raise ToolError("target_changed")
    return {"window_handle": target.handle, "rectangle": viewport.model_dump()}


def main() -> None:
    if not is_macos() or sys.argv[1:] != ["--viewport"]:
        raise SystemExit(1)
    try:
        encoded = json.dumps(browser_viewport(), separators=(",", ":"))
    except ToolError as error:
        encoded = NativeReply.model_validate(
            {"error": error.code, "reason": error.reason}
        ).model_dump_json()
    except Exception:
        encoded = NativeReply(error="native_failure").model_dump_json()
    print(encoded)


if __name__ == "__main__":
    main()
