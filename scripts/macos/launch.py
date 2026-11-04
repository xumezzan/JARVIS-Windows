"""Finder launcher for the local checkout-bound macOS application."""

import json
import plistlib
import subprocess
import sys
from pathlib import Path

from jarvis.installation.desktop import verify_prebuilt


def current_bundle(repository: Path, output: Path, assets: Path) -> bool:
    """Only reuse a bundle whose interpreter, sources and prebuilt assets match."""
    try:
        if output.is_symlink():
            return False
        app = output / "Contents/Resources/app"
        config = app / "python-runtime.json"
        marker = output / "Contents/Resources/.jarvis-desktop-package"
        if config.stat().st_size > 8192 or marker.stat().st_size > 100:
            return False
        if marker.read_text() != "jarvis-local-package-v1":
            return False
        if json.loads(config.read_text()) != {
            "python": str(repository / ".venv/bin/python"),
            "root": str(repository),
        }:
            return False
        with (output / "Contents/Info.plist").open("rb") as stream:
            metadata = plistlib.load(stream)
        return (
            metadata.get("CFBundleIdentifier") == "org.jarvis.desktop"
            and metadata.get("CFBundleExecutable") == "Jarvis"
            and (output / "Contents/MacOS/Jarvis").is_file()
            and (app / "manifest.json").read_bytes() == (assets / "manifest.json").read_bytes()
            and marker.stat().st_mtime
            >= (repository / "scripts/package_desktop.py").stat().st_mtime
        )
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("Launch-Jarvis.command requires macOS.")
    repository = Path(__file__).resolve().parents[2]
    output = Path.home() / "Applications/Jarvis.app"
    assets = verify_prebuilt(repository)
    if not current_bundle(repository, output, assets):
        subprocess.run(
            [
                sys.executable,
                str(repository / "scripts/package_desktop.py"),
                "--output",
                str(output),
            ],
            check=True,
            timeout=300,
        )
    # LaunchServices gives Dock/Spotlight the same bundle identity and TCC owner.
    # The .app starts the verified private Python runtime without npm or a shell.
    subprocess.run(["/usr/bin/open", str(output)], check=True, timeout=15)


if __name__ == "__main__":
    main()
