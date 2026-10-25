"""Shareable installer documents preserve evidence and exclude untrusted report content."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from jarvis.installation import share
from jarvis.installation.probe import PACKAGES
from jarvis.installation.report import CHECKS, InstallationReport

RUN_ID = "cc142ad1-9ae8-4ddc-ac37-696e8468a221"
FINGERPRINT = "ab" * 32
SECRET = "SECRET_ACCOUNT_PATH_OR_TOKEN<script>alert(1)</script>"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def create_symlink(path: Path, target: Path) -> None:
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("Creating symlinks requires permission on this host.")


def bootstrap(root: Path, status: str = "passed") -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": 2,
        "run_id": RUN_ID,
        "status": status,
        "started_at_utc": "2026-10-15T04:00:00+00:00",
        "finished_at_utc": "2026-10-15T04:05:00+00:00",
        "phases": [{"id": name, "status": "passed"} for name in share.PHASES],
    }
    write_json(root / "installation-bootstrap.json", value)
    return value


def application(root: Path) -> InstallationReport:
    report = InstallationReport(root, RUN_ID)
    report.value.update(source=FINGERPRINT, python="3.12.10")
    report.value["checks"] = {"packages": {name: "1.2.3" for name in PACKAGES}}
    for check in CHECKS:
        if check.required:
            report.record(check.name, "passed")
    report.finish("automatic_checks_passed")
    return report


@pytest.fixture
def reports(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "bootstrap"
    app_root = tmp_path / "application"
    root.mkdir()
    app_root.mkdir()
    bootstrap(root)
    application(app_root)
    return root, app_root


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_matching_reports_create_standalone_documents_with_bounded_functional_evidence(
    reports: tuple[Path, Path],
) -> None:
    root, app_root = reports
    share.export_report(root, app_root, RUN_ID)
    markdown = (root / "Jarvis-Report.md").read_text(encoding="utf-8")
    page = (root / "Jarvis-Report.html").read_text(encoding="utf-8")
    for document in (markdown, page):
        assert RUN_ID in document and FINGERPRINT in document
        assert "3.12.10" in document and "PySide6-Essentials" in document
        assert "ПРОЙДЕНО: локальный сценарий" in document
        assert "КОМПОНЕНТЫ ПРОВЕРЕНЫ; реальный сценарий НЕ ПРОВЕРЕН" in document
        assert "MVP пока НЕ ПОДТВЕРЖДЕНА" in document
        assert str(root) not in document and str(app_root) not in document
    assert "<!doctype html>" in page and "<meta charset='utf-8'>" in page
    assert "Content-Security-Policy" in page and "default-src 'none'" in page
    for forbidden in ("<script", "<iframe", "<img", "<link", "src=", "href=", "url("):
        assert forbidden not in page.lower()
    assert not list(root.glob("tmp*"))


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
def test_failure_or_cancellation_preserves_completed_and_unperformed_checks(
    tmp_path: Path, outcome: str
) -> None:
    root = tmp_path / "reports"
    root.mkdir()
    boot = bootstrap(root, outcome)
    boot["phases"] = [
        {"id": "host", "status": "passed"},
        {"id": "runtime", "status": outcome},
    ]
    write_json(root / "installation-bootstrap.json", boot)
    report = InstallationReport(root, RUN_ID)
    report.record("permissions", "passed", duration_ms=7)
    report.record("planner", "running")
    report.finish("cancelled" if outcome == "cancelled" else "failed")
    markdown, page = share.render_report(root, root, RUN_ID)
    code = "setup_failed" if outcome == "failed" else "cancelled"
    for document in (markdown, page):
        assert "ПРОЙДЕНО: локальный сценарий" in document
        assert f"{code} / 0 мс" in document
        assert "НЕ ПРОВЕРЕНО" in document
        assert "2026-10-15T04:00:00+00:00" in document
    assert "| Память: запись, чтение, изменение и удаление (memory) | НЕ ПРОВЕРЕНО |" in markdown


@pytest.mark.parametrize("problem", ["missing", "stale", "invalid", "oversized", "symlink"])
def test_unusable_application_report_never_contributes_old_success_or_metadata(
    reports: tuple[Path, Path], problem: str
) -> None:
    root, app_root = reports
    path = app_root / "installation-report.json"
    if problem == "missing":
        path.unlink()
    elif problem == "stale":
        value = read_json(path)
        value["run_id"] = str(uuid4())
        write_json(path, value)
    elif problem == "invalid":
        path.write_text("{" + SECRET, encoding="utf-8")
    elif problem == "oversized":
        path.write_bytes(b" " * (share.LIMIT + 1))
    else:
        private = app_root / "private.json"
        path.rename(private)
        create_symlink(path, private)
    markdown, page = share.render_report(root, app_root, RUN_ID)
    for document in (markdown, page):
        assert "Установщик: ПРОЙДЕНО" in document
        assert "Автодиагностика приложения: НЕ ПРОВЕРЕНО" in document
        assert "ПРОЙДЕНО: локальный сценарий" not in document
        assert FINGERPRINT not in document and "3.12.10" not in document
        assert SECRET not in document
    assert share.UNAVAILABLE[problem if problem in {"missing", "stale"} else "invalid"] in markdown


@pytest.mark.parametrize("report_name", ["installation-bootstrap.json", "installation-report.json"])
@pytest.mark.parametrize("status", [[], {}, 42, None])
def test_malformed_typed_status_does_not_prevent_export_of_independent_evidence(
    reports: tuple[Path, Path], report_name: str, status: object
) -> None:
    root, app_root = reports
    path = (root if "bootstrap" in report_name else app_root) / report_name
    value = read_json(path)
    value.update(status=status, source=SECRET, python=SECRET)
    write_json(path, value)
    share.export_report(root, app_root, RUN_ID)
    for name in ("Jarvis-Report.md", "Jarvis-Report.html"):
        document = (root / name).read_text(encoding="utf-8")
        assert share.UNAVAILABLE["invalid"] in document
        assert SECRET not in document
        if "bootstrap" in report_name:
            assert "ПРОЙДЕНО: локальный сценарий" in document
        else:
            assert "Установщик: ПРОЙДЕНО" in document
            assert FINGERPRINT not in document


@pytest.mark.parametrize("problem", ["duplicate", "unknown", "missing_pass", "bad_schema"])
def test_malformed_bootstrap_cannot_certify_setup(reports: tuple[Path, Path], problem: str) -> None:
    root, app_root = reports
    path = root / "installation-bootstrap.json"
    value = read_json(path)
    if problem == "duplicate":
        value["phases"] = [value["phases"][0], value["phases"][0]]
    elif problem == "unknown":
        value["phases"] = [{"id": SECRET, "status": "passed"}]
    elif problem == "missing_pass":
        value["phases"].pop()
    else:
        value["schema"] = 1
    write_json(path, value)
    markdown, page = share.render_report(root, app_root, RUN_ID)
    for document in (markdown, page):
        assert "Установщик: НЕ ПРОВЕРЕНО" in document
        assert share.UNAVAILABLE["invalid"] in document
        assert SECRET not in document


@pytest.mark.parametrize("problem", ["manual_pass", "duplicate", "unknown", "missing_required"])
def test_invalid_application_claims_never_make_functions_pass(
    reports: tuple[Path, Path], problem: str
) -> None:
    root, app_root = reports
    path = app_root / "installation-report.json"
    value = read_json(path)
    rows = value["diagnostics"]
    if problem == "manual_pass":
        next(row for row in rows if row["id"] == "live_mail")["status"] = "passed"
    elif problem == "duplicate":
        rows[-1] = rows[0]
    elif problem == "unknown":
        rows[-1]["id"] = SECRET
    else:
        value["diagnostics"] = [row for row in rows if row["id"] != "native_window"]
    write_json(path, value)
    markdown, page = share.render_report(root, app_root, RUN_ID)
    for document in (markdown, page):
        assert "Автодиагностика приложения: НЕ ПРОВЕРЕНО" in document
        assert "ПРОЙДЕНО: локальный сценарий" not in document
        assert FINGERPRINT not in document
        assert SECRET not in document


def test_freeform_fields_and_nonallowlisted_metadata_never_enter_shared_document(
    reports: tuple[Path, Path],
) -> None:
    root, app_root = reports
    path = app_root / "installation-report.json"
    value = read_json(path)
    value.update(platform=SECRET, source_directory=SECRET, stdout=SECRET, error=SECRET)
    value["checks"]["packages"]["SECRET_PACKAGE"] = "1.2.3"
    value["checks"]["packages"]["pydantic"] = SECRET
    value["checks"]["warnings"] = [SECRET]
    for row in value["diagnostics"]:
        row.update(label=SECRET, evidence=SECRET, next_action=SECRET, stdout=SECRET)
    write_json(path, value)
    boot = read_json(root / "installation-bootstrap.json")
    boot.update(source_directory=SECRET, error=SECRET)
    for phase in boot["phases"]:
        phase.update(name=SECRET, remedy=SECRET, error_category=SECRET)
    write_json(root / "installation-bootstrap.json", boot)
    markdown, page = share.render_report(root, app_root, RUN_ID)
    for document in (markdown, page):
        assert "SECRET" not in document and "<script" not in document
        assert FINGERPRINT in document
        assert "PySide6-Essentials" in document
        assert "ПРОЙДЕНО: локальный сценарий" in document


@pytest.mark.parametrize("fingerprint", [SECRET, "a" * 63, "a" * 65, "A" * 64, {}, None])
def test_invalid_source_fingerprints_are_excluded(
    reports: tuple[Path, Path], fingerprint: object
) -> None:
    root, app_root = reports
    path = app_root / "installation-report.json"
    value = read_json(path)
    value["source"] = fingerprint
    write_json(path, value)
    markdown, page = share.render_report(root, app_root, RUN_ID)
    for document in (markdown, page):
        assert "SHA-256 поставляемых исходников: не получен" in document
        assert "SECRET" not in document


def test_output_symlink_is_refused_without_modifying_its_target(reports: tuple[Path, Path]) -> None:
    root, app_root = reports
    private = app_root / "private.html"
    private.write_text(SECRET, encoding="utf-8")
    create_symlink(root / "Jarvis-Report.html", private)
    with pytest.raises(OSError, match="links"):
        share.export_report(root, app_root, RUN_ID)
    assert private.read_text(encoding="utf-8") == SECRET


def test_report_cli_runs_in_isolated_stdlib_only_python_after_early_bootstrap_failure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "report with spaces и кириллицей"
    root.mkdir()
    value = bootstrap(root, "failed")
    value["phases"] = [{"id": "host", "status": "failed"}]
    write_json(root / "installation-bootstrap.json", value)
    script = Path(__file__).resolve().parents[2] / "scripts" / "windows" / "export_report.py"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(script),
            "--root",
            str(root),
            "--application-root",
            str(root),
            "--run-id",
            RUN_ID,
        ],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout == b"" and result.stderr == b""
    for name in ("Jarvis-Report.html", "Jarvis-Report.md"):
        document = (root / name).read_text(encoding="utf-8")
        assert "Установщик: ОШИБКА" in document
        assert "Автодиагностика приложения: НЕ ПРОВЕРЕНО" in document
        assert "ПРОЙДЕНО: локальный сценарий" not in document
        assert RUN_ID in document
