"""Actual Git/PowerShell workflows against private local fixtures, never GitHub or installation."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

CANONICAL = "https://github.com/xumezzan/JARVIS-Windows.git"


def git(directory: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.longpaths=true", "-C", str(directory), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ | {"GIT_TERMINAL_PROMPT": "0"},
    ).stdout.strip()


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, Path, str, str]:
    origin = tmp_path / "fixture-origin.git"
    origin.mkdir()
    git(origin, "init", "--bare")
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-b", "main")
    git(seed, "config", "user.name", "Fixture")
    git(seed, "config", "user.email", "fixture@example.test")
    scripts = seed / "scripts/windows"
    scripts.mkdir(parents=True)
    production = Path(__file__).resolve().parents[2] / "scripts/windows/Update-Jarvis.ps1"
    # Only the trusted transport constant differs; all Git/Process/worktree code stays real.
    fixture_text = production.read_text(encoding="utf-8-sig").replace(
        "$canonical = '" + CANONICAL + "'",
        "$canonical = '" + str(origin).replace("'", "''") + "'",
    )
    (scripts / production.name).write_text(fixture_text, encoding="utf-8-sig")
    (seed / "README.md").write_text("old fixture")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "old fixture")
    before = git(seed, "rev-parse", "HEAD")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "origin", "main")
    repository = tmp_path / "Асаль working copy"
    subprocess.run(
        ["git", "clone", "-b", "main", str(origin), str(repository)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    (seed / "README.md").write_text("new published fixture")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "new fixture")
    after = git(seed, "rev-parse", "HEAD")
    git(seed, "push", "origin", "main")
    return repository, origin, before, after


def update(
    repository: Path, temporary: Path
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    pwsh = os.environ.get("JARVIS_TEST_PWSH") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell unavailable; portable updater scenarios require its runtime.")
    temporary.mkdir()
    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(repository / "scripts/windows/Update-Jarvis.ps1"),
            "-SkipInstall",
        ],
        env=os.environ | {"TMPDIR": str(temporary), "TEMP": str(temporary), "TMP": str(temporary)},
        capture_output=True,
        text=True,
        timeout=90,
    )
    reports = list(temporary.glob("JarvisUpdate-*/update-report.json"))
    assert len(reports) == 1, result.stdout + result.stderr
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    markdown = reports[0].with_suffix(".md").read_text(encoding="utf-8")
    assert report["windows_mvp_acceptance"] == "pending"
    assert "source_ready" in markdown or "failed" in markdown
    assert "PRIVATE_LOCAL_CONTENT" not in markdown + json.dumps(report)
    return result, report


@pytest.mark.parametrize("kind", ["clean", "dirty", "branch", "diverged", "detached"])
def test_latest_source_preserves_local_work_and_history(
    tmp_path: Path, source: tuple[Path, Path, str, str], kind: str
) -> None:
    repository, _, before, after = source
    if kind == "dirty":
        (repository / "README.md").write_text("PRIVATE_LOCAL_CONTENT")
        (repository / "private-untracked.txt").write_text("PRIVATE_LOCAL_CONTENT")
    elif kind == "branch":
        git(repository, "switch", "-c", "local-work")
    elif kind == "detached":
        git(repository, "switch", "--detach")
    elif kind == "diverged":
        git(repository, "config", "user.name", "Fixture")
        git(repository, "config", "user.email", "fixture@example.test")
        (repository / "local.txt").write_text("PRIVATE_LOCAL_CONTENT")
        git(repository, "add", ".")
        git(repository, "commit", "-m", "local only")
        before = git(repository, "rev-parse", "HEAD")
    result, report = update(repository, tmp_path / "reports")
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["status"] == "source_ready"  # Never claims installation from -SkipInstall.
    assert report["previous_checkout_commit"] == before
    assert report["source_commit"] == after
    snapshot = Path(report["source_directory"])
    assert git(snapshot, "rev-parse", "HEAD") == after
    assert (snapshot / "README.md").read_text() == "new published fixture"
    if kind == "clean":
        assert snapshot == repository and report["checkout_mode"] == "fast_forward"
        assert git(repository, "rev-parse", "HEAD") == after
    else:
        assert snapshot != repository and report["checkout_mode"] == "isolated_checkout"
        assert git(repository, "rev-parse", "HEAD") == before
        if kind == "dirty":
            assert (repository / "README.md").read_text() == "PRIVATE_LOCAL_CONTENT"
            assert (repository / "private-untracked.txt").read_text() == "PRIVATE_LOCAL_CONTENT"
        if kind == "diverged":
            assert (repository / "local.txt").read_text() == "PRIVATE_LOCAL_CONTENT"


def test_no_new_commit_still_prepares_source_without_claiming_installation(
    tmp_path: Path, source: tuple[Path, Path, str, str]
) -> None:
    repository, _, _, after = source
    git(repository, "fetch", "origin", "main")
    git(repository, "merge", "--ff-only", "origin/main")
    result, report = update(repository, tmp_path / "reports")
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["previous_checkout_commit"] == report["source_commit"] == after
    assert report["status"] == "source_ready"


def test_unavailable_remote_does_not_change_source_or_claim_update(
    tmp_path: Path, source: tuple[Path, Path, str, str]
) -> None:
    repository, origin, before, _ = source
    origin.rename(origin.with_name("fixture-origin-offline.git"))
    result, report = update(repository, tmp_path / "reports")
    assert result.returncode != 0
    assert report["status"] == "failed" and report["phase"] == "fetch_main"
    assert report["source_commit"] is None
    assert git(repository, "rev-parse", "HEAD") == before


def test_other_remote_is_refused_before_fetch(
    tmp_path: Path, source: tuple[Path, Path, str, str]
) -> None:
    repository, _, before, _ = source
    git(repository, "remote", "set-url", "origin", "PRIVATE_LOCAL_CONTENT")
    result, report = update(repository, tmp_path / "reports")
    assert result.returncode != 0 and report["phase"] == "preflight"
    assert report["source_commit"] is None
    assert "PRIVATE_LOCAL_CONTENT" not in result.stdout + result.stderr
    assert git(repository, "rev-parse", "HEAD") == before


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "commit",
        "run_id",
        "status",
        "bootstrap",
        "invalid_id",
        "bootstrap_commit",
        "active_commit",
        "active_slot",
    ],
)
def test_stale_or_incomplete_installation_reports_cannot_certify_update(
    tmp_path: Path, change: str
) -> None:
    pwsh = os.environ.get("JARVIS_TEST_PWSH") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell unavailable.")
    production = Path(__file__).resolve().parents[2] / "scripts/windows/Update-Jarvis.ps1"
    run_id = "00000000-0000-4000-8000-000000000001"
    installation = {
        "source_commit": "a" * 40,
        "status": "automatic_checks_passed",
        "run_id": run_id,
        "slot": "b",
    }
    bootstrap = {"status": "passed", "run_id": run_id, "source_commit": "a" * 40}
    active = {"source_commit": "a" * 40, "slot": "b"}
    if change == "commit":
        installation["source_commit"] = "b" * 40
    elif change == "run_id":
        bootstrap["run_id"] = "00000000-0000-4000-8000-000000000002"
    elif change == "status":
        installation["status"] = "running"
    elif change == "bootstrap":
        bootstrap["status"] = "failed"
    elif change == "invalid_id":
        installation["run_id"] = bootstrap["run_id"] = "not-a-run"
    elif change == "bootstrap_commit":
        bootstrap["source_commit"] = "b" * 40
    elif change == "active_commit":
        active["source_commit"] = "b" * 40
    elif change == "active_slot":
        active["slot"] = "a"
    fixture = tmp_path / "reports.json"
    fixture.write_text(
        json.dumps({"installation": installation, "bootstrap": bootstrap, "active": active}),
        encoding="utf-8",
    )
    driver = tmp_path / "verify.ps1"
    driver.write_text(
        "param([string]$Source,[string]$Fixture)\n"
        "$tokens=$null; $errors=$null\n"
        "$ast=[System.Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)\n"
        "$function=$ast.Find({param($node) "
        "$node -is [System.Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -eq 'Test-InstallationReports'},$true)\n"
        "Invoke-Expression $function.Extent.Text\n"
        "$data=Get-Content -Encoding UTF8 -LiteralPath $Fixture -Raw | ConvertFrom-Json\n"
        "$result=Test-InstallationReports $data.installation $data.bootstrap "
        "$data.active ('a'*40)\n"
        "if($result){'verified'}else{'refused'}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(driver), str(production), str(fixture)],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    assert result.stdout.strip() == ("verified" if change == "none" else "refused")
