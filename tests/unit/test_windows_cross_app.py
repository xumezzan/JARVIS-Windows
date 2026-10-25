"""Portable cross-app regressions. Fakes do not establish native Windows acceptance."""

import asyncio
import base64
import importlib
import io
import random
import sys
from contextlib import closing
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from tests.windows_support import WindowsProbe, target

from jarvis.core.planner.contracts import Step
from jarvis.core.planner.runner import Runner
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.platforms.windows.native import NativeDesktop
from jarvis.platforms.windows.transport import ProcessBackend
from jarvis.tools.base import ExecutionContext, ToolError, canonical
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import (
    MAX_SCREENSHOT_BASE64,
    MAX_SCREENSHOT_JPEG_BYTES,
    ClickElement,
    ElementTarget,
    FocusApp,
    NativeRequest,
    Rectangle,
    SendHotkey,
    TypeText,
    WindowsResult,
    clipboard_digest,
    register_windows,
    text_digest,
)

RECT = Rectangle(left=10, top=20, right=610, bottom=420)
ELEMENT = ElementTarget(
    runtime_id=[10, 20], name="Save", control_type="Button", automation_id="save", rectangle=RECT
)


def adapter() -> NativeDesktop:
    result = NativeDesktop.__new__(NativeDesktop)
    result.desktop = Mock()
    result.psutil = Mock()
    result.user32 = Mock()
    result.user32.GetForegroundWindow.return_value = target().handle
    return result


def control(kind: str = "Button", name: str = "Save", password: bool = False) -> Mock:
    result = Mock(
        handle=101,
        element_info=Mock(
            control_type=kind,
            class_name="Custom",
            automation_id="save",
            runtime_id=[10, 20],
            element=Mock(CurrentIsPassword=password),
        ),
    )
    result.window_text.return_value = name
    result.children.return_value = []
    result.is_visible.return_value = True
    result.is_enabled.return_value = True
    result.rectangle.return_value = Mock(**RECT.model_dump())
    return result


def test_schemas_accept_arbitrary_app_and_bound_nonempty_editor() -> None:
    observed = target()
    assert observed.editor is not None
    observed = observed.model_copy(
        update={
            "app": "excel",
            "empty": False,
            "editor": observed.editor.model_copy(update={"content_sha256": text_digest("old")}),
        }
    )
    assert TypeText(target=observed, text="new").target.app == "excel"
    with pytest.raises(ValidationError):
        TypeText(target=observed.model_copy(update={"editor": None}), text="new")


