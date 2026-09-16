"""Native adapter decision logic with fake UIA objects; not native Windows acceptance."""

import winreg
from pathlib import Path
from unittest.mock import Mock

import pytest
from tests.windows_support import target

from jarvis.platforms.windows import native
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


def installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *names: str) -> Path:
    """A throwaway Start menu plus System32, with the real registry kept out of the catalog."""
    programs = tmp_path / "APPDATA/Microsoft/Windows/Start Menu/Programs"
    programs.mkdir(parents=True, exist_ok=True)
    for variable in ("APPDATA", "PROGRAMDATA"):
        root = tmp_path / variable
        (root / "Microsoft/Windows/Start Menu/Programs").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(variable, str(root))
    system32 = tmp_path / "SYSTEMROOT/System32"
    system32.mkdir(parents=True, exist_ok=True)
    (system32 / "notepad.exe").touch()
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path / "SYSTEMROOT"))
    for name in names:
        (programs / f"{name}.lnk").touch()

    def unavailable(*args: object, **kwargs: object) -> None:
        raise OSError

    monkeypatch.setattr(winreg, "OpenKey", unavailable)
    return programs


def test_any_installed_application_resolves_by_its_own_name(
    desktop: NativeDesktop, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    programs = installed(tmp_path, monkeypatch, "Калькулятор", "Telegram Desktop")
    assert desktop.resolve_app("калькулятор").path == programs / "Калькулятор.lnk"
    assert desktop.resolve_app("telegram").name == "Telegram Desktop"
    # A spoken shorthand still reaches the system application when no shortcut matches.
    assert desktop.resolve_app("блокнот").path.name == "notepad.exe"
    with pytest.raises(ToolError, match="application_missing"):
        desktop.resolve_app("такого приложения нет")


def test_script_hosts_are_never_launchable_by_name(
    desktop: NativeDesktop, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed(tmp_path, monkeypatch, "Windows PowerShell", "Командная строка", "cmd")
    for name in ("powershell", "windows powershell", "командная строка", "cmd"):
        with pytest.raises(ToolError, match="application_missing"):
            desktop.resolve_app(name)


def test_ambiguous_application_name_fails_closed(
    desktop: NativeDesktop, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed(tmp_path, monkeypatch, "Photo Editor", "Photo Viewer")
    with pytest.raises(ToolError, match="application_ambiguous"):
        desktop.resolve_app("photo")
    assert desktop.resolve_app("photo editor").name == "Photo Editor"


def test_observed_window_identity_comes_from_its_executable() -> None:
    assert native.app_key("C:\\Windows\\System32\\notepad.exe") == "notepad"
    assert native.app_key("C:\\untrusted\\Notepad.exe") == "notepad"
    assert native.app_key("C:\\Program Files\\WindowsApps\\x\\CalculatorApp.exe") == (
        "calculatorapp"
    )
    # The key alone is never proof of identity: resolve() also compares the full path.
    assert target().executable.endswith("notepad.exe")


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
