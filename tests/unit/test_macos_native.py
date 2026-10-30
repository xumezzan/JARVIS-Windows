"""Portable fake-AX checks; none grant OS permission or control a real desktop."""

import base64
import io
import json
import plistlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from jarvis.observability.diagnostics import Reason
from jarvis.platforms.macos import native
from jarvis.platforms.macos.native import LaunchEntry, NativeDesktop, Node, command
from jarvis.tools.base import ToolError
from jarvis.tools.windows import (
    MAX_SCREENSHOT_JPEG_BYTES,
    NativeRequest,
    Rectangle,
    WindowTarget,
    clipboard_digest,
    text_digest,
)


class FakeAX:
    kAXValueCGPointType = 1
    kAXValueCGSizeType = 2

    def __init__(self) -> None:
        self.trusted = True
        self.writes: list[tuple[Any, str, Any]] = []
        self.presses: list[Any] = []

    def AXIsProcessTrusted(self) -> bool:
        return self.trusted

    def AXUIElementCopyAttributeValue(
        self, value: dict[str, Any], key: str, result: Any
    ) -> tuple[int, Any]:
        return (0, value[key]) if key in value else (-25205, None)

    def AXValueGetValue(self, value: Any, kind: int, result: Any) -> tuple[bool, Any]:
        return True, value

    def AXUIElementIsAttributeSettable(
        self, value: dict[str, Any], key: str, result: Any
    ) -> tuple[int, bool]:
        return 0, bool(value.get("settable", False))

    def AXUIElementSetAttributeValue(self, value: dict[str, Any], key: str, text: Any) -> int:
        self.writes.append((value, key, text))
        value[key] = text
        return 0

    def AXUIElementPerformAction(self, value: Any, action: str) -> int:
        self.presses.append(value)
        return 0


def node(role: str, name: str = "", **attributes: Any) -> dict[str, Any]:
    return {
        "AXRole": role,
        "AXTitle": name,
        "AXPosition": SimpleNamespace(x=10, y=20),
        "AXSize": SimpleNamespace(width=100, height=30),
        "AXEnabled": True,
        **attributes,
    }


def target() -> WindowTarget:
    return WindowTarget(
        app="TextEdit",
        handle=123,
        pid=42,
        process_started=1234.5,
        executable=str(Path("/System/Applications/TextEdit.app/Contents/MacOS/TextEdit").resolve()),
        runtime_id=[42, 123],
        title="Document",
    )


def row() -> dict[str, Any]:
    return {
        "kCGWindowOwnerPID": 42,
        "kCGWindowNumber": 123,
        "kCGWindowLayer": 0,
        "kCGWindowName": "Document",
        "kCGWindowOwnerName": "TextEdit",
        "kCGWindowAlpha": 1,
        "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 800, "Height": 600},
    }


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> NativeDesktop:
    adapter = NativeDesktop()
    adapter._loaded = True
    adapter.ax = FakeAX()
    adapter.quartz = Mock()
    adapter.appkit = Mock()
    process = Mock()
    process.exe.return_value = target().executable
    process.create_time.return_value = target().process_started
    adapter.psutil = SimpleNamespace(Process=lambda pid: process, Error=RuntimeError)
    monkeypatch.setattr(adapter, "window_rows", lambda: [row()])
    return adapter


def test_window_target_uses_real_process_and_cg_window_identity(desktop: NativeDesktop) -> None:
    observed = desktop.windows()
    assert observed == [target()]
    assert desktop.windows("TextEdit") == observed
    assert desktop.windows("unrelated") == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("pid", 43),
        ("handle", 124),
        ("process_started", 20.0),
        ("runtime_id", [1]),
        ("executable", "/wrong"),
    ],
)
def test_reused_window_or_process_identity_is_refused(
    desktop: NativeDesktop, field: str, value: Any
) -> None:
    changed = target().model_copy(update={field: value})
    with pytest.raises(ToolError, match="target_changed"):
        desktop.resolve_row(changed)


