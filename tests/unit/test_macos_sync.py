"""Coherent local builds, late-launch deferral and preservation of the installed app."""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest


def module() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts/macos/manage_installation.py"
    spec = importlib.util.spec_from_file_location("jarvis_sync_test", path)
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def bundle(root: Path, app: Path, text: str) -> None:
    resources = app / "Contents/Resources"
    data = resources / "app"
    data.mkdir(parents=True)
    (resources / ".jarvis-desktop-package").write_text("jarvis-local-package-v1")
    (data / "index.html").write_text(text)
    (data / "manifest.json").write_text(
        json.dumps(
            {
                "files": {"index.html": hashlib.sha256(text.encode()).hexdigest()},
            }
        )
    )
    (data / "python-runtime.json").write_text(
        json.dumps(
            {
                "root": str(root),
                "python": str(root / ".venv/bin/python"),
            }
        )
    )


@pytest.fixture
def installation(tmp_path: Path) -> tuple[ModuleType, Path, Path]:
    root = tmp_path / "source with spaces"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/package_desktop.py").write_text(
        "def package(repository, output):\n    raise AssertionError('replace with fixture')\n"
    )
    app = tmp_path / "Applications/Jarvis.app"
    bundle(root, app, "old")
    assets = root / "src/jarvis/desktop_assets"
    assets.mkdir(parents=True)
    (assets / "manifest.json").write_bytes(
        (app / "Contents/Resources/app/manifest.json").read_bytes()
    )
    return module(), root, app


def test_current_checks_bytes_and_source_binding(
    installation: tuple[ModuleType, Path, Path],
) -> None:
    sync, root, app = installation
    assert sync.current(root, app)
    assert not sync.current(root / "other", app)
    (app / "Contents/Resources/app/index.html").write_text("corrupt")
    assert not sync.current(root, app)


def test_running_app_is_not_packaged_or_restarted(
    installation: tuple[ModuleType, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    sync, root, app = installation
    (root / "src/jarvis/desktop_assets/manifest.json").write_text('{"files":{}}')
    monkeypatch.setattr(sync, "running", lambda app: True)
    assert sync.sync(root, app) == "waiting_for_close"
    assert (app / "Contents/Resources/app/index.html").read_text() == "old"


@pytest.mark.parametrize("late_launch", [False, True])
def test_staged_update_is_published_only_while_closed(
    installation: tuple[ModuleType, Path, Path], monkeypatch: pytest.MonkeyPatch, late_launch: bool
) -> None:
    sync, root, app = installation
    staged = root / "fixture.app"
    bundle(root, staged, "new")
    (root / "src/jarvis/desktop_assets/manifest.json").write_bytes(
        (staged / "Contents/Resources/app/manifest.json").read_bytes()
    )
    launches = iter([False, late_launch])
    monkeypatch.setattr(sync, "running", lambda app: next(launches))
    calls: list[Path] = []
    monkeypatch.setattr(sync, "register", calls.append)
    original = sync.importlib.util.module_from_spec

    def packager(spec: Any) -> ModuleType:
        result = cast(ModuleType, original(spec))

        class Loader:
            def exec_module(self, target: ModuleType) -> None:
                def package(repository: Path, output: Path) -> None:
                    bundle(repository, output, "new")

                target.__dict__["package"] = package

        spec.loader = Loader()
        return result

    monkeypatch.setattr(sync.importlib.util, "module_from_spec", packager)
    assert sync.sync(root, app) == ("waiting_for_close" if late_launch else "updated")
    assert (app / "Contents/Resources/app/index.html").read_text() == (
        "old" if late_launch else "new"
    )
    assert calls == ([] if late_launch else [app])


def test_failed_package_keeps_old_app(
    installation: tuple[ModuleType, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    sync, root, app = installation
    (root / "src/jarvis/desktop_assets/manifest.json").write_text('{"files":{}}')
    monkeypatch.setattr(sync, "running", lambda app: False)
    with pytest.raises(AssertionError):
        sync.sync(root, app)
    assert (app / "Contents/Resources/app/index.html").read_text() == "old"


def test_foreign_app_is_never_overwritten(tmp_path: Path) -> None:
    app = tmp_path / "Jarvis.app"
    app.mkdir()
    with pytest.raises(ValueError, match="not made by Jarvis"):
        module().sync(tmp_path, app)


def test_agent_is_a_fixed_local_job_without_application_autostart(tmp_path: Path) -> None:
    sync = module()
    job = sync.agent(tmp_path / "source", tmp_path / "sync.py")
    assert job["ProgramArguments"] == [
        str(tmp_path / "source/.venv/bin/python"),
        str(tmp_path / "sync.py"),
        "--sync",
    ]
    assert job["StartInterval"] == 30 and job["RunAtLoad"]
    assert "KeepAlive" not in job and "open" not in job["ProgramArguments"]
    assert job["WatchPaths"] == [str(tmp_path / "source/src/jarvis/desktop_assets/manifest.json")]


def test_cli_refuses_non_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    sync = module()
    monkeypatch.setattr(sync.sys, "platform", "win32")
    monkeypatch.setattr(sync.sys, "argv", ["sync.py", "--sync"])
    with pytest.raises(SystemExit):
        sync.main()
