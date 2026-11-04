"""Keep one Finder application synchronized with explicitly selected local source.

The user installs this helper explicitly. launchd only checks a finished prebuilt
manifest; it never fetches source, builds npm, restarts Jarvis or opens a microphone.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import psutil

LABEL = "org.jarvis.desktop.sync"
MARKER = "jarvis-local-package-v1"
TARGET = Path("/Applications/Jarvis.app")
MAINTENANCE = Path.home() / "Library/Application Support/JarvisDesktop/maintenance"
LSREGISTER = (
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)


def atomic_write(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise ValueError("Refusing a linked maintenance file.")
    fd, name = tempfile.mkstemp(prefix=".jarvis-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def owned(app: Path) -> bool:
    try:
        marker = app / "Contents/Resources/.jarvis-desktop-package"
        return (
            not app.is_symlink()
            and marker.stat().st_size == len(MARKER)
            and marker.read_text() == MARKER
        )
    except OSError:
        return False


def running(app: Path) -> bool:
    prefix = str(app) + os.sep
    for process in psutil.process_iter(["name", "exe"]):
        try:
            executable = process.info["exe"]
            if executable and executable.startswith(prefix):
                return True
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            if str(process.info.get("name", "")).startswith("Jarvis"):
                return True  # An uncertain owned process is never replaced.
    return False


def current(repository: Path, app: Path) -> bool:
    try:
        resources = app / "Contents/Resources/app"
        matches = (
            owned(app)
            and (resources / "manifest.json").read_bytes()
            == (repository / "src/jarvis/desktop_assets/manifest.json").read_bytes()
            and json.loads((resources / "python-runtime.json").read_bytes())
            == {"python": str(repository / ".venv/bin/python"), "root": str(repository)}
            and (app / "Contents/Resources/.jarvis-desktop-package").stat().st_mtime
            >= (repository / "scripts/package_desktop.py").stat().st_mtime
        )
        if not matches:
            return False
        manifest = json.loads((resources / "manifest.json").read_bytes())
        for name, digest in manifest["files"].items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                return False
            file = resources / relative
            if file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def register(app: Path) -> None:
    subprocess.run([LSREGISTER, "-f", str(app)], check=True, timeout=30, capture_output=True)


def sync(repository: Path, target: Path = TARGET) -> str:
    if target.is_symlink() or (target.exists() and not owned(target)):
        raise ValueError("Refusing to replace an application not made by Jarvis.")
    if current(repository, target):
        return "current"
    if running(target):
        return "waiting_for_close"
    # Use the reviewed packager from the selected checkout, with literal argv/paths.
    # Build elsewhere first so launching Jarvis during signing cannot interrupt it.
    spec = importlib.util.spec_from_file_location(
        "jarvis_packager", repository / "scripts/package_desktop.py"
    )
    if spec is None or spec.loader is None:
        raise ValueError("Packager unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".jarvis-sync-", dir=target.parent) as temporary:
        candidate = Path(temporary) / "Jarvis.app"
        module.package(repository, candidate)
        if not current(repository, candidate):
            raise ValueError("Build changed during packaging; waiting for a stable build.")
        if running(target):
            return "waiting_for_close"
        if target.is_symlink() or (target.exists() and not owned(target)):
            raise ValueError("Application changed during update.")
        backup = target.with_name(f".Jarvis.app.previous-{uuid4().hex}")
        if target.exists():
            target.rename(backup)
        try:
            candidate.rename(target)
        except OSError:
            if backup.exists():
                backup.rename(target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    register(target)
    return "updated"


def agent(repository: Path, helper: Path) -> dict[str, Any]:
    return {
        "Label": LABEL,
        "ProgramArguments": [str(repository / ".venv/bin/python"), str(helper), "--sync"],
        "RunAtLoad": True,
        "StartInterval": 30,
        "WatchPaths": [str(repository / "src/jarvis/desktop_assets/manifest.json")],
        "ProcessType": "Background",
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }


def install(repository: Path) -> None:
    repository = repository.resolve(strict=True)
    legacy = Path.home() / "Applications/Jarvis.app"
    if legacy.exists() and not legacy.is_symlink() and (not owned(legacy) or running(legacy)):
        raise ValueError("Close the owned legacy Jarvis before migration.")
    if not (repository / ".venv/bin/python").is_file():
        raise ValueError("Prepare the repository's .venv first.")
    if MAINTENANCE.is_symlink():
        raise ValueError("Refusing a linked maintenance directory.")
    MAINTENANCE.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = MAINTENANCE / "source.json"
    if config.exists() and load_source(config) != repository:
        raise ValueError("A different checkout owns the existing installation.")
    helper = MAINTENANCE / "sync.py"
    atomic_write(helper, Path(__file__).read_bytes())
    atomic_write(config, json.dumps({"repository": str(repository)}).encode())
    state = sync(repository)
    if state == "waiting_for_close":
        raise ValueError("Close Jarvis before installing its permanent launcher.")
    # Keep old Dock shortcuts usable, but index only the canonical application.
    if legacy.exists() and not legacy.is_symlink():
        if not owned(legacy) or running(legacy):
            raise ValueError("Close the owned legacy Jarvis before migration.")
        subprocess.run([LSREGISTER, "-u", str(legacy)], check=True, timeout=30, capture_output=True)
        preserved = MAINTENANCE / f"previous-user-install-{uuid4().hex}"
        legacy.rename(preserved)
        legacy.symlink_to(TARGET, target_is_directory=True)
    register(TARGET)
    agents = Path.home() / "Library/LaunchAgents"
    agents.mkdir(exist_ok=True)
    plist = agents / f"{LABEL}.plist"
    if plist.exists() and plistlib.loads(plist.read_bytes()).get("Label") != LABEL:
        raise ValueError("Refusing an unrelated LaunchAgent.")
    atomic_write(plist, plistlib.dumps(agent(repository, helper)))
    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["/bin/launchctl", "bootout", f"{domain}/{LABEL}"],
        check=False,
        capture_output=True,
        timeout=15,
    )
    subprocess.run(
        ["/bin/launchctl", "bootstrap", domain, str(plist)],
        check=True,
        capture_output=True,
        timeout=15,
    )
    print("Installed /Applications/Jarvis.app and local build synchronization.")


def load_source(path: Path) -> Path:
    if path.is_symlink() or path.stat().st_size > 8192:
        raise ValueError("Invalid source configuration.")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or set(value) != {"repository"}:
        raise ValueError("Invalid source configuration.")
    root = Path(value["repository"])
    if not root.is_absolute():
        raise ValueError("Source must be an absolute path.")
    return root


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", type=Path, metavar="REPOSITORY")
    action.add_argument("--sync", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This installer requires macOS.")
    if args.install is not None:
        install(args.install)
        return 0
    # launchd serializes its job; flock also excludes a simultaneous manual run.
    import fcntl

    with (MAINTENANCE / "sync.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            state = sync(load_source(MAINTENANCE / "source.json"))
        except Exception:
            state = "update_failed"
        atomic_write(MAINTENANCE / "status.json", json.dumps({"state": state}).encode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