def test_missing_accessibility_opens_only_fixed_settings_url_once(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop.ax.trusted = False
    opened: list[list[str]] = []
    monkeypatch.setattr(native, "command", lambda argv, **kwargs: opened.append(argv))
    for _ in range(2):
        with pytest.raises(ToolError) as caught:
            desktop.accessibility()
        assert caught.value.reason is Reason.MACOS_ACCESSIBILITY_REQUIRED
    assert opened == [
        [
            "/usr/bin/open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        ]
    ]


def test_password_subtree_is_never_walked_or_exposed(desktop: NativeDesktop) -> None:
    private = node("AXSecureTextField", AXValue="secret", AXChildren=[node("AXButton", "secret")])
    normal = node("AXButton", "Save")
    root = node("AXWindow", AXChildren=[private, normal])
    walked, truncated = desktop.walk(root, target())
    assert len(walked) == 3 and not truncated
    result, masks, complete = desktop.inspection(root, target(), 32)
    assert complete
    assert [element.name for element in result.elements] == ["Save"]
    assert masks == [Rectangle(left=10, top=20, right=110, bottom=50)]
    assert "secret" not in result.model_dump_json()


@pytest.mark.parametrize("attribute", ["AXRole", "AXChildren", "AXSubrole", "AXProtectedContent"])
@pytest.mark.parametrize("error", [-25200, -25202, -25204])
def test_ax_read_failure_is_never_proof_of_empty_or_nonprivate_tree(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch, attribute: str, error: int
) -> None:
    original = desktop.ax.AXUIElementCopyAttributeValue

    def read(value: Any, name: str, output: Any) -> tuple[int, Any]:
        return (error, None) if name == attribute else original(value, name, output)

    monkeypatch.setattr(desktop.ax, "AXUIElementCopyAttributeValue", read)
    with pytest.raises(ToolError, match="target_changed"):
        desktop.inspection(node("AXWindow"), target(), 32)


def test_missing_required_role_is_not_a_safe_leaf(desktop: NativeDesktop) -> None:
    with pytest.raises(ToolError):
        desktop.walk({}, target())


def test_writable_checks_never_target_secure_disabled_or_readonly(desktop: NativeDesktop) -> None:
    assert desktop.writable(node("AXTextArea", settable=True))
    for value in (
        node("AXSecureTextField", settable=True),
        node("AXTextField", AXSubrole="AXSecureTextField", settable=True),
        node("AXTextField", AXProtectedContent=True, settable=True),
        node("AXTextField", AXEnabled=False, settable=True),
        node("AXTextField", settable=False),
    ):
        assert not desktop.writable(value)


def test_editor_value_is_represented_only_by_digest(desktop: NativeDesktop) -> None:
    editor = node("AXTextArea", "Document", AXValue="private body\r\nline", settable=True)
    snapshot = desktop.editor_target(Node(editor, (123, 1), 1), [])
    assert snapshot.content_sha256 == text_digest("private body\nline")
    assert "private body" not in snapshot.model_dump_json()


def test_two_identical_semantic_editors_are_refused(desktop: NativeDesktop) -> None:
    editor = node("AXTextArea", "Document", AXValue="", settable=True)
    root = node("AXWindow", AXChildren=[editor, editor.copy()])
    wanted = desktop.editor_target(Node(editor, (123, 0), 1), [])
    with pytest.raises(ToolError, match="target_changed"):
        desktop.chosen_editor(root, target(), wanted)


def test_truncated_editor_tree_cannot_prove_unique_identity(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = node("AXTextArea", "Document", AXValue="", settable=True)
    observed = Node(editor, (123, 0), 1)
    wanted = desktop.editor_target(observed, [])
    monkeypatch.setattr(desktop, "walk", lambda *args: ([observed], True))
    with pytest.raises(ToolError, match="target_changed"):
        desktop.chosen_editor(node("AXWindow"), target(), wanted)


def typing_fixture(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch, content: str = "old"
) -> tuple[dict[str, Any], WindowTarget]:
    editor = node("AXTextArea", "Document", AXValue=content, settable=True)
    root = node("AXWindow", AXChildren=[editor])
    wanted = desktop.editor_target(Node(editor, (123, 0), 1), [])
    observed = target().model_copy(update={"editor": wanted})
    monkeypatch.setattr(desktop, "resolve", lambda *args, **kwargs: root)
    return editor, observed


@pytest.mark.parametrize("overwrite", [False, True])
def test_type_is_directed_literal_and_independently_verified(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch, overwrite: bool
) -> None:
    editor, observed = typing_fixture(desktop, monkeypatch)
    text = "текст {ENTER}\nsecond line"
    result = desktop.dispatch(
        NativeRequest(operation="type", target=observed, text=text, overwrite=overwrite)
    )
    expected = text if overwrite else "old" + text
    assert editor["AXValue"] == expected
    assert desktop.ax.writes == [(editor, "AXValue", expected)]
    assert result.text_sha256 == text_digest(expected)
    assert result.inserted_text_sha256 == text_digest(text)
    verified = desktop.dispatch(NativeRequest(operation="verify", target=observed, result=result))
    assert verified.text_sha256 == result.text_sha256


def test_changed_editor_digest_never_writes(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor, observed = typing_fixture(desktop, monkeypatch)
    editor["AXValue"] = "owner changed it"
    with pytest.raises(ToolError) as caught:
        desktop.dispatch(NativeRequest(operation="type", target=observed, text="new"))
    assert caught.value.reason is Reason.EDITOR_CHANGED
    assert not desktop.ax.writes


def test_after_preview_target_is_checked_again(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor, observed = typing_fixture(desktop, monkeypatch)
    desktop.before_action = lambda preview: editor.update(AXValue="changed during preview")
    with pytest.raises(ToolError, match="target_changed"):
        desktop.dispatch(NativeRequest(operation="type", target=observed, text="new"))
    assert not desktop.ax.writes


@pytest.mark.parametrize("changed", [False, True])
def test_click_reobserves_exact_semantic_element_after_preview(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch, changed: bool
) -> None:
    button = node("AXButton", "Save")
    root = node("AXWindow", AXChildren=[button])
    observed = desktop.element_snapshot(Node(button, (123, 0), 1))
    monkeypatch.setattr(desktop, "activate", lambda *args: root)
    monkeypatch.setattr(desktop, "resolve", lambda *args: root)
    monkeypatch.setattr(desktop, "require_foreground", lambda *args: None)
    if changed:
        desktop.before_action = lambda preview: button.update(AXTitle="Different action")
        with pytest.raises(ToolError, match="target_changed"):
            desktop.dispatch(NativeRequest(operation="click", target=target(), element=observed))
        assert not desktop.ax.presses
    else:
        result = desktop.dispatch(
            NativeRequest(operation="click", target=target(), element=observed)
        )
        assert result.performed and desktop.ax.presses == [button]


def test_tree_depth_and_mark_count_are_bounded(desktop: NativeDesktop) -> None:
    root = node("AXWindow", AXChildren=[node("AXButton", f"button {i}") for i in range(150)])
    result, _, complete = desktop.inspection(root, target(), 32)
    assert complete and result.truncated
    assert [element.mark_id for element in result.elements] == list(range(1, 97))
    assert desktop.walk(root, target(), 0)[1]


def test_retina_redaction_and_marks_use_capture_to_logical_bounds() -> None:
    from PIL import Image

    image = Image.new("RGB", (1600, 1200), "white")
    bounds = Rectangle(left=100, top=200, right=900, bottom=800)
    private = Rectangle(left=150, top=250, right=200, bottom=300)
    NativeDesktop.redact(image, bounds, [private])
    assert image.getpixel((100, 100)) == (0, 0, 0)
    assert image.getpixel((200, 200)) == (0, 0, 0)
    assert image.getpixel((99, 100)) == (255, 255, 255)


@pytest.mark.parametrize("moved_password", [False, True])
def test_native_capture_redacts_secure_pixels_and_rejects_moved_password(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch, moved_password: bool
) -> None:
    from PIL import Image

    secret = node(
        "AXTextField",
        AXSubrole="AXSecureTextField",
        AXValue="secret",
        AXPosition=SimpleNamespace(x=100, y=100),
    )
    root = node("AXWindow", AXChildren=[secret, node("AXButton", "Save")])
    desktop.quartz.CGPreflightScreenCaptureAccess.return_value = True
    monkeypatch.setattr(desktop, "active", lambda: (root, target()))
    monkeypatch.setattr(desktop, "resolve", lambda *args: root)
    monkeypatch.setattr(desktop, "require_foreground", lambda *args: None)

    def capture(argv: list[str], **kwargs: Any) -> bytes:
        assert argv[0] == "/usr/sbin/screencapture" and "-l" in argv
        Image.new("RGB", (1600, 1200), "white").save(argv[-1])
        if moved_password:
            secret["AXPosition"] = SimpleNamespace(x=300, y=300)
        return b""

    monkeypatch.setattr(native, "command", capture)
    if moved_password:
        with pytest.raises(ToolError, match="target_changed"):
            desktop.capture("active_window")
    else:
        result = desktop.capture("active_window")
        assert result.image_base64 is not None and result.image_format == "JPEG"
        data = base64.b64decode(result.image_base64)
        assert len(data) <= MAX_SCREENSHOT_JPEG_BYTES
        with Image.open(io.BytesIO(data)) as picture:
            x, y = round(picture.width * 150 / 800), round(picture.height * 115 / 600)
            pixel = picture.getpixel((x, y))
            assert isinstance(pixel, tuple)
            assert max(pixel) < 20
        assert [element.name for element in result.elements] == ["Save"]
        assert "secret" not in result.model_dump_json()


def test_app_resolution_only_uses_observed_bundle_and_refuses_ambiguity(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = LaunchEntry(
        Path("/System/Applications/TextEdit.app"), Path(target().executable), ("TextEdit",)
    )
    monkeypatch.setattr(desktop, "catalog", lambda: [entry])
    assert desktop.resolve_app("notepad") == entry
    monkeypatch.setattr(desktop, "catalog", lambda: [entry, entry])
    with pytest.raises(ToolError, match="application_ambiguous"):
        desktop.resolve_app("notepad")


@pytest.mark.parametrize(
    "bad_metadata",
    [
        b"<?xml version='1.0'?><plist><dict>\x00</dict></plist>",
        plistlib.dumps(["valid plist, but not an application metadata dictionary"]),
        plistlib.dumps({"CFBundleExecutable": "Broken", "unused": "x" * 1_000_000}),
    ],
    ids=["malformed-xml", "non-dictionary-plist", "oversized-plist"],
)
def test_unrelated_bad_app_metadata_does_not_disable_installed_app_resolution(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad_metadata: bytes
) -> None:
    broken = tmp_path / "Broken.app" / "Contents"
    broken.mkdir(parents=True)
    (broken / "Info.plist").write_bytes(bad_metadata)
    (broken / "MacOS").mkdir()
    (broken / "MacOS/Broken").write_bytes(b"")
    valid = tmp_path / "Calculator.app" / "Contents"
    (valid / "MacOS").mkdir(parents=True)
    executable = valid / "MacOS/Calculator"
    executable.write_bytes(b"")
    (valid / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleExecutable": "Calculator", "CFBundleName": "Calculator"})
    )
    catalog = NativeDesktop.catalog((tmp_path,))
    assert [entry.executable for entry in catalog] == [executable]
    monkeypatch.setattr(desktop, "catalog", lambda: catalog)
    assert desktop.resolve_app("calculator").executable == executable


@pytest.mark.parametrize("appears", [True, False])
def test_open_waits_for_observed_window_without_relaunching_after_six_seconds(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch, appears: bool
) -> None:
    entry = LaunchEntry(
        Path("/System/Applications/TextEdit.app"), Path(target().executable), ("TextEdit",)
    )
    elapsed = [0.0]
    calls: list[list[str]] = []
    monkeypatch.setattr(desktop, "resolve_app", lambda app: entry)
    monkeypatch.setattr(desktop, "with_editor", lambda observed: observed)
    monkeypatch.setattr(
        desktop, "windows", lambda: [target()] if appears and elapsed[0] >= 8 else []
    )
    monkeypatch.setattr(native, "command", lambda argv, **kwargs: calls.append(argv))
    monkeypatch.setattr(time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(time, "sleep", lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    if appears:
        assert desktop.open_app("notepad").target == target()
        assert 8 <= elapsed[0] < 14
    else:
        with pytest.raises(ToolError, match="native_timeout"):
            desktop.open_app("notepad")
        assert 14 <= elapsed[0] < 14.1
    assert calls == [["/usr/bin/open", "-a", str(entry.bundle)]]


@pytest.mark.parametrize("failure_stage", ["resolve", "editors"])
def test_optional_editor_ax_failure_keeps_only_revalidated_bare_window(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch, failure_stage: str
) -> None:
    old_editor = desktop.editor_target(
        Node(node("AXTextArea", "Document", AXValue="", settable=True), (123, 0), 1), []
    )
    observed = target().model_copy(update={"editor": old_editor, "empty": True})

    def unavailable(*args: Any) -> Any:
        raise ToolError("target_changed", reason=Reason.STALE_ELEMENT)

    monkeypatch.setattr(
        desktop, "resolve", unavailable if failure_stage == "resolve" else lambda *args: object()
    )
    monkeypatch.setattr(desktop, "editors", unavailable)
    current = desktop.with_editor(observed)
    assert current.identity == observed.identity
    assert current.editor is None and not current.empty


def test_optional_editor_failure_cannot_hide_changed_window_identity(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop, "resolve", lambda *args: object())

    def unavailable(*args: Any) -> Any:
        monkeypatch.setattr(desktop, "window_rows", lambda: [])
        raise ToolError("target_changed", reason=Reason.STALE_ELEMENT)

    monkeypatch.setattr(desktop, "editors", unavailable)
    with pytest.raises(ToolError) as caught:
        desktop.with_editor(target())
    assert caught.value.reason is Reason.WINDOW_IDENTITY_CHANGED


def test_chrome_url_is_a_literal_argument_and_has_no_shell(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = LaunchEntry(
        Path("/Applications/Google Chrome.app"), Path("/chrome"), ("Google Chrome",)
    )
    monkeypatch.setattr(desktop, "resolve_app", lambda name: entry)
    calls = []
    monkeypatch.setattr(native, "command", lambda argv, **kwargs: calls.append(argv))
    url = "https://example.com/?text=$(bad)&literal=`bad`"
    result = desktop.dispatch(NativeRequest(operation="open_chrome_url", url=url))
    assert result.performed
    assert calls == [["/usr/bin/open", "-a", str(entry.bundle), result.text]]


def test_jxa_fallback_code_is_fixed_and_uses_cg_window_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    desktop = NativeDesktop()
    desktop._loaded = True
    calls = []

    def run(argv: list[str], **kwargs: Any) -> bytes:
        calls.append(argv)
        return json.dumps([row()]).encode()

    monkeypatch.setattr(native, "command", run)
    assert desktop.window_rows()[0]["kCGWindowNumber"] == 123
    assert calls[0][:3] == ["/usr/bin/osascript", "-l", "JavaScript"]
    assert "CoreGraphics" in calls[0][-1]


@pytest.mark.skipif(sys.platform == "win32", reason="macOS utilities use POSIX pipe selectors")
def test_bounded_utility_roundtrips_utf8_and_reaps_on_limit() -> None:
    data = "Русский текст\n".encode()
    assert (
        command(
            [
                sys.executable,
                "-I",
                "-c",
                "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
            ],
            data=data,
        )
        == data
    )
    with pytest.raises(ToolError, match="control_unsupported"):
        command([sys.executable, "-I", "-c", "print('x' * 10000)"], limit=20)


@pytest.mark.skipif(sys.platform == "win32", reason="macOS utilities use POSIX pipe selectors")
def test_bounded_utility_timeout_is_finite() -> None:
    with pytest.raises(ToolError, match="native_timeout"):
        command([sys.executable, "-I", "-c", "import time; time.sleep(60)"], timeout=0.05)


def test_clipboard_uses_utf8_and_has_digest(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def run(argv: list[str], **kwargs: Any) -> bytes:
        calls.append((argv, kwargs))
        return "Привет".encode() if argv[0].endswith("pbpaste") else b""

    monkeypatch.setattr(native, "command", run)
    result = desktop.dispatch(NativeRequest(operation="clipboard_set", text="Привет"))
    assert result.text == "Привет" and result.clipboard_sha256
    assert calls[0][1]["data"] == "Привет".encode()


def test_key_mapping_is_fixed_command_modifier() -> None:
    assert set(native.HOTKEYS) == {"Ctrl+C", "Ctrl+V", "Ctrl+A", "Ctrl+Z", "Enter", "Tab", "Alt+F4"}
    assert all(native.HOTKEYS[key][1] for key in ("Ctrl+C", "Ctrl+V", "Ctrl+A", "Ctrl+Z"))


def test_paste_rechecks_clipboard_digest_before_any_key_event(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop, "activate", lambda *args: None)
    monkeypatch.setattr(desktop, "clipboard", lambda **kwargs: "changed")
    monkeypatch.setattr(
        desktop.ax,
        "AXUIElementCreateApplication",
        lambda pid: {"AXFocusedUIElement": node("AXTextField")},
        raising=False,
    )
    with pytest.raises(ToolError, match="target_changed"):
        desktop.hotkey(
            NativeRequest(
                operation="hotkey",
                target=target(),
                hotkey="Ctrl+V",
                clipboard_sha256=clipboard_digest("observed"),
            )
        )
    desktop.quartz.CGEventCreateKeyboardEvent.assert_not_called()


def test_unsupported_calculator_contract_is_never_faked(desktop: NativeDesktop) -> None:
    with pytest.raises(ToolError, match="control_unsupported"):
        desktop.dispatch(
            NativeRequest(operation="calculator_observe", target=target(), number="123")
        )


def test_screenshot_denies_before_capture_when_screen_permission_missing(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop.quartz.CGPreflightScreenCaptureAccess.return_value = False
    calls = []
    monkeypatch.setattr(native, "command", lambda argv, **kwargs: calls.append(argv))
    with pytest.raises(ToolError) as caught:
        desktop.capture("active_window")
    assert caught.value.reason is Reason.MACOS_SCREEN_RECORDING_REQUIRED
    assert all("screencapture" not in argv[0] for argv in calls)


def test_full_display_capture_fails_closed_before_observing_other_apps(
    desktop: NativeDesktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = Mock()
    monkeypatch.setattr(desktop, "accessibility", observed)
    with pytest.raises(ToolError, match="control_unsupported"):
        desktop.capture("screen")
    observed.assert_not_called()
