"""Installer diagnostics certify bounded observations, never unperformed acceptance."""

import importlib
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from jarvis.installation import probe, setup
from jarvis.installation import report as report_module
from jarvis.installation.assets import SetupError
from jarvis.installation.report import CHECKS, CheckStatus, InstallationReport


def read_report(root: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads((root / "installation-report.json").read_text(encoding="utf-8"))
    )


def test_report_progress_and_failure_preserve_completed_and_unperformed_checks(
    tmp_path: Path,
) -> None:
    report = InstallationReport(tmp_path)
    report.record("pip_check", "passed", duration_ms=2)
    report.record("import_qt", "running")
    running = read_report(tmp_path)
    assert running["status"] == "running"
    assert next(c for c in running["diagnostics"] if c["id"] == "pip_check")["status"] == "passed"
    report.finish("failed")
    finished = read_report(tmp_path)
    assert finished["status"] == "failed" and finished["finished_at"]
    assert report.results["pip_check"]["status"] == "passed"
    assert report.results["import_qt"]["status"] == "failed"
    assert report.results["local_vosk"]["status"] == "not_checked"
    assert "ОШИБКА" in (tmp_path / "installation-report.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("missing_status", ["not_checked", "warning", "failed", "running"])
def test_success_requires_every_required_check_to_have_passed(
    tmp_path: Path, missing_status: str
) -> None:
    report = InstallationReport(tmp_path)
    for check in CHECKS:
        if check.required and check.name != "native_window":
            report.record(check.name, "passed")
    report.record("native_window", cast(CheckStatus, missing_status))
    with pytest.raises(ValueError, match="Incomplete"):
        report.finish("automatic_checks_passed")
    assert read_report(tmp_path)["status"] != "automatic_checks_passed"


def test_automatic_success_keeps_live_acceptance_explicitly_unchecked(tmp_path: Path) -> None:
    report = InstallationReport(tmp_path)
    for check in CHECKS:
        if check.required:
            report.record(check.name, "passed")
    report.record("microphone", "warning", code="missing")
    report.finish("automatic_checks_passed")
    saved = read_report(tmp_path)
    assert saved["status"] == "automatic_checks_passed"
    for check in CHECKS:
        if check.scope == "manual":
            result = next(c for c in saved["diagnostics"] if c["id"] == check.name)
            assert result["status"] == "not_checked"
            assert result["evidence"] == ""
            assert result["next_action"]
    assert saved["windows_mvp_acceptance"] == "pending"
    markdown = (tmp_path / "installation-report.md").read_text(encoding="utf-8")
    assert "Полная работоспособность MVP **ещё не подтверждена**" in markdown
    assert "НЕ ПРОВЕРЕНО" in markdown


@pytest.mark.parametrize("name", [check.name for check in CHECKS if check.scope == "manual"])
def test_installer_cannot_mark_manual_acceptance_passed(tmp_path: Path, name: str) -> None:
    report = InstallationReport(tmp_path)
    with pytest.raises(ValueError, match="manual acceptance"):
        report.record(name, "passed")
    assert report.results[name]["status"] == "not_checked"


@pytest.mark.parametrize(
    ("name", "status", "code"),
    [
        ("SECRET", "passed", "none"),
        ("pip_check", "SECRET", "none"),
        ("pip_check", "failed", "SECRET"),
    ],
)
def test_arbitrary_diagnostic_strings_are_rejected_before_persistence(
    tmp_path: Path, name: str, status: str, code: str
) -> None:
    report = InstallationReport(tmp_path)
    report.save()
    before = (tmp_path / "installation-report.json").read_bytes()
    with pytest.raises(ValueError) as error:
        report.record(name, cast(CheckStatus, status), code=code)
    assert "SECRET" not in str(error.value)
    assert (tmp_path / "installation-report.json").read_bytes() == before
    assert "SECRET" not in (tmp_path / "installation-report.md").read_text(encoding="utf-8")


def test_cancelled_check_is_reported_without_certifying_remaining_checks(tmp_path: Path) -> None:
    report = InstallationReport(tmp_path)
    report.record("pip_check", "passed")
    report.record("offline_chromium", "running")
    report.finish("cancelled")
    assert read_report(tmp_path)["status"] == "cancelled"
    assert report.results["pip_check"]["status"] == "passed"
    assert report.results["offline_chromium"]["status"] == "cancelled"
    assert report.results["gui_smoke"]["status"] == "not_checked"


def test_probe_import_exception_returns_only_fixed_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(name: str) -> None:
        raise OSError("SECRET host path and exception detail")

    monkeypatch.setattr(importlib, "import_module", fail)
    assert probe.run_check("import_pydantic") == {"status": "failed", "code": "check_failed"}


@pytest.mark.parametrize("name", ["live_mail", "voice_hardware", "SECRET", "os"])
def test_probe_cli_rejects_unallowed_checks_without_running_them(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setattr(sys, "argv", ["probe", "--check", name])
    monkeypatch.setattr(probe, "run_check", lambda name: pytest.fail("Invalid check executed"))
    with pytest.raises(SystemExit) as error:
        probe.main()
    assert error.value.code == 2


def test_probe_cli_discards_library_prints_from_result_protocol(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def noisy_check(name: str) -> dict[str, object]:
        print("SECRET library diagnostic text")
        return {"status": "passed", "code": "none"}

    monkeypatch.setattr(sys, "argv", ["probe", "--check", "import_pydantic"])
    monkeypatch.setattr(probe, "run_check", noisy_check)
    probe.main()
    captured = capsys.readouterr()
    assert "SECRET" not in captured.out
    assert json.loads(captured.out) == {"status": "passed", "code": "none"}


@pytest.mark.parametrize("failure", ["missing", "unknown", "value_type", "unsafe_version"])
def test_package_metadata_accepts_only_the_public_bounded_whitelist(failure: str) -> None:
    packages: dict[str, object] = {package: "2.0.0" for package in probe.PACKAGES}
    if failure == "missing":
        packages.pop("pydantic")
    elif failure == "unknown":
        packages["SECRET"] = "2.0.0"
    elif failure == "value_type":
        packages["pydantic"] = {"SECRET": "2.0.0"}
    else:
        packages["pydantic"] = "SECRET host path"
    raw = json.dumps({"status": "passed", "code": "none", "packages": packages})
    with pytest.raises(ValueError) as error:
        setup.validate_probe("package_versions", raw)
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize(
    "result",
    [
        {"status": "passed", "code": "check_failed"},
        {"status": "warning", "code": "missing"},
        {"status": ["passed"], "code": "none"},
        {"status": "passed", "code": {"none": True}},
    ],
)
def test_required_probe_rejects_invalid_status_code_combinations(result: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        setup.validate_probe("import_pydantic", json.dumps(result))


def probe_output(command: Sequence[str]) -> str:
    if "--check" not in command:
        return ""
    name = command[command.index("--check") + 1]
    result: dict[str, Any] = {"status": "passed", "code": "none"}
    if name == "package_versions":
        result["packages"] = {package: "2.0.0" for package in probe.PACKAGES}
    return json.dumps(result)


@pytest.mark.parametrize("failure", ["timeout", "exception", "reported_failure"])
@pytest.mark.parametrize("failed_check", ["pip_check", "import_pydantic"])
def test_failed_child_is_finite_and_other_checks_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, failed_check: str
) -> None:
    report = InstallationReport(tmp_path)
    calls: list[list[str]] = []

    def run(command: Sequence[str], env: Mapping[str, str], *, seconds: int = 600) -> str:
        calls.append(list(command))
        failing = failed_check in command or (
            failed_check == "pip_check" and list(command[-2:]) == ["pip", "check"]
        )
        if failing:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(command, seconds, output="SECRET")
            if failure == "exception":
                raise OSError("SECRET")
            if failed_check == "pip_check":
                raise SetupError("SECRET")
            return json.dumps({"status": "failed", "code": "check_failed"})
        return probe_output(command)

    monkeypatch.setattr(setup, "run", run)
    with pytest.raises(SetupError) as error:
        setup.check_slot(tmp_path / "slot", "model", tmp_path, report)
    assert "SECRET" not in str(error.value)
    assert report.results[failed_check]["status"] == "failed"
    assert report.results[failed_check]["code"] == (
        "timeout" if failure == "timeout" else "check_failed"
    )
    assert report.results["memory"]["status"] == "passed"
    assert report.results["package_versions"]["status"] == "passed"
    assert report.results["gui_smoke"]["status"] == "passed"
    assert len(calls) > 10
    for path in tmp_path.glob("installation-report.*"):
        assert "SECRET" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "reply",
    [
        "SECRET raw native output",
        "[]",
        "null",
        '{"status":"passed","code":"none","message":"SECRET"}',
        '{"status":"passed","code":"SECRET"}',
        '{"status":"passed","code":"none","packages":{"SECRET":"1"}}',
        '{"status":"SECRET","code":"none"}',
    ],
)
def test_malformed_or_unbounded_child_reply_is_rejected_without_saving_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: str
) -> None:
    report = InstallationReport(tmp_path)

    def run(command: Sequence[str], env: Mapping[str, str], *, seconds: int = 600) -> str:
        return reply if "import_pydantic" in command else probe_output(command)

    monkeypatch.setattr(setup, "run", run)
    with pytest.raises(SetupError):
        setup.check_slot(tmp_path / "slot", "model", tmp_path, report)
    assert report.results["import_pydantic"]["status"] == "failed"
    assert report.results["import_pydantic"]["code"] == "invalid_response"
    assert report.results["memory"]["status"] == "passed"
    for path in tmp_path.glob("installation-report.*"):
        assert "SECRET" not in path.read_text(encoding="utf-8")


def test_diagnostic_children_use_literal_isolated_commands_and_temporary_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "SECRET")
    monkeypatch.setenv("PYTHONPATH", "SECRET")
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "user-data"))
    calls: list[tuple[list[str], dict[str, str], int]] = []

    def run(command: Sequence[str], env: Mapping[str, str], *, seconds: int = 600) -> str:
        calls.append((list(command), dict(env), seconds))
        return probe_output(command)

    monkeypatch.setattr(setup, "run", run)
    slot = tmp_path / "Асаль & Jarvis slot"
    setup.check_slot(slot, "model", tmp_path, InstallationReport(tmp_path))
    assert calls
    for command, env, seconds in calls:
        assert command[0] == str(slot / "venv/Scripts/python.exe")
        assert command[1] == "-I"
        assert "SECRET" not in str(env)
        assert 0 < seconds <= 90
        if "jarvis.installation.probe" in command or "--smoke-test" in command:
            assert env["JARVIS_DATA_DIR"] != str(tmp_path / "user-data")
    assert not (tmp_path / "user-data").exists()


