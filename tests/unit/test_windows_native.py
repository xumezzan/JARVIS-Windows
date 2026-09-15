"""Native adapter decision logic with fake UIA objects; not native Windows acceptance."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from tests.windows_support import target

from jarvis.platforms.windows.native import NativeDesktop
from jarvis.tools.base import ToolError
from jarvis.tools.windows import NativeRequest, text_digest


@pytest.fixture
def desktop() -> NativeDesktop:
    adapter = NativeDesktop.__new__(NativeDesktop)
    adapter.desktop = Mock()
    adapter.user32 = Mock()
    adapter.psutil = Mock()
    return adapter


@pytest.mark.parametrize(
    "field,value",
    [
        ("pid", 999),
        ("process_started", 4567.0),
        ("runtime_id", [8]),
        ("title", "changed"),
        ("executable", "C:\\wrong.exe"),
    ],
)
def test_native_rechecks_identity(
    desktop: NativeDesktop,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    monkeypatch.setattr(
        desktop, "snapshot", lambda *args, **kwargs: target().model_copy(update={field: value})
    )
    with pytest.raises(ToolError, match="target_changed"):
        desktop.resolve(target())


def test_nonempty_editor_is_never_modified(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop, "resolve", lambda *args, **kwargs: Mock())
    monkeypatch.setattr(desktop, "editor", lambda *args: Mock())
    monkeypatch.setattr(desktop, "editor_target", lambda *args: target().editor)
    monkeypatch.setattr(desktop, "read", lambda *args: "unsaved user content")
    with pytest.raises(ToolError, match="target_changed"):
        desktop.dispatch(NativeRequest(operation="type", target=target(), text="new"))
    desktop.user32.SendMessageTimeoutW.assert_not_called()


def test_tab_switch_denied(desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch) -> None:
    editor_target = target().editor
    assert editor_target is not None
    monkeypatch.setattr(desktop, "resolve", lambda *args, **kwargs: Mock())
    monkeypatch.setattr(desktop, "editor", lambda *args: Mock())
    monkeypatch.setattr(
        desktop,
        "editor_target",
        lambda *args: editor_target.model_copy(update={"selected_tabs": [[999]]}),
    )
    with pytest.raises(ToolError, match="target_changed"):
        desktop.resolve_editor(target(), empty=True)


def test_literal_directed_message_and_readback(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = Mock(handle=101)
    monkeypatch.setattr(desktop, "resolve_editor", lambda *args, **kwargs: editor)
    text = "Жарвис {ENTER}\nsecond line"
    monkeypatch.setattr(desktop, "read", lambda *args: text)
    result = desktop.dispatch(NativeRequest(operation="type", target=target(), text=text))
    assert result.text_sha256 == text_digest(text)
    call = desktop.user32.SendMessageTimeoutW.call_args.args
    assert call[:3] == (101, 0xC2, 1)
    assert call[4:6] == (2, 1000)
    desktop.user32.SetForegroundWindow.assert_not_called()


def test_executable_allowlist_no_path_search(
    desktop: NativeDesktop,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("SYSTEMROOT", "PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        monkeypatch.setenv(name, str(tmp_path / name))
    path = tmp_path / "SYSTEMROOT/System32/notepad.exe"
    path.parent.mkdir(parents=True)
    path.touch()
    assert desktop.executable("notepad") == path
    assert desktop.app_for(str(path)) == "notepad"
    assert desktop.app_for(str(tmp_path / "untrusted/notepad.exe")) is None
    with pytest.raises(ToolError, match="application_missing"):
        desktop.executable("chrome")


def test_ambiguous_editor_fails_closed(desktop: NativeDesktop) -> None:
    def editor() -> Mock:
        return Mock(element_info=Mock(control_type="Edit", class_name="Edit"))

    window = Mock(element_info=Mock(class_name="Notepad"))
    window.descendants.return_value = [editor(), editor()]
    with pytest.raises(ToolError, match="control_unsupported"):
        desktop.editor(window)


def test_notepad_dialog_is_not_a_document_target(desktop: NativeDesktop) -> None:
    window = Mock(element_info=Mock(class_name="#32770"))
    with pytest.raises(ToolError, match="control_unsupported"):
        desktop.editor(window)
    window.descendants.assert_not_called()
