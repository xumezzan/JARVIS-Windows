"""Per-user two-slot setup, invoked explicitly from the repository on Windows only."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jarvis.installation.assets import SetupError, digest, download, extract_model


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def clean_environment(source: Mapping[str, str]) -> dict[str, str]:
    """No credentials, pip configuration, proxy URLs or browser download overrides."""
    allowed = {
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "SYSTEMDRIVE",
        "TEMP",
        "TMP",
        "PATH",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "COMMONPROGRAMFILES",
        "PATHEXT",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_ARCHITEW6432",
        "JARVIS_DATA_DIR",
        "JARVIS_BROWSER_ORIGINS",
        "JARVIS_PLANNER_MODEL",
    }
    env = {key: value for key, value in source.items() if key.upper() in allowed}
    env.update(
        {
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_INDEX_URL": "https://pypi.org/simple",
            "PYTHONUTF8": "1",
            "PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT": "30000",
        }
    )
    return env


def run(command: Sequence[str], env: Mapping[str, str], *, seconds: int = 600) -> str:
    """Bounded child with no shell or raw diagnostic persistence; kill tree on interruption."""
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            list(command),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.DEVNULL,
        )
        try:
            code = process.wait(timeout=seconds)
            if code:
                raise SetupError("Setup check failed. See the named step; repair and rerun setup.")
            if output.tell() > 2_000_000:
                raise SetupError("Setup check produced excessive output.")
            output.seek(0)
            return output.read().decode("utf-8", errors="replace")
        finally:
            if process.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(
                        [
                            str(Path(os.environ["SYSTEMROOT"]) / "System32" / "taskkill.exe"),
                            "/PID",
                            str(process.pid),
                            "/T",
                            "/F",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=15,
                        check=False,
                    )
                process.kill()
                process.wait(timeout=15)


def source_digest(repository: Path) -> str:
    """Only distributed inputs; excludes memory, credentials, caches and local design tools."""
    files = [repository / name for name in ("pyproject.toml", "README.md")]
    files += [
        p
        for base in ("src", "scripts/windows")
        for p in (repository / base).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    ]
    fingerprint = hashlib.sha256()
    for path in sorted(files):
        fingerprint.update(path.relative_to(repository).as_posix().encode())
        fingerprint.update(bytes.fromhex(digest(path)))
    return fingerprint.hexdigest()


def inventory(slot: Path) -> dict[str, str]:
    """Detect missing/modified installed files on repair, including browser and model assets."""
    return {
        p.relative_to(slot).as_posix(): digest(p)
        for p in sorted(slot.rglob("*"))
        if p.is_file() and p.name != "receipt.json" and "__pycache__" not in p.parts
    }


def healthy(slot: Path, fingerprint: str) -> bool:
    try:
        receipt = json.loads((slot / "receipt.json").read_text(encoding="utf-8"))
        return bool(receipt["source"] == fingerprint and receipt["files"] == inventory(slot))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def active_slot(root: Path) -> str | None:
    try:
        value = json.loads((root / "active.json").read_text(encoding="utf-8"))["slot"]
        if value not in ("a", "b"):
            raise ValueError
        return str(value)
    except FileNotFoundError:
        return None
    except (ValueError, KeyError, TypeError):
        raise SetupError(
            "Invalid installation pointer. Preserve it and inspect before repair."
        ) from None


def reject_links(root: Path) -> None:
    """Never traverse a user-created link/junction while checking or deleting setup slots."""
    for path in (root, *root.parents):
        if path.is_symlink() or path.is_junction():
            raise SetupError("Setup directories must not be links or junctions.")
    for path in root.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise SetupError("Setup directories must not contain links or junctions.")


def slot_environment(slot: Path, model: str) -> dict[str, str]:
    env = clean_environment(os.environ)
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(slot / "browsers")
    env["JARVIS_VOSK_MODEL"] = str(slot / "models" / model)
    env["QT_QPA_PLATFORM"] = "windows"
    return env


def prepare_slot(root: Path, repository: Path, name: str, asset: dict[str, Any]) -> Path:
    slot = root / "slots" / name
    if slot.exists():
        if not (slot / ".jarvis-slot").is_file():
            raise SetupError("Refusing to replace a directory not owned by this installer.")
        shutil.rmtree(slot)  # Only the inactive, marked slot; app data is elsewhere.
    slot.mkdir(parents=True)
    (slot / ".jarvis-slot").write_text("1", encoding="ascii")
    env = slot_environment(slot, asset["directory"])
    print("Creating isolated runtime (no development dependencies).", flush=True)
    run([sys.executable, "-I", "-m", "venv", str(slot / "venv")], env)
    python = str(slot / "venv" / "Scripts" / "python.exe")
    print("Installing application and voice dependencies from PyPI (network required).", flush=True)
    run(
        [
            python,
            "-I",
            "-m",
            "pip",
            "--isolated",
            "install",
            "--index-url",
            "https://pypi.org/simple",
            "--retries",
            "0",
            "--timeout",
            "30",
            f"{repository}[voice]",
        ],
        env,
        seconds=1200,
    )
    print("Installing Playwright's matching Chromium from its official CDN.", flush=True)
    run([python, "-I", "-m", "playwright", "install", "chromium"], env, seconds=600)
    print("Checking and extracting the selected local Russian Vosk model.", flush=True)
    archive = root / "cache" / "vosk.zip"
    download(asset["url"], archive, asset["sha256"], asset["size"])
    extract_model(archive, slot / "models", asset["directory"])
    return slot


def check_slot(slot: Path, model: str, root: Path) -> dict[str, Any]:
    python = str(slot / "venv" / "Scripts" / "python.exe")
    env = slot_environment(slot, model)
    print(
        "Checking dependencies, offline Chromium, local model and native voice availability.",
        flush=True,
    )
    run([python, "-I", "-m", "pip", "check"], env)
    checks = json.loads(run([python, "-I", "-m", "jarvis.installation.probe"], env, seconds=90))
    if "failure" in checks:
        from jarvis.installation.probe import DEPENDENCIES

        component = checks["failure"]
        if component not in (*DEPENDENCIES, "chromium", "model"):
            raise SetupError("Invalid component check response.")
        raise SetupError(
            f"Component check failed: {component}. Check Windows compatibility, "
            "security software and installed files; rerun setup to repair."
        )
    print("Running native GUI smoke in a temporary data directory.", flush=True)
    with tempfile.TemporaryDirectory(prefix="smoke-", dir=root) as temporary:
        smoke_env = env | {"JARVIS_DATA_DIR": temporary}
        run([python, "-I", "-m", "jarvis", "--smoke-test"], smoke_env, seconds=30)
    return dict(checks)


def launch(slot: Path, model: str, root: Path) -> None:
    """Observe a real shown Qt window via a fresh PID-bound receipt; leave it running."""
    report = root / "startup.json"
    report.unlink(missing_ok=True)
    process = subprocess.Popen(
        [
            str(slot / "venv" / "Scripts" / "pythonw.exe"),
            "-I",
            "-m",
            "jarvis",
            "--startup-report",
            str(report),
        ],
        env=slot_environment(slot, model),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    observed = False
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and process.poll() is None:
            try:
                value = json.loads(report.read_text(encoding="utf-8"))
                if value == {"pid": process.pid, "platform": "windows", "visible": True}:
                    observed = True
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
    finally:
        if not observed and process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
    raise SetupError("The real Windows window was not observed. Check desktop/session and rerun.")


def install(repository: Path, root: Path) -> None:
    reject_links(root)
    data_path = os.environ.get("JARVIS_DATA_DIR")
    if data_path and (
        not Path(data_path).is_absolute()
        or Path(data_path).expanduser().resolve().is_relative_to(root.resolve())
    ):
        raise SetupError(
            "JARVIS_DATA_DIR must be an absolute path outside JarvisInstall. "
            "Preserve existing data before changing its path."
        )
    if shutil.disk_usage(root).free < 3_000_000_000:
        raise SetupError("Setup needs at least 3 GB free. Free space and rerun.")
    (root / "cache").mkdir(exist_ok=True)
    fingerprint = source_digest(repository)
    asset = json.loads((repository / "scripts/windows/assets.json").read_text())["vosk"]
    current = active_slot(root)
    slot = root / "slots" / (current or "a")
    reusable = current is not None and healthy(slot, fingerprint)
    if reusable:
        print("Installed files match; checking without downloading.", flush=True)
    else:
        name = "b" if current == "a" else "a"
        slot = prepare_slot(root, repository, name, asset)
    checks = check_slot(slot, asset["directory"], root)
    if source_digest(repository) != fingerprint:
        raise SetupError("Repository changed during setup. Close Jarvis and rerun setup.")
    if not reusable:
        atomic_json(slot / "receipt.json", {"source": fingerprint, "files": inventory(slot)})
    # Verify the candidate before changing the pointer used by the stable launcher.
    print("Starting Jarvis and waiting for its visible native window.", flush=True)
    launch(slot, asset["directory"], root)
    atomic_json(root / "active.json", {"slot": slot.name, "model": asset["directory"]})
    atomic_json(
        root / "installation-report.json",
        {
            "schema": 1,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "source": fingerprint,
            "slot": slot.name,
            "checks": checks,
            "gui_smoke": "passed",
            "native_window": "observed",
            "windows_mvp_acceptance": "pending",
            "live_mail": "pending",
            "live_model": "pending",
            "voice_hardware": "pending",
            "voice_setup": "needs_user_action" if checks.get("warnings") else "detected_unverified",
        },
    )
    print("Application window observed. Live voice/mail/model and full Windows MVP remain pending.")
    for warning in checks.get("warnings", []):
        print(warning)


def supported_host() -> bool:
    return (
        sys.platform == "win32"
        and platform.machine().lower() in {"amd64", "x86_64"}
        and sys.getwindowsversion().build >= 22000
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis Windows setup (run Install-Jarvis.ps1).")
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if not supported_host():
        print("Setup requires an interactive Windows 11 x64 session.", file=sys.stderr)
        return 2
    repository = Path(__file__).resolve().parents[3]
    try:
        install(repository, args.root.resolve())
        return 0
    except KeyboardInterrupt:
        print(
            "Setup cancelled. Rerun the same script to repair; app data is preserved.",
            file=sys.stderr,
        )
        return 130
    except (SetupError, OSError, subprocess.SubprocessError, ValueError) as error:
        message = (
            str(error)
            if isinstance(error, SetupError)
            else (
                "Setup failed at the displayed step. "
                "Check network, permissions, disk and runtime; rerun."
            )
        )
        print(message, file=sys.stderr)
        return 1
