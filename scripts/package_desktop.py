"""Make a local macOS application from the prebuilt UI and installed Electron runtime."""

import argparse
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from jarvis.installation.desktop import verify_prebuilt

PACKAGE_MARKER = "jarvis-local-package-v1"


def _owned(output: Path) -> bool:
    marker = output / "Contents/Resources/.jarvis-desktop-package"
    old_marker = output / ".jarvis-desktop-package"
    if not marker.is_file():
        marker = old_marker
    return (
        not output.is_symlink()
        and marker.is_file()
        and marker.stat().st_size == len(PACKAGE_MARKER)
        and marker.read_text() == PACKAGE_MARKER
    )


def _prepare(repository: Path, output: Path, assets: Path, runtime: Path, python: Path) -> None:
    shutil.copytree(runtime, output, symlinks=True)
    marker = output / "Contents/Resources/.jarvis-desktop-package"
    marker.write_text(PACKAGE_MARKER)
    app = output / "Contents/Resources/app"
    shutil.copytree(assets, app)
    (app / "python-runtime.json").write_text(
        json.dumps({"python": str(python), "root": str(repository)}), encoding="utf-8"
    )
    info = output / "Contents/Info.plist"
    with info.open("rb") as stream:
        metadata = plistlib.load(stream)
    # Electron uses its executable identity when deciding whether it is packaged.
    # Rename the real executable and its plist entry together, not app.isPackaged.
    (output / "Contents/MacOS/Electron").rename(output / "Contents/MacOS/Jarvis")
    metadata.update(
        CFBundleName="Jarvis",
        CFBundleDisplayName="Jarvis",
        CFBundleExecutable="Jarvis",
        CFBundleIdentifier="org.jarvis.desktop",
        LSUIElement=False,
        NSMicrophoneUsageDescription=(
            "Jarvis использует микрофон для голосовых команд, когда вы включаете запись."
        ),
        NSScreenCaptureUsageDescription=(
            "Jarvis снимает выбранное окно для выполнения вашей задачи."
        ),
        NSAppleEventsUsageDescription=(
            "Jarvis управляет выбранными приложениями при выполнении вашей задачи."
        ),
    )
    with info.open("wb") as stream:
        plistlib.dump(metadata, stream)
    # Finder metadata is incompatible with signing. Preserve quarantine and TCC.
    for attribute in ("com.apple.ResourceFork", "com.apple.FinderInfo"):
        subprocess.run(
            ["/usr/bin/xattr", "-dr", attribute, str(output)],
            check=False,
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for bundle in (*output.rglob("*.app"), *output.rglob("*.framework"), output):
            subprocess.run(
                ["/usr/bin/xattr", "-d", attribute, str(bundle)],
                check=False,
                timeout=10,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(output)],
        check=True,
        timeout=120,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(output)],
        check=True,
        timeout=30,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def package(repository: Path, output: Path) -> None:
    if sys.platform != "darwin":
        raise ValueError(
            "Windows packaging is performed by Install-Jarvis.cmd in its inactive slot."
        )
    repository = repository.resolve()
    output = output.absolute()
    assets = verify_prebuilt(repository)
    runtime = repository / "desktop/node_modules/electron/dist/Electron.app"
    python = repository / ".venv/bin/python"
    if not runtime.is_dir() or not python.is_file() or output.suffix != ".app":
        raise ValueError("Prepare the development runtime and prebuilt desktop first.")
    if output.is_symlink() or (output.exists() and not _owned(output)):
        raise ValueError("Refusing to replace an application not made by this packager.")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Build and verify a candidate first. A failed copy/sign never removes the
    # owner's previous launcher; no settings, credentials or profiles live here.
    with tempfile.TemporaryDirectory(prefix=".jarvis-package-", dir=output.parent) as temporary:
        stage = Path(temporary) / output.name
        # Keep recovery outside TemporaryDirectory: even a failed rollback must
        # leave the previous bundle recoverable rather than clean it up on exit.
        backup = output.with_name(f".{output.name}.previous-{uuid4().hex}")
        _prepare(repository, stage, assets, runtime, python)
        if output.exists():
            if not _owned(output):
                raise ValueError("The existing application changed during packaging.")
            output.rename(backup)
        try:
            stage.rename(output)
        except OSError:
            if backup.exists():
                backup.rename(output)
            raise
        if backup.exists():
            shutil.rmtree(backup)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    # Applications is outside commonly synchronized Documents folders, whose Finder
    # metadata can reappear while codesign is sealing a generated bundle.
    output = (args.output or Path.home() / "Applications/Jarvis.app").absolute()
    package(repository, output)
    print(output)


if __name__ == "__main__":
    main()
