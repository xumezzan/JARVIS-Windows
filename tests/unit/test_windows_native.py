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


def prepared(
    desktop: NativeDesktop,
    monkeypatch: pytest.MonkeyPatch,
    *,
    editor: Mock,
    reads: tuple[str, ...] = ("",),
) -> None:
    """Point the adapter at one observed, writable field; reads are returned in order."""
    contents = list(reads)

    def read(*args: object) -> str:
        return contents.pop(0) if len(contents) > 1 else contents[0]

    monkeypatch.setattr(desktop, "resolve", lambda *args, **kwargs: Mock())
    monkeypatch.setattr(desktop, "controls", lambda *args: [editor])
    monkeypatch.setattr(desktop, "editor_target", lambda *args: target().editor)
    monkeypatch.setattr(desktop, "writable", lambda *args: True)
    monkeypatch.setattr(desktop, "read", read)


def test_existing_content_is_never_replaced_unless_asked(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = Mock(handle=101)
    prepared(desktop, monkeypatch, editor=editor, reads=("unsaved user content",))
    with pytest.raises(ToolError, match="target_changed"):
        desktop.dispatch(NativeRequest(operation="type", target=target(), text="new"))
    desktop.user32.SendMessageTimeoutW.assert_not_called()
    editor.iface_value.SetValue.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("selected_tabs", [[999]]),
        ("automation_id", "other"),
        ("class_name", "Other"),
        ("name", "Пароль"),
        ("control_type", "Document"),
    ],
)
def test_a_changed_field_identity_is_refused(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    editor_target = target().editor
    assert editor_target is not None
    monkeypatch.setattr(desktop, "controls", lambda *args: [Mock()])
    monkeypatch.setattr(
        desktop, "editor_target", lambda *args: editor_target.model_copy(update={field: value})
    )
    with pytest.raises(ToolError, match="target_changed"):
        desktop.chosen_editor(Mock(), editor_target)


def test_a_rebuilt_control_is_still_the_same_field(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Notepad hands out a new window handle between calls; that is not a different field."""
    editor_target = target().editor
    assert editor_target is not None
    control = Mock()
    monkeypatch.setattr(desktop, "controls", lambda *args: [control])
    monkeypatch.setattr(
        desktop,
        "editor_target",
        lambda *args: editor_target.model_copy(update={"handle": 999, "runtime_id": [7, 999]}),
    )
    assert desktop.chosen_editor(Mock(), editor_target) is control


def test_two_identical_fields_are_refused_instead_of_guessed(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor_target = target().editor
    assert editor_target is not None
    monkeypatch.setattr(desktop, "controls", lambda *args: [Mock(), Mock()])
    monkeypatch.setattr(desktop, "editor_target", lambda *args: editor_target)
    with pytest.raises(ToolError, match="target_changed"):
        desktop.chosen_editor(Mock(), editor_target)


def test_literal_directed_message_and_readback(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = Mock(handle=101)
    text = "Жарвис {ENTER}\nsecond line"
    prepared(desktop, monkeypatch, editor=editor, reads=(text,))
    monkeypatch.setattr(desktop, "native_handle", lambda *args: 101)
    result = desktop.dispatch(
        NativeRequest(operation="type", target=target(), text=text, overwrite=True)
    )
    assert result.text_sha256 == text_digest(text)
    calls = desktop.user32.SendMessageTimeoutW.call_args_list
    # Replacing selects the whole field first, so it replaces instead of inserting.
    assert [item.args[1] for item in calls] == [0xB1, 0xC2]
    assert calls[-1].args[:3] == (101, 0xC2, 1)
    assert calls[-1].args[4:6] == (2, 1000)
    desktop.user32.SetForegroundWindow.assert_not_called()
    editor.iface_value.SetValue.assert_not_called()


def test_an_empty_field_is_filled_without_selecting_anything(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = Mock(handle=101)
    text = "привет"
    prepared(desktop, monkeypatch, editor=editor, reads=("", text))
    monkeypatch.setattr(desktop, "native_handle", lambda *args: 101)
    desktop.dispatch(NativeRequest(operation="type", target=target(), text=text))
    assert [item.args[1] for item in desktop.user32.SendMessageTimeoutW.call_args_list] == [0xC2]


def test_a_field_without_its_own_window_uses_the_value_pattern(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Browser and Electron fields have no native window; keys are still never sent."""
    editor = Mock(handle=0)
    text = "привет"
    prepared(desktop, monkeypatch, editor=editor, reads=("", text))
    monkeypatch.setattr(desktop, "native_handle", lambda *args: 0)
    result = desktop.dispatch(NativeRequest(operation="type", target=target(), text=text))
    assert result.text_sha256 == text_digest(text)
    editor.iface_value.SetValue.assert_called_once_with(text)
    desktop.user32.SendMessageTimeoutW.assert_not_called()


def test_password_and_read_only_fields_are_never_targets(desktop: NativeDesktop) -> None:
    secret = Mock()
    secret.element_info.element.CurrentIsPassword = True
    assert not desktop.writable(secret)
    locked = Mock()
    locked.element_info.element.CurrentIsPassword = False
    locked.iface_value.CurrentIsReadOnly = True
    assert not desktop.writable(locked)
    unknown = Mock()
    type(unknown.element_info).element = property(lambda self: (_ for _ in ()).throw(OSError()))
    assert not desktop.writable(unknown)  # Anything unreadable fails closed.


def test_listing_fields_reports_only_identity(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop, "resolve", lambda *args, **kwargs: Mock())
    monkeypatch.setattr(desktop, "controls", lambda *args: [Mock(), Mock()])
    monkeypatch.setattr(desktop, "editor_target", lambda *args: target().editor)
    result = desktop.dispatch(NativeRequest(operation="fields", target=target()))
    assert len(result.editors) == 2
    # Identity only: the content of a field never leaves the machine in an observation.
    assert "text" not in result.editors[0].model_dump()


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


def test_a_window_without_a_writable_field_is_not_a_typing_target(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop, "controls", lambda *args: [])
    with pytest.raises(ToolError, match="control_unsupported"):
        desktop.editor(Mock())
    # A dialog of the same application is a separate window with its own field identity,
    # so it can never be mistaken for the document the caller observed.
    monkeypatch.setattr(desktop, "controls", lambda *args: [Mock()])
    monkeypatch.setattr(desktop, "editor_target", lambda *args: target().editor)
    other = target().editor
    assert other is not None
    with pytest.raises(ToolError, match="target_changed"):
        desktop.chosen_editor(Mock(), other.model_copy(update={"automation_id": "dialog"}))