@pytest.mark.parametrize("values", [{}, {"handle": 100, "title": "Excel"}, {"title": " "}])
def test_focus_rejects_missing_ambiguous_selector(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FocusApp(**values)


def test_paste_requires_content_binding_and_hotkeys_are_not_scripts() -> None:
    with pytest.raises(ValidationError):
        SendHotkey(target=target(), hotkey="Ctrl+V")
    with pytest.raises(ValidationError):
        SendHotkey(target=target(), hotkey="^a{ENTER}")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ClickElement(target=target(), x=10, y=20)


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [MAX_SCREENSHOT_BASE64, MAX_SCREENSHOT_BASE64 + 4])
async def test_screenshot_engine_handoff_excludes_image_from_text_result(
    tmp_path: Path, size: int
) -> None:
    image = "A" * size
    backend = Mock(
        call=AsyncMock(
            return_value=WindowsResult(
                image_base64=image, image_format="JPEG", width=1280, height=800
            )
        )
    )
    registry = ToolRegistry()
    register_windows(registry, backend)
    with closing(AuditLog(tmp_path / "audit.db")) as audit:
        engine = PermissionEngine(registry, ApprovalStore(), audit)
        action = engine.prepare("windows.screenshot", {}, Mode.EXECUTE)
        assert isinstance(action, Action) and len(action.payload.encode()) < 65536
        outcome = await engine.execute(action)
        if size == MAX_SCREENSHOT_BASE64:
            assert outcome.status is Status.SUCCESS and outcome.screenshot_b64 == image
            assert outcome.result_json is not None and len(outcome.result_json.encode()) < 1000
            assert "image_base64" not in outcome.result_json
        else:
            assert outcome.status is Status.ERROR and outcome.screenshot_b64 is None
        assert image not in repr(outcome) and image not in "".join(audit.recent())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,arguments,risk",
    [
        ("inspect_active_window", {}, Risk.SAFE),
        ("clipboard_get", {}, Risk.SAFE),
        ("clipboard_set", {"text": "PRIVATE_CLIPBOARD"}, Risk.SAFE),
        ("send_hotkey", {"target": target().model_dump(), "hotkey": "Enter"}, Risk.CONFIRM),
        (
            "click_element",
            {"target": target().model_dump(), "element": ELEMENT.model_dump()},
            Risk.SAFE,
        ),
        ("screenshot", {"scope": "screen"}, Risk.SAFE),
    ],
)
async def test_new_tools_simulate_without_native_hooks(
    tmp_path: Path, name: str, arguments: dict[str, Any], risk: Risk
) -> None:
    registry = ToolRegistry()
    probe = WindowsProbe()
    register_windows(registry, probe)
    with closing(AuditLog(tmp_path / "audit.db")) as audit:
        store = ApprovalStore()
        engine = PermissionEngine(registry, store, audit)
        action = engine.prepare("windows." + name, arguments, Mode.SIMULATION)
        assert isinstance(action, Action) and action.risk is risk
        token = (
            store.take_authority(audit.approved).approve(action) if risk is Risk.CONFIRM else None
        )
        assert (await engine.execute(action, token)).status is Status.SIMULATED
        assert not probe.calls
        assert "PRIVATE_CLIPBOARD" not in "".join(audit.recent())


def test_listing_includes_other_apps_and_omits_shell_and_untitled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = adapter()
    excel = control(name="Budget — Excel")
    shell = control(name="Program Manager")
    shell.element_info.class_name = "Progman"
    untitled = control(name="   ")
    desktop.desktop.windows.return_value = [shell, untitled, excel]
    other = target().model_copy(update={"app": "excel", "title": "Budget — Excel"})
    monkeypatch.setattr(desktop, "snapshot", lambda window, **kwargs: other)
    assert desktop.windows() == [other]
    assert desktop.windows("chrome") == []


def test_snapshot_does_not_restrict_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    desktop = adapter()
    window = control(name="Budget")
    process = Mock(pid=300)
    process.exe.return_value = "/Applications/Example.exe"
    process.create_time.return_value = 1234.0
    desktop.psutil.Process.return_value = process
    observed = desktop.snapshot(window, include_editor=False)
    assert observed.app == "example" and observed.executable == "/Applications/Example.exe"


def test_title_search_is_case_insensitive_and_rejects_multiple_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = adapter()
    monkeypatch.setattr(desktop, "windows", lambda: [target()])
    assert desktop.select_window(FocusApp(title="NOTEPAD")) == target()
    monkeypatch.setattr(desktop, "windows", lambda: [target(), target()])
    with pytest.raises(ToolError, match="target_changed"):
        desktop.select_window(FocusApp(title="notepad"))


def test_inspection_depth_password_and_element_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    desktop = adapter()
    window = control("Window")
    button = control()
    secret = control("Edit", password=True)
    deep = control("Edit")
    button.children.return_value = [deep]
    window.children.return_value = [button, secret]
    monkeypatch.setattr(desktop, "active", lambda: (window, target()))
    monkeypatch.setattr(desktop, "resolve", lambda *args, **kwargs: window)
    result = desktop.inspect(1)
    assert result.elements == [ELEMENT]
    assert result.window_rectangle == RECT
    assert len(desktop.inspect(2).elements) == 2
    window.children.return_value = [control() for _ in range(300)]
    result = desktop.inspect(5)
    assert len(result.elements) == 96 and result.truncated
    assert [element.mark_id for element in result.elements] == list(range(1, 97))


def test_walk_is_bounded_on_cyclic_provider_tree() -> None:
    desktop = adapter()
    window = control("Window")
    window.children.return_value = [window] * 20
    controls, truncated = desktop.walk(window)
    assert len(controls) == 2048 and truncated


