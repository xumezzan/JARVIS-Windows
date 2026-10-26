"""Make a local macOS application from the prebuilt UI and installed Electron runtime."""

import argparse
import json
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from jarvis.installation.desktop import verify_prebuilt


def package(repository: Path, output: Path) -> None:
    if sys.platform != "darwin":
        raise ValueError(
            "Windows packaging is performed by Install-Jarvis.cmd in its inactive slot."
        )
    assets = verify_prebuilt(repository)
    runtime = repository / "desktop/node_modules/electron/dist/Electron.app"
    python = repository / ".venv/bin/python"
    if not runtime.is_dir() or not python.is_file() or output.suffix != ".app":
        raise ValueError("Prepare the development runtime and prebuilt desktop first.")
    marker = output / "Contents/Resources/.jarvis-desktop-package"
    old_marker = output / ".jarvis-desktop-package"
    if output.exists():
        if old_marker.is_file() and not marker.is_file():
            marker = old_marker
        if (
            output.is_symlink()
            or not marker.is_file()
            or marker.read_text() != "jarvis-local-package-v1"
        ):
            raise ValueError("Refusing to replace an application not made by this packager.")
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(runtime, output, symlinks=True)
    marker = output / "Contents/Resources/.jarvis-desktop-package"
    marker.write_text("jarvis-local-package-v1")
    app = output / "Contents/Resources/app"
    shutil.copytree(assets, app)
    (app / "python-runtime.json").write_text(
        json.dumps(
            {
                "python": str(python),
                "root": str(repository),
            }
        ),
        encoding="utf-8",
    )
    info = output / "Contents/Info.plist"
    with info.open("rb") as stream:
        metadata = plistlib.load(stream)
    metadata.update(
        CFBundleName="Jarvis", CFBundleDisplayName="Jarvis", CFBundleIdentifier="org.jarvis.desktop"
    )
    with info.open("wb") as stream:
        plistlib.dump(metadata, stream)
    # Finder display/resource-fork metadata copied from the local dependency is
    # incompatible with signing. Keep quarantine and all OS consent attributes.
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
    # Sign only this generated local app. No notarization or Gatekeeper override.
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(output)],
        check=True,
        timeout=120,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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
