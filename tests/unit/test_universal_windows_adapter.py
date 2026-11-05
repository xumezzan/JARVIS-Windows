"""Generic Windows adapter regressions with synthetic OS objects, not hardware acceptance."""

import importlib
from pathlib import Path
from unittest.mock import Mock

import pytest
from tests.windows_support import target

from jarvis.platforms.windows.native import NativeDesktop, app_key, hotkey_sequence
from jarvis.tools.base import ToolError
from jarvis.tools.windows import NativeRequest, TypeText, text_digest


def adapter() -> NativeDesktop:
    result = NativeDesktop.__new__(NativeDesktop)
    result.desktop = Mock()
    result.psutil = Mock()
    result.user32 = Mock()
    result.user32.GetClipboardSequenceNumber.return_value = 7
    return result


def test_absolute_app_path_launches_as_one_argument_without_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop = adapter()
    executable = tmp_path / "Unanticipated Application 你好.exe"
    executable.touch()
    launcher = Mock()
    monkeypatch.setattr("jarvis.platforms.windows.native.subprocess.Popen", launcher)
    entry = desktop.resolve_app(str(executable))
    desktop.launch(entry)
    assert launcher.call_args.args == ([str(executable)],)
    assert launcher.call_args.kwargs["shell"] is False
    for suffix in (".txt", ".bat", ".cmd", ".ps1", ".py"):
        document = tmp_path / ("document" + suffix)
        document.touch()
        with pytest.raises(ToolError, match="application_missing"):
            desktop.resolve_app(str(document))


@pytest.mark.parametrize("name", ["画像編集", "Oʻzbekcha Ҳисоб", "应用程序"])
def test_executable_identity_preserves_all_unicode_application_names(name: str) -> None:
    result = app_key("C:\\Applications\\" + name + ".exe")
    assert result != "unknown"
    assert all(character in result for character in name.casefold() if character.isalnum())


@pytest.mark.parametrize(
    "chord,expected",
    [
        ("Ctrl+Shift+S", "^+s"),
        ("Cmd+F12", "^{F12}"),
        ("Option+ArrowLeft", "%{LEFT}"),
        ("Meta+E", "{VK_LWIN down}e{VK_LWIN up}"),
        ("Escape", "{ESC}"),
    ],
)
def test_generic_hotkeys_compile_only_bounded_chords(chord: str, expected: str) -> None:
    assert hotkey_sequence(chord) == expected


@pytest.mark.parametrize("chord", ["^a{ENTER}", "Ctrl+Ctrl+A", "Ctrl+run.exe", "F25", ""])
def test_hotkeys_never_accept_raw_keyboard_scripts(chord: str) -> None:
    with pytest.raises(ToolError):
        hotkey_sequence(chord)


@pytest.mark.parametrize("fail", [False, True])
def test_custom_control_paste_restores_clipboard_and_requires_observation_afterwards(
    monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    desktop = adapter()
    observed = target().model_copy(update={"editor": None})
    clipboard = Mock()
    ole, keyboard = Mock(), Mock()
    original_formats = object()
    ole.OleGetClipboard.return_value = original_formats
    if fail:
        keyboard.send_keys.side_effect = RuntimeError("uncertain native dispatch")
    real_import = importlib.import_module
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: (
            {"pythoncom": ole, "pywinauto.keyboard": keyboard}.get(name) or real_import(name)
        ),
    )
    monkeypatch.setattr(desktop, "active", lambda: (Mock(), observed))
    monkeypatch.setattr(desktop, "fallback_destination", lambda wanted: Mock())
    monkeypatch.setattr(desktop, "require_foreground", lambda wanted: None)
    monkeypatch.setattr(desktop, "wait_input_idle", lambda wanted: None)
    monkeypatch.setattr(desktop, "clipboard", clipboard)
    request = NativeRequest(operation="type", text="unanticipated application text")
    if fail:
        with pytest.raises(RuntimeError, match="uncertain"):
            desktop.dispatch(request)
    else:
        result = desktop.dispatch(request)
        assert result.performed and result.target == observed
        assert not result.text_verified and result.text_sha256 is None
        assert result.inserted_text_sha256 == text_digest(request.text or "")
    clipboard.assert_called_once_with(request.text)
    keyboard.send_keys.assert_called_once_with("^v", pause=0)
    ole.OleSetClipboard.assert_called_once_with(original_formats)
    ole.OleFlushClipboard.assert_called_once()


def test_new_user_clipboard_content_is_never_overwritten_by_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = adapter()
    desktop.user32.GetClipboardSequenceNumber.side_effect = [7, 8]
    ole, keyboard = Mock(), Mock()
    real_import = importlib.import_module
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: (
            {"pythoncom": ole, "pywinauto.keyboard": keyboard}.get(name) or real_import(name)
        ),
    )
    monkeypatch.setattr(desktop, "fallback_destination", lambda wanted: Mock())
    monkeypatch.setattr(desktop, "require_foreground", lambda wanted: None)
    monkeypatch.setattr(desktop, "wait_input_idle", lambda wanted: None)
    monkeypatch.setattr(desktop, "clipboard", lambda text: text)
    desktop.paste_text(TypeText(target=target(), text="new"))
    ole.OleSetClipboard.assert_not_called()


@pytest.mark.parametrize("password,read_only", [(True, False), (False, True)])
def test_paste_fallback_keeps_known_password_and_read_only_guards(
    monkeypatch: pytest.MonkeyPatch, password: bool, read_only: bool
) -> None:
    desktop = adapter()
    focused = Mock()
    focused.process_id.return_value = target().pid
    focused.element_info.element.CurrentIsPassword = password
    focused.iface_value.CurrentIsReadOnly = read_only
    monkeypatch.setattr(desktop, "activate", lambda wanted: Mock())
    monkeypatch.setattr(desktop, "focused_control", lambda: focused)
    with pytest.raises(ToolError, match="control_unsupported"):
        desktop.fallback_destination(target())