@pytest.mark.parametrize("foreground,clipboard", [(999, "approved"), (100, "changed")])
def test_hotkey_rejects_wrong_focus_or_changed_clipboard(
    monkeypatch: pytest.MonkeyPatch,
    foreground: int,
    clipboard: str,
) -> None:
    desktop = adapter()
    desktop.user32.GetForegroundWindow.return_value = foreground
    monkeypatch.setattr(desktop, "resolve", lambda *args, **kwargs: control("Window"))
    monkeypatch.setattr(desktop, "clipboard", lambda *args, **kwargs: clipboard)
    keyboard = Mock()
    real_import = importlib.import_module
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: keyboard if name == "pywinauto.keyboard" else real_import(name),
    )
    with pytest.raises(ToolError, match="target_changed"):
        desktop.dispatch(
            NativeRequest(
                operation="hotkey",
                target=target(),
                hotkey="Ctrl+V",
                clipboard_sha256=clipboard_digest("approved"),
            )
        )
    keyboard.send_keys.assert_not_called()


def test_invocation_is_not_retried_after_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    desktop = adapter()
    window, button = control("Window"), control()
    monkeypatch.setattr(desktop, "activate", lambda *args: window)
    monkeypatch.setattr(desktop, "require_foreground", lambda *args: None)
    monkeypatch.setattr(desktop, "walk", lambda *args: ([button], False))
    module = Mock(NoPatternInterfaceError=type("NoPatternInterfaceError", (Exception,), {}))
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    button.iface_invoke.Invoke.side_effect = RuntimeError("outcome uncertain")
    with pytest.raises(RuntimeError):
        desktop.dispatch(NativeRequest(operation="click", target=target(), element=ELEMENT))
    button.click_input.assert_not_called()


@pytest.mark.parametrize(
    "x,rect,hit",
    [(900, RECT, 100), (30, RECT, 999), (30, RECT.model_copy(update={"left": 11}), 100)],
)
def test_coordinate_click_rejects_outside_occluded_or_moved_window(
    monkeypatch: pytest.MonkeyPatch,
    x: int,
    rect: Rectangle,
    hit: int,
) -> None:
    desktop = adapter()
    desktop.user32.WindowFromPoint.return_value = hit
    desktop.user32.IsChild.return_value = False
    monkeypatch.setattr(desktop, "activate", lambda *args: control("Window"))
    with pytest.raises(ToolError, match="target_changed"):
        desktop.dispatch(
            NativeRequest(operation="click", target=target(), x=x, y=30, window_rectangle=rect)
        )


@pytest.mark.parametrize("noisy", [False, True])
def test_screenshot_is_bounded_jpeg_without_files(
    monkeypatch: pytest.MonkeyPatch, noisy: bool
) -> None:
    image_module = pytest.importorskip("PIL.Image")
    grab = Mock()
    grab.grab.return_value = (
        image_module.frombytes("RGB", (2560, 1600), random.Random(0).randbytes(2560 * 1600 * 3))
        if noisy
        else image_module.new("RGB", (2560, 1600), "white")
    )
    real_import = importlib.import_module
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: grab if name == "PIL.ImageGrab" else real_import(name),
    )
    result = adapter().dispatch(NativeRequest(operation="screenshot", scope="screen"))
    assert result.width is not None and result.height is not None
    assert 0 < result.width <= 1280 and 0 < result.height <= 800
    if not noisy:
        assert (result.width, result.height) == (1280, 800)
    assert result.image_base64 is not None
    assert len(result.image_base64) <= MAX_SCREENSHOT_BASE64
    raw = base64.b64decode(result.image_base64, validate=True)
    assert len(raw) <= MAX_SCREENSHOT_JPEG_BYTES
    decoded = image_module.open(io.BytesIO(raw))
    assert decoded.format == "JPEG" and decoded.size == (result.width, result.height)
    reference = io.BytesIO()
    image_module.new("RGB", (1, 1)).save(reference, format="JPEG", quality=65)
    assert decoded.quantization == image_module.open(io.BytesIO(reference.getvalue())).quantization
    grab.grab.assert_called_once()