def fixture_installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "installation"
    root.mkdir()
    repository = tmp_path / "repository"
    manifest = repository / "scripts/windows/assets.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"vosk":{"directory":"model"}}', encoding="utf-8")
    (root / "slots/a").mkdir(parents=True)
    (root / "slots/a/old-app").write_text("preserved", encoding="utf-8")
    setup.atomic_json(root / "active.json", {"slot": "a", "model": "model"})
    monkeypatch.setattr(setup, "source_digest", lambda repository: "fixture-fingerprint")
    monkeypatch.setattr(setup, "healthy", lambda slot, fingerprint: False)
    return repository, root


def record_components(report: InstallationReport) -> None:
    from jarvis.installation.probe import PROBE_CHECKS

    for name in (*PROBE_CHECKS, "pip_check", "gui_smoke"):
        report.record(name, "passed")


@pytest.mark.parametrize("phase", ["prepare", "check", "launch", "cancelled"])
def test_current_installation_failure_replaces_stale_success_and_keeps_active_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    repository, root = fixture_installation(tmp_path, monkeypatch)
    old = InstallationReport(root, "00000000-0000-4000-8000-000000000001")
    for catalog_check in CHECKS:
        if catalog_check.required:
            old.record(catalog_check.name, "passed")
    old.finish("automatic_checks_passed")
    reached: list[str] = []

    def prepare(*args: Any) -> Path:
        reached.append("prepare")
        if phase == "prepare":
            raise OSError("SECRET")
        if phase == "cancelled":
            raise KeyboardInterrupt("SECRET")
        slot = root / "slots/b"
        slot.mkdir()
        return slot

    def check(
        slot: Path, model: str, install_root: Path, report: InstallationReport | None = None
    ) -> dict[str, Any]:
        reached.append("check")
        assert report is not None
        if phase == "check":
            report.record("import_qt", "failed", code="check_failed")
            raise SetupError("SECRET")
        record_components(report)
        return {}

    def launch(*args: Any) -> None:
        reached.append("launch")
        raise SetupError("SECRET")

    monkeypatch.setattr(setup, "prepare_slot", prepare)
    monkeypatch.setattr(setup, "check_slot", check)
    monkeypatch.setattr(setup, "launch", launch)
    run_id = "00000000-0000-4000-8000-000000000002"
    with pytest.raises(KeyboardInterrupt if phase == "cancelled" else (SetupError, OSError)):
        setup.install(repository, root, run_id=run_id)
    saved = read_report(root)
    assert saved["run_id"] == run_id
    assert saved["status"] == ("cancelled" if phase == "cancelled" else "failed")
    assert saved["finished_at"]
    assert setup.active_slot(root) == "a"
    assert (root / "slots/a/old-app").read_text(encoding="utf-8") == "preserved"
    assert ("launch" in reached) == (phase == "launch")
    for path in root.glob("installation-report.*"):
        assert "SECRET" not in path.read_text(encoding="utf-8")
    assert "НЕ ПРОВЕРЕНО" in (root / "installation-report.md").read_text(encoding="utf-8")


