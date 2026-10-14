"""Per-user two-slot setup, invoked explicitly from the repository on Windows only."""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from jarvis.installation.assets import SetupError, digest, download, extract_model
from jarvis.installation.report import CATALOG, CheckStatus, InstallationReport


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
                try:
                    if sys.platform == "win32":
                        with suppress(OSError, subprocess.SubprocessError):
                            subprocess.run(
                                [
                                    str(
                                        Path(os.environ["SYSTEMROOT"]) / "System32" / "taskkill.exe"
                                    ),
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
                finally:
                    if process.poll() is None:
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


def prepare_slot(
    root: Path,
    repository: Path,
    name: str,
    asset: dict[str, Any],
    report: InstallationReport | None = None,
) -> Path:
    def progress(step: str) -> None:
        if report is not None:
            report.value["preparation_step"] = step
            report.save()

    progress("slot_directory")
    slot = root / "slots" / name
    if slot.exists():
        if not (slot / ".jarvis-slot").is_file():
            raise SetupError("Refusing to replace a directory not owned by this installer.")
        try:
            shutil.rmtree(slot)  # Only the inactive, marked slot; app data is elsewhere.
        except OSError as error:
            # Setup starts Jarvis to see its window and deliberately leaves it running, so
            # the copy from an earlier run still holds this slot open. Say so: a file-in-use
            # error arriving as an unexplained failure sends the owner looking at the network.
            raise SetupError(
                "Cannot replace the previous components: their files are still in use. "
                "Close every open Jarvis window and rerun setup."
            ) from error
    slot.mkdir(parents=True)
    (slot / ".jarvis-slot").write_text("1", encoding="ascii")
    env = slot_environment(slot, asset["directory"])
    progress("isolated_environment")
    print("Creating isolated runtime (no development dependencies).", flush=True)
    run([sys.executable, "-I", "-m", "venv", str(slot / "venv")], env)
    python = str(slot / "venv" / "Scripts" / "python.exe")
    progress("application_packages")
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
    progress("browser_download")
    print("Installing Playwright's matching Chromium from its official CDN.", flush=True)
    run([python, "-I", "-m", "playwright", "install", "chromium"], env, seconds=600)
    progress("local_model")
    print("Checking and extracting the selected local Russian Vosk model.", flush=True)
    archive = root / "cache" / "vosk.zip"
    download(asset["url"], archive, asset["sha256"], asset["size"])
    extract_model(archive, slot / "models", asset["directory"])
    return slot


def validate_probe(name: str, raw: str) -> dict[str, Any]:
    """Accept only finite results and public version metadata, never raw child diagnostics."""
    from jarvis.installation.probe import PACKAGES, VERSION_PATTERN

    if len(raw) > 16000:
        raise ValueError("Invalid diagnostic response.")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) - {"status", "code", "packages"}:
        raise ValueError("Invalid diagnostic response.")
    allowed = {("passed", "none"), ("failed", "check_failed")}
    if not CATALOG[name].required:
        allowed |= {("warning", "missing"), ("warning", "discovery_failed")}
    if not isinstance(value.get("status"), str) or not isinstance(value.get("code"), str):
        raise ValueError("Invalid diagnostic response.")
    if (value["status"], value["code"]) not in allowed:
        raise ValueError("Invalid diagnostic response.")
    packages = value.get("packages")
    if name == "package_versions" and value["status"] == "passed":
        if not isinstance(packages, dict) or set(packages) != set(PACKAGES):
            raise ValueError("Invalid package metadata.")
        if any(
            not isinstance(item, str) or len(item) > 80 or not VERSION_PATTERN.fullmatch(item)
            for item in packages.values()
        ):
            raise ValueError("Invalid package metadata.")
    elif "packages" in value:
        raise ValueError("Unexpected package metadata.")
    return dict(value)


def check_slot(
    slot: Path, model: str, root: Path, report: InstallationReport | None = None
) -> dict[str, Any]:
    from jarvis.installation.probe import PROBE_CHECKS

    report = report or InstallationReport(root)
    python = str(slot / "venv" / "Scripts" / "python.exe")
    env = slot_environment(slot, model)
    packages: dict[str, str] = {}
    print(
        "Running independent bounded installation diagnostics (no accounts or audio capture).",
        flush=True,
    )
    with tempfile.TemporaryDirectory(prefix="smoke-", dir=root) as temporary:
        check_env = env | {"JARVIS_DATA_DIR": temporary}
        for name in ("pip_check", *PROBE_CHECKS, "gui_smoke"):
            report.record(name, "running")
            started = time.monotonic()
            print(f"Checking {name}.", flush=True)
            status: CheckStatus = "passed"
            code = "none"
            try:
                if name == "pip_check":
                    run([python, "-I", "-m", "pip", "check"], check_env, seconds=30)
                elif name == "gui_smoke":
                    run([python, "-I", "-m", "jarvis", "--smoke-test"], check_env, seconds=30)
                else:
                    raw = run(
                        [python, "-I", "-m", "jarvis.installation.probe", "--check", name],
                        check_env,
                        seconds=30 if name in {"offline_chromium", "local_vosk"} else 15,
                    )
                    result = validate_probe(name, raw)
                    status, code = cast(CheckStatus, result["status"]), result["code"]
                    if name == "package_versions" and status == "passed":
                        packages = result["packages"]
            except subprocess.TimeoutExpired:
                status, code = "failed", "timeout"
            except ValueError:
                status, code = "failed", "invalid_response"
            except (SetupError, OSError, subprocess.SubprocessError):
                status, code = "failed", "check_failed"
            report.record(
                name, status, code=code, duration_ms=int((time.monotonic() - started) * 1000)
            )
    warnings = [
        CATALOG[name].remedy
        for name in PROBE_CHECKS
        if not CATALOG[name].required and report.results[name]["status"] != "passed"
    ]
    checks = {
        "packages": packages,
        "warnings": warnings,
        "dependencies": "passed"
        if all(
            report.results[name]["status"] == "passed"
            for name in PROBE_CHECKS
            if name.startswith("import_")
        )
        else "failed",
        "offline_chromium": report.results["offline_chromium"]["status"],
        "local_vosk_load": report.results["local_vosk"]["status"],
    }
    report.value["checks"] = checks
    report.value["voice_setup"] = "needs_user_action" if warnings else "detected_unverified"
    report.save()
    if any(
        report.results[name]["status"] != "passed"
        for name in ("pip_check", *PROBE_CHECKS, "gui_smoke")
        if CATALOG[name].required
    ):
        raise SetupError(
            "Required automatic diagnostics failed. "
            "Read installation-report.md; repair and rerun setup."
        )
    return checks


def shown_window(receipt: object, launched: int) -> bool:
    """Bind the receipt to the launched process; a venv redirector reports its own child."""
    if not isinstance(receipt, dict):
        return False
    return (
        receipt.get("platform") == "windows"
        and receipt.get("visible") is True
        and launched in (receipt.get("pid"), receipt.get("ppid"))
    )


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
                if shown_window(value, process.pid):
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


def install(
    repository: Path, root: Path, *, run_id: str | None = None, source_commit: str | None = None
) -> None:
    reject_links(root)
    report = InstallationReport(root, run_id)
    try:
        report.record("preflight", "running")
        if source_commit is not None and not re.fullmatch(r"[a-f0-9]{40}", source_commit):
            raise SetupError("Invalid source commit; installation was not started.")
        report.value["source_commit"] = source_commit
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
        report.value["source"] = fingerprint
        report.value["previous_slot"] = current
        report.record("preflight", "passed")
        report.record("prepare", "running")
        slot = root / "slots" / (current or "a")
        reusable = current is not None and healthy(slot, fingerprint)
        if reusable:
            print("Installed files match; checking without downloading.", flush=True)
        else:
            name = "b" if current == "a" else "a"
            slot = prepare_slot(root, repository, name, asset, report)
        report.value["slot"] = slot.name
        report.record("prepare", "passed")
        checks = check_slot(slot, asset["directory"], root, report)
        report.value["checks"] = checks
        from jarvis.installation.probe import PROBE_CHECKS

        if any(
            report.results[name]["status"] != "passed"
            for name in ("pip_check", *PROBE_CHECKS, "gui_smoke")
            if CATALOG[name].required
        ):
            raise SetupError(
                "Required automatic diagnostics are incomplete; candidate was not activated."
            )
        report.record("source_integrity", "running")
        if source_digest(repository) != fingerprint:
            raise SetupError("Repository changed during setup. Close Jarvis and rerun setup.")
        if not reusable:
            atomic_json(
                slot / "receipt.json",
                {"source": fingerprint, "source_commit": source_commit, "files": inventory(slot)},
            )
        report.record("source_integrity", "passed")
        # Verify the candidate before changing the pointer used by the stable launcher.
        report.record("native_window", "running")
        print("Starting Jarvis and waiting for its visible native window.", flush=True)
        launch(slot, asset["directory"], root)
        report.value["gui_smoke"] = "passed"
        report.value["native_window"] = "observed"
        report.record("native_window", "passed")
        report.record("activation", "running")
        atomic_json(
            root / "active.json",
            {"slot": slot.name, "model": asset["directory"], "source_commit": source_commit},
        )
        report.record("activation", "passed")
        report.finish("automatic_checks_passed")
        print(
            "Automatic diagnostics passed. "
            "Live voice/mail/model and full Windows MVP remain pending."
        )
        print(f"Read the report: {root / 'installation-report.md'}")
        for warning in checks.get("warnings", []):
            print(warning)
    except BaseException as error:
        try:
            report.finish("cancelled" if isinstance(error, KeyboardInterrupt) else "failed")
        except OSError:
            print(
                "Installation report could not be saved. Check directory permissions.",
                file=sys.stderr,
            )
        raise


def supported_host() -> bool:
    return (
        sys.platform == "win32"
        and platform.machine().lower() in {"amd64", "x86_64"}
        and sys.getwindowsversion().build >= 22000
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis Windows setup (run Install-Jarvis.ps1).")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run-id", help="Bootstrap UUID identifying this installation attempt.")
    parser.add_argument(
        "--source-commit", help="Published commit selected by the explicit updater."
    )
    args = parser.parse_args()
    if not supported_host():
        print("Setup requires an interactive Windows 11 x64 session.", file=sys.stderr)
        return 2
    repository = Path(__file__).resolve().parents[3]
    try:
        install(
            repository, args.root.absolute(), run_id=args.run_id, source_commit=args.source_commit
        )
        return 0
    except KeyboardInterrupt:
        print(
            "Setup cancelled. Rerun the same script to repair; app data is preserved.",
            file=sys.stderr,
        )
        return 130
    except (SetupError, OSError, subprocess.SubprocessError, ValueError) as error:
        if isinstance(error, SetupError):
            message = str(error)
        else:
            # Name what went wrong. The previous text promised a step that a failure before
            # the first printed step never displayed, and it dropped the cause entirely.
            # The operating system's own wording is localised and arrives mangled through a
            # redirected console, so only the class and the numeric code are reported.
            code = getattr(error, "winerror", None) or getattr(error, "errno", None)
            named = type(error).__name__ + (f" {code}" if code else "")
            message = (
                f"Setup failed ({named}). Close open Jarvis windows, then check network, "
                "permissions, disk and runtime; rerun."
            )
        print(message, file=sys.stderr)
        return 1