@pytest.mark.asyncio
async def test_pipe_transports_image_larger_than_old_60kb_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_launch = asyncio.create_subprocess_exec

    async def launch(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        # Generate the large frame in the child: Windows command lines have a small limit.
        script = (
            "import json,sys; sys.stdin.buffer.readline(); "
            'payload={"result":{"image_base64":"A"*200000,"image_format":"JPEG",'
            '"width":1280,"height":800}}; sys.stdout.write(json.dumps(payload)+"\\n")'
        )
        return await real_launch(sys.executable, "-I", "-c", script, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    result = await ProcessBackend()._exchange(
        NativeRequest(operation="screenshot"), ExecutionContext(Event())
    )
    assert result.image_base64 == "A" * 200000


def test_planner_requires_observed_element_and_latest_clipboard(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    register_windows(registry, WindowsProbe())
    with closing(AuditLog(tmp_path / "audit.db")) as audit:
        engine = PermissionEngine(registry, ApprovalStore(), audit)
        # Only provenance is exercised here; no provider/UI/native callback is run.
        runner = Runner(registry, engine, Mock(), Mock(), Mock())
        observed = WindowsResult(target=target(), elements=[ELEMENT], window_rectangle=RECT)
        outcome = Outcome(uuid4(), Status.SUCCESS, result_json=canonical(observed))
        runner.steps.append(Step("windows.inspect_active_window", outcome))
        action = engine.prepare(
            "windows.click_element",
            {"target": target().model_dump(), "element": ELEMENT.model_dump()},
            Mode.EXECUTE,
        )
        assert isinstance(action, Action) and runner._observed_target(action)
        changed = engine.prepare(
            "windows.click_element",
            {
                "target": target().model_dump(),
                "element": ELEMENT.model_copy(update={"runtime_id": [999]}).model_dump(),
            },
            Mode.EXECUTE,
        )
        assert isinstance(changed, Action) and not runner._observed_target(changed)
        paste = engine.prepare(
            "windows.send_hotkey",
            {
                "target": target().model_dump(),
                "hotkey": "Ctrl+V",
                "clipboard_sha256": clipboard_digest("one"),
            },
            Mode.EXECUTE,
        )
        assert isinstance(paste, Action) and not runner._observed_target(paste)
        for text in ("one", "two"):
            result = WindowsResult(text=text, clipboard_sha256=clipboard_digest(text))
            runner.steps.append(
                Step(
                    "windows.clipboard_get",
                    Outcome(outcome.request_id, Status.SUCCESS, result_json=canonical(result)),
                )
            )
            assert runner._observed_target(paste) == (text == "one")


def test_clipboard_ownership_readback_and_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    desktop = adapter()
    clipboard, gui, kernel = Mock(), Mock(), Mock()
    clipboard.IsClipboardFormatAvailable.return_value = True
    clipboard.GetClipboardData.return_value = "Жарвис"
    kernel.GlobalSize.return_value = 14
    gui.CreateWindowEx.return_value = 777
    modules = {
        "win32clipboard": clipboard,
        "win32gui": gui,
        "ctypes": Mock(WinDLL=Mock(return_value=kernel)),
    }
    monkeypatch.setattr(importlib, "import_module", lambda name: modules[name])
    result = desktop.dispatch(NativeRequest(operation="clipboard_set", text="Жарвис"))
    assert result.text == "Жарвис" and result.clipboard_sha256 == clipboard_digest("Жарвис")
    clipboard.OpenClipboard.assert_called_once_with(777)
    clipboard.SetClipboardText.assert_called_once_with("Жарвис", 13)
    clipboard.CloseClipboard.assert_called_once()
    gui.DestroyWindow.assert_called_once_with(777)


@pytest.mark.parametrize("rich,size", [(True, 14), (False, 2**30)])
def test_clipboard_rejects_rich_paste_or_oversized_allocation(
    monkeypatch: pytest.MonkeyPatch,
    rich: bool,
    size: int,
) -> None:
    desktop = adapter()
    clipboard, gui, kernel = Mock(), Mock(), Mock()
    clipboard.IsClipboardFormatAvailable.return_value = True
    clipboard.EnumClipboardFormats.return_value = 49152 if rich else 0
    kernel.GlobalSize.return_value = size
    modules = {
        "win32clipboard": clipboard,
        "win32gui": gui,
        "ctypes": Mock(WinDLL=Mock(return_value=kernel)),
    }
    monkeypatch.setattr(importlib, "import_module", lambda name: modules[name])
    with pytest.raises(ToolError, match="control_unsupported"):
        desktop.clipboard(plain_only=True)
    clipboard.GetClipboardData.assert_not_called()
    clipboard.CloseClipboard.assert_called_once()
    gui.DestroyWindow.assert_called_once()


def test_uia_hotkey_and_click_dispatch_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    desktop = adapter()
    window, button = control("Window"), control()
    monkeypatch.setattr(desktop, "activate", lambda *args: window)
    monkeypatch.setattr(desktop, "require_foreground", lambda *args: None)
    monkeypatch.setattr(desktop, "walk", lambda *args: ([button], False))
    keyboard = Mock()
    monkeypatch.setattr(importlib, "import_module", lambda name: keyboard)
    result = desktop.dispatch(NativeRequest(operation="hotkey", target=target(), hotkey="Ctrl+A"))
    assert result.performed and result.target == target()
    keyboard.send_keys.assert_called_once_with("^a", pause=0)
    result = desktop.dispatch(NativeRequest(operation="click", target=target(), element=ELEMENT))
    assert result.performed
    button.iface_invoke.Invoke.assert_called_once()
    button.click_input.assert_not_called()


def test_nonempty_generic_editor_is_replaced_and_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    desktop = adapter()
    window, editor = control("Window"), control("Edit")
    observed = target()
    assert observed.editor is not None
    observed = observed.model_copy(
        update={
            "app": "custom",
            "empty": False,
            "editor": observed.editor.model_copy(update={"content_sha256": text_digest("old")}),
        }
    )
    monkeypatch.setattr(desktop, "resolve", lambda *args, **kwargs: window)
    monkeypatch.setattr(desktop, "controls", lambda *args: [editor])
    monkeypatch.setattr(desktop, "writable", lambda *args: True)
    monkeypatch.setattr(desktop, "editor_target", lambda *args: observed.editor)
    values = iter(("old", "new"))
    monkeypatch.setattr(desktop, "read", lambda *args: next(values))
    monkeypatch.setattr(desktop, "native_handle", lambda *args: 0)
    result = desktop.dispatch(
        NativeRequest(operation="type", target=observed, text="new", overwrite=True)
    )
    editor.iface_value.SetValue.assert_called_once_with("new")
    assert result.text_sha256 == text_digest("new")
    desktop.user32.SendMessageTimeoutW.assert_not_called()


def test_changed_nonempty_editor_content_is_not_overwritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = adapter()
    observed = target()
    assert observed.editor is not None
    expected = observed.editor.model_copy(update={"content_sha256": text_digest("old")})
    observed = observed.model_copy(update={"app": "custom", "empty": False, "editor": expected})
    editor = control("Edit")
    monkeypatch.setattr(desktop, "resolve", lambda *args, **kwargs: control("Window"))
    monkeypatch.setattr(desktop, "controls", lambda *args: [editor])
    monkeypatch.setattr(desktop, "writable", lambda *args: True)
    monkeypatch.setattr(
        desktop,
        "editor_target",
        lambda *args: expected.model_copy(update={"content_sha256": text_digest("changed")}),
    )
    monkeypatch.setattr(desktop, "read", lambda *args: "changed")
    with pytest.raises(ToolError, match="target_changed"):
        desktop.dispatch(NativeRequest(operation="type", target=observed, text="new"))
    editor.iface_value.SetValue.assert_not_called()


def test_clipboard_digest_binds_line_endings() -> None:
    assert clipboard_digest("one\r\ntwo") != clipboard_digest("one\ntwo")
