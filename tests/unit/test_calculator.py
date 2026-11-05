"""Legacy scenario tools are absent; arbitrary tasks use the universal tool catalog."""

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import (
    ClickElement,
    NativeRequest,
    OpenApp,
    SendHotkey,
    TypeText,
    register_windows,
)


def test_scenario_specific_tools_are_not_registered() -> None:
    registry = ToolRegistry()
    register_windows(registry, Mock())
    names = {str(tool["name"]) for tool in registry.discover()}
    assert not any("calculator" in name or "notepad" in name for name in names)
    assert {
        "windows.open_app",
        "windows.focus_app",
        "windows.inspect_active_window",
        "windows.screenshot",
        "windows.click_element",
        "windows.type_text",
        "windows.send_hotkey",
        "windows.clipboard_get",
        "windows.clipboard_set",
    } <= names
    with pytest.raises(ValidationError):
        NativeRequest.model_validate({"operation": "calculator_observe"})


@pytest.mark.parametrize(
    "query",
    [
        "任意のアプリ",
        "Новый редактор 2027",
        "/Applications/Custom App.app",
        r"C:\Program Files\Vendor\Unknown.exe",
    ],
)
def test_application_queries_have_no_product_allowlist(query: str) -> None:
    assert OpenApp.model_validate({"query": query}).app == query


def test_custom_window_input_does_not_require_a_text_pattern() -> None:
    assert TypeText(text="Произвольный текст").field is None
    assert SendHotkey(hotkey="Ctrl+Shift+S").target is None
    click = ClickElement(x=30, y=50)
    assert click.target is None and click.window_rectangle is None
