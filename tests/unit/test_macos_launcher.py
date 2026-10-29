"""Local macOS bundle replacement, launch identity and failure recovery."""

import importlib.util
import json
import plistlib
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def script(relative: str) -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / relative
    spec = importlib.util.spec_from_file_location("jarvis_macos_launcher_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def packaging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, Path, Path]:
    module = script("package_desktop.py")
    root = tmp_path / "Jarvis Асаль with spaces"
    runtime = root / "desktop/node_modules/electron/dist/Electron.app/Contents"
    (runtime / "MacOS").mkdir(parents=True)
    (runtime / "Resources").mkdir()
    (runtime / "MacOS/Electron").write_bytes(b"fixture executable")
    (runtime / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "Electron"}))
    python = root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture python")
    assets = root / "src/jarvis/desktop_assets"
    assets.mkdir(parents=True)
    (assets / "manifest.json").write_text('{"files": "fixture"}')
    (root / "scripts").mkdir()
    (root / "scripts/package_desktop.py").write_text("fixture packager")
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module, "verify_prebuilt", lambda _root: assets)
    return module, root, tmp_path / "Applications/Jarvis.app"


def test_bundle_has_real_packaged_identity_and_explicit_privacy_descriptions(
    packaging: tuple[ModuleType, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    module, root, output = packaging
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(module.subprocess, "run", run)
    module.package(root, output)
    metadata = plistlib.loads((output / "Contents/Info.plist").read_bytes())
    assert metadata["CFBundleIdentifier"] == "org.jarvis.desktop"
    assert metadata["CFBundleExecutable"] == "Jarvis"
    assert (output / "Contents/MacOS/Jarvis").read_bytes() == b"fixture executable"
    assert not (output / "Contents/MacOS/Electron").exists()
    assert metadata["LSUIElement"] is False
    for key in (
        "NSMicrophoneUsageDescription",
        "NSScreenCaptureUsageDescription",
        "NSAppleEventsUsageDescription",
    ):
        assert metadata[key]
    assert json.loads((output / "Contents/Resources/app/python-runtime.json").read_text()) == {
        "python": str(root / ".venv/bin/python"),
        "root": str(root),
    }
    assert any(args[:2] == ["/usr/bin/codesign", "--verify"] for args in calls)
    assert all("com.apple.quarantine" not in args and "tccutil" not in args for args in calls)
    launcher = script("macos/launch.py")
    assert launcher.current_bundle(root, output, root / "src/jarvis/desktop_assets")
    (root / "src/jarvis/desktop_assets/manifest.json").write_text('{"files": "updated"}')
    assert not launcher.current_bundle(root, output, root / "src/jarvis/desktop_assets")


def test_failed_sign_keeps_the_previous_bundle_untouched(
    packaging: tuple[ModuleType, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    module, root, output = packaging
    resources = output / "Contents/Resources"
    resources.mkdir(parents=True)
    (resources / ".jarvis-desktop-package").write_text("jarvis-local-package-v1")
    (resources / "previous").write_text("previous working launcher")

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if args[0] == "/usr/bin/codesign":
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        module.package(root, output)
    assert (resources / "previous").read_text() == "previous working launcher"
    assert not (output / "Contents/MacOS/Jarvis").exists()
    assert list(output.parent.glob(".jarvis-package-*")) == []


def test_packager_refuses_foreign_bundles_before_touching_them(
    packaging: tuple[ModuleType, Path, Path],
) -> None:
    module, root, output = packaging
    output.mkdir(parents=True)
    (output / "owner-file").write_text("keep")
    with pytest.raises(ValueError, match="not made by this packager"):
        module.package(root, output)
    assert (output / "owner-file").read_text() == "keep"


def test_packager_rejects_an_unsupported_host(
    packaging: tuple[ModuleType, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    module, root, output = packaging
    monkeypatch.setattr(module.sys, "platform", "linux")
    with pytest.raises(ValueError):
        module.package(root, output)
    assert not output.exists()
