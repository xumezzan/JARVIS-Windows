"""Native adapter decision logic with fake UIA objects; not native Windows acceptance."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from tests.windows_support import target

from jarvis.platforms.windows.native import LaunchEntry, NativeDesktop
from jarvis.tools.base import ToolError
from jarvis.tools.windows import NativeRequest, TypeText


@pytest.fixture
def desktop() -> NativeDesktop:
    adapter = NativeDesktop.__new__(NativeDesktop)
    adapter.desktop = Mock()
    adapter.user32 = Mock()
    adapter.psutil = Mock()
    return adapter


def test_nonempty_editor_is_never_modified(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop, "resolve", lambda *args, **kwargs: Mock())
    monkeypatch.setattr(desktop, "controls", lambda *args: [Mock()])
    monkeypatch.setattr(desktop, "writable", lambda *args: True)
    monkeypatch.setattr(desktop, "editor_target", lambda *args: target().editor)
    monkeypatch.setattr(desktop, "read", lambda *args: "unsaved user content")
    with pytest.raises(ToolError, match="target_changed"):
        desktop.dispatch(NativeRequest(operation="type", target=target(), text="new"))
    desktop.user32.SendMessageTimeoutW.assert_not_called()


def test_tab_switch_denied(desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch) -> None:
    editor_target = target().editor
    assert editor_target is not None
    monkeypatch.setattr(desktop, "resolve", lambda *args, **kwargs: Mock())
    monkeypatch.setattr(desktop, "controls", lambda *args: [Mock()])
    monkeypatch.setattr(desktop, "writable", lambda *args: True)
    monkeypatch.setattr(
        desktop,
        "editor_target",
        lambda *args: editor_target.model_copy(update={"selected_tabs": [[999]]}),
    )
    with pytest.raises(ToolError, match="target_changed"):
        desktop.resolve_editor(TypeText(target=target(), text="new"))


def test_executable_allowlist_no_path_search(
    desktop: NativeDesktop,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "trusted/notepad.exe"
    monkeypatch.setattr(desktop, "catalog", lambda: [LaunchEntry("Notepad", path, "notepad")])
    assert desktop.resolve_app("notepad").path == path
    for untrusted in ("chrome", "cmd", "powershell", str(tmp_path / "untrusted/notepad.exe")):
        with pytest.raises(ToolError):
            desktop.resolve_app(untrusted)


def test_window_without_editable_controls_is_not_a_typing_target(desktop: NativeDesktop) -> None:
    window = Mock(element_info=Mock(class_name="#32770"), descendants=Mock(return_value=[]))
    with pytest.raises(ToolError, match="control_unsupported"):
        desktop.editor(window)
    assert window.descendants.call_count == 2


def test_window_listing_stops_at_the_reply_schema_limit(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    windows = [Mock(handle=index + 1) for index in range(101)]
    for window in windows:
        window.window_text.return_value = "Observed window"
        window.element_info.class_name = "FixtureWindow"
    desktop.desktop.windows.return_value = windows
    monkeypatch.setattr(
        desktop,
        "snapshot",
        lambda window, **kwargs: target().model_copy(update={"handle": window.handle}),
    )
    result = desktop.windows(include_editor=False)
    assert len(result) == 100 and [item.handle for item in result] == list(range(1, 101))