def test_missing_mandatory_diagnostic_cannot_launch_or_activate_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, root = fixture_installation(tmp_path, monkeypatch)

    def prepare(*args: Any) -> Path:
        slot = root / "slots/b"
        slot.mkdir()
        return slot

    monkeypatch.setattr(setup, "prepare_slot", prepare)
    monkeypatch.setattr(setup, "check_slot", lambda *args: {})
    monkeypatch.setattr(setup, "launch", lambda *args: pytest.fail("Unverified candidate launched"))
    with pytest.raises((SetupError, ValueError)):
        setup.install(repository, root)
    assert setup.active_slot(root) == "a"
    saved = read_report(root)
    assert saved["status"] == "failed"
    assert next(c for c in saved["diagnostics"] if c["id"] == "native_window")["status"] == (
        "not_checked"
    )


def test_preflight_failure_has_a_current_finite_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, root = fixture_installation(tmp_path, monkeypatch)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(root / "slots/a/user-data"))
    with pytest.raises(SetupError):
        setup.install(repository, root)
    saved = read_report(root)
    assert saved["status"] == "failed"
    assert saved["finished_at"]
    assert next(c for c in saved["diagnostics"] if c["id"] == "preflight")["status"] == "failed"
    assert not (root / "slots/a/user-data").exists()
    assert setup.active_slot(root) == "a"


