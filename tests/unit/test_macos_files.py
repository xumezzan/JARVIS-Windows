"""Portable macOS file-policy checks and synthetic, reversible file-adapter effects."""

import os
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.files.policy import FilePolicy as LegacyPolicy
from jarvis.files.policy import default_roots
from jarvis.installation import setup
from jarvis.observability.audit import AuditLog, ErrorCode
from jarvis.permissions.approvals import ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.platforms.files import LocalFiles
from jarvis.security.filesystem_policy import FilePolicy, Root
from jarvis.tools.base import ToolError
from jarvis.tools.files import register_files
from jarvis.tools.filesystem import register_filesystem
from jarvis.tools.registry import ToolRegistry


def test_macos_default_roots_are_own_document_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("Desktop", "Documents", "Downloads", "Pictures", "Library"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert default_roots() == tuple(
        str(tmp_path / name) for name in ("Desktop", "Documents", "Downloads")
    )


@pytest.mark.parametrize("relative", [".env", ".env.local", ".ssh/key.txt", "nested/.env"])
@pytest.mark.parametrize("mode", [Mode.SIMULATION, Mode.EXECUTE])
def test_macos_secrets_are_refused_before_either_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str, mode: Mode
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    registry = ToolRegistry()
    register_files(registry, LegacyPolicy((str(tmp_path),)), LocalFiles())
    documents = FilePolicy(
        roots=(Root(name="documents", path=str(tmp_path)),),
        workspace=str(tmp_path),
        protected=str(tmp_path / "data"),
    )
    register_filesystem(registry, documents)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, ApprovalStore(), audit)
        for name in ("files.read_text", "fs.read_document"):
            result = engine.prepare(name, {"path": str(tmp_path / relative)}, mode)
            assert isinstance(result, Outcome) and result.status is Status.INVALID
            assert result.error in (ErrorCode.PATH_DENIED, ErrorCode.FILE_DENIED)


@pytest.mark.parametrize("root", ["/System", "/Library/Keychains"])
def test_macos_system_roots_cannot_be_explicitly_granted(
    monkeypatch: pytest.MonkeyPatch, root: str
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    policy = LegacyPolicy((str(Path(root).absolute()),))
    with pytest.raises(ToolError, match="path_denied"):
        policy.check(str(Path(root).absolute() / "document.txt"))
    documents = FilePolicy(
        roots=(Root(name="explicit", path=str(Path(root).absolute())),),
        workspace=str(Path(root).absolute()),
        protected=str(Path(root).absolute() / "data"),
    )
    with pytest.raises(ToolError, match="file_denied"):
        documents.path(str(Path(root).absolute() / "document.txt"))


def test_macos_alias_to_secret_is_refused_by_both_policies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    secret = tmp_path / ".env"
    secret.write_text("synthetic fixture")
    link = tmp_path / "ordinary.txt"
    try:
        link.symlink_to(secret)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Requires SeCreateSymbolicLinkPrivilege on Windows")
        raise
    policies = (
        LegacyPolicy((str(tmp_path),)),
        FilePolicy(
            roots=(Root(name="documents", path=str(tmp_path)),),
            workspace=str(tmp_path),
            protected=str(tmp_path / "data"),
        ),
    )
    with pytest.raises(ToolError):
        policies[0].check(str(link))
    with pytest.raises(ToolError):
        policies[1].path(str(link))


@pytest.mark.parametrize("suffix", [".command", ".sh", ".scpt", ".app", ".py"])
def test_macos_programs_cannot_be_opened_as_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(ToolError, match="file_unsupported"):
        LegacyPolicy((str(tmp_path),)).openable(tmp_path / ("run" + suffix))


def test_macos_document_open_uses_exact_argv_no_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    path = tmp_path / "сводка $(not-a-command).txt"
    seen: list[tuple[list[str], dict[str, Any]]] = []

    def run(args: list[str], **kwargs: Any) -> None:
        seen.append((args, kwargs))

    monkeypatch.setattr(subprocess, "run", run)
    LocalFiles._open(path)
    assert seen[0][0] == ["/usr/bin/open", str(path)]
    assert seen[0][1]["timeout"] == 8 and "shell" not in seen[0][1]


@pytest.mark.skipif(os.name == "nt", reason="Synthetic POSIX directory descriptor operations")
def test_macos_trash_preserves_existing_names_and_moves_without_copying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    trash = tmp_path / ".Trash"
    trash.mkdir()
    (trash / "report.txt").write_text("previous document")
    source = tmp_path / "report.txt"
    source.write_text("new document")
    inode = source.stat().st_ino

    def exclusive(source_fd: int, name: str, trash_fd: int, target: str) -> None:
        # Emulate native RENAME_EXCL only within this synthetic filesystem.
        if os.path.exists(trash / target):
            raise FileExistsError
        os.rename(name, target, src_dir_fd=source_fd, dst_dir_fd=trash_fd)

    monkeypatch.setattr(LocalFiles, "_macos_rename_exclusive", staticmethod(exclusive))
    LocalFiles._recycle(source)
    assert not source.exists()
    assert (trash / "report.txt").read_text() == "previous document"
    moved = [path for path in trash.iterdir() if path.name != "report.txt"]
    assert len(moved) == 1 and moved[0].read_text() == "new document"
    assert moved[0].stat().st_ino == inode


@pytest.mark.skipif(os.name == "nt", reason="Synthetic POSIX symlink operations")
def test_macos_trash_refuses_a_redirected_trash_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".Trash").symlink_to(outside)
    source = tmp_path / "report.txt"
    source.write_text("document")
    with pytest.raises(ToolError, match="file_failure"):
        LocalFiles._recycle(source)
    assert source.read_text() == "document" and not list(outside.iterdir())


@pytest.mark.parametrize("machine,supported", [("arm64", True), ("x86_64", True), ("i386", False)])
def test_macos_supported_host(
    monkeypatch: pytest.MonkeyPatch, machine: str, supported: bool
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(setup.platform, "machine", lambda: machine)
    assert setup.supported_host() is supported


@pytest.mark.parametrize("build,supported", [(21999, False), (22000, True)])
def test_windows_supported_host_still_requires_windows_11(
    monkeypatch: pytest.MonkeyPatch, build: int, supported: bool
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(setup.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(
        sys, "getwindowsversion", lambda: SimpleNamespace(build=build), raising=False
    )
    assert setup.supported_host() is supported


def test_macos_windows_installer_directs_to_macos_launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "argv", ["setup", "--root", str(tmp_path)])
    monkeypatch.setattr(
        setup, "install", lambda *a, **kw: pytest.fail("Windows install must not run")
    )
    assert setup.main() == 2
    assert "scripts/macos/Launch-Jarvis.command" in capsys.readouterr().err