@pytest.mark.parametrize("cleanup_error", ["timeout", "oserror"])
def test_tree_cleanup_failure_still_reaps_real_direct_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cleanup_error: str,
) -> None:
    fixture = tmp_path / "child.py"
    fixture.write_text(
        "import time\nprint('SECRET child output', flush=True)\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    processes: list[subprocess.Popen[bytes]] = []
    tree_commands: list[list[str]] = []
    original_popen = subprocess.Popen

    def start(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    def failed_tree_cleanup(command: Sequence[str], **kwargs: Any) -> None:
        tree_commands.append(list(command))
        assert kwargs["timeout"] == 15
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        if cleanup_error == "timeout":
            raise subprocess.TimeoutExpired(command, 15, output="SECRET tree cleanup output")
        raise OSError("SECRET tree cleanup error")

    monkeypatch.setattr(subprocess, "Popen", start)
    monkeypatch.setattr(subprocess, "run", failed_tree_cleanup)
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path / "Windows"))
    with monkeypatch.context() as platform_patch:
        platform_patch.setattr(sys, "platform", "win32")
        with pytest.raises(subprocess.TimeoutExpired) as error:
            setup.run([sys.executable, str(fixture)], {}, seconds=1)
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert tree_commands == [
        [
            str(tmp_path / "Windows/System32/taskkill.exe"),
            "/PID",
            str(processes[0].pid),
            "/T",
            "/F",
        ]
    ]
    assert "SECRET" not in str(error.value)
    captured = capsys.readouterr()
    assert "SECRET" not in captured.out + captured.err


def test_report_write_failure_preserves_original_safe_setup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository, root = fixture_installation(tmp_path, monkeypatch)
    original_error = SetupError("Setup failed safely; repair and rerun.")
    refuse_writes = False
    original_atomic_text = report_module.atomic_text

    def write(path: Path, value: str) -> None:
        if refuse_writes:
            raise OSError("SECRET filesystem error")
        original_atomic_text(path, value)

    def prepare(*args: Any) -> Path:
        nonlocal refuse_writes
        refuse_writes = True
        raise original_error

    monkeypatch.setattr(report_module, "atomic_text", write)
    monkeypatch.setattr(setup, "prepare_slot", prepare)
    with pytest.raises(SetupError) as error:
        setup.install(repository, root)
    assert error.value is original_error
    assert setup.active_slot(root) == "a"
    captured = capsys.readouterr()
    assert "Installation report could not be saved" in captured.err
    assert "SECRET" not in str(error.value) + captured.out + captured.err
    for path in root.glob("installation-report.*"):
        assert "SECRET" not in path.read_text(encoding="utf-8")


def test_selected_commit_is_recorded_with_successful_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, root = fixture_installation(tmp_path, monkeypatch)
    commit = "a" * 40

    def prepare(*args: Any) -> Path:
        slot = root / "slots/b"
        slot.mkdir()
        return slot

    def check(
        slot: Path, model: str, install_root: Path, report: InstallationReport | None = None
    ) -> dict[str, Any]:
        assert report is not None
        record_components(report)
        return {}

    monkeypatch.setattr(setup, "prepare_slot", prepare)
    monkeypatch.setattr(setup, "check_slot", check)
    monkeypatch.setattr(setup, "launch", lambda *args: None)
    setup.install(repository, root, source_commit=commit)
    assert read_report(root)["source_commit"] == commit
    assert json.loads((root / "active.json").read_text())["source_commit"] == commit
    assert json.loads((root / "slots/b/receipt.json").read_text())["source_commit"] == commit
    assert commit in (root / "installation-report.md").read_text()


def test_failed_update_does_not_put_selected_commit_in_active_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, root = fixture_installation(tmp_path, monkeypatch)

    def fail(*args: Any) -> Path:
        raise SetupError("Preparation failed.")

    monkeypatch.setattr(setup, "prepare_slot", fail)
    with pytest.raises(SetupError):
        setup.install(repository, root, source_commit="b" * 40)
    assert read_report(root)["source_commit"] == "b" * 40
    assert read_report(root)["status"] == "failed"
    assert "source_commit" not in json.loads((root / "active.json").read_text())
    assert setup.active_slot(root) == "a"


def test_invalid_commit_is_refused_without_recording_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, root = fixture_installation(tmp_path, monkeypatch)
    with pytest.raises(SetupError):
        setup.install(repository, root, source_commit="SECRET untrusted source")
    assert read_report(root)["status"] == "failed"
    assert "SECRET" not in (root / "installation-report.json").read_text()
    assert setup.active_slot(root) == "a"
