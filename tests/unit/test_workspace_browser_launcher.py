"""Fixed browser arguments and provenance: a foreign listener never becomes a CDP grant."""

import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import psutil
import pytest

from jarvis.platforms import workspace_browser as module
from jarvis.platforms.workspace_browser import BrowserLaunchError, BrowserReceipt, WorkspaceBrowser


def test_foreign_port_is_refused_without_start_or_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Occupied:
        def __enter__(self) -> "Occupied":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def bind(self, address: object) -> None:
            raise OSError("occupied")

    monkeypatch.setattr(socket, "socket", Occupied)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("must not launch"))
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: pytest.fail("must not connect"))
    with pytest.raises(BrowserLaunchError, match="workspace_browser_port_busy"):
        WorkspaceBrowser(tmp_path).launch()


def test_receipt_requires_exact_process_start_executable_and_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "chrome.exe"
    executable.touch()
    browser = WorkspaceBrowser(tmp_path)
    arguments = [
        str(executable),
        f"--user-data-dir={browser.profile}",
        "--remote-debugging-port=9222",
        "--remote-debugging-address=127.0.0.1",
    ]

    class Process:
        def create_time(self) -> float:
            return 10.0

        def exe(self) -> str:
            return str(executable)

        def cmdline(self) -> list[str]:
            return arguments

    monkeypatch.setattr(module, "installed_browsers", lambda: (executable,))
    monkeypatch.setattr(psutil, "Process", lambda pid: Process())
    receipt = BrowserReceipt(pid=123, started=10.0, executable=str(executable))
    browser._process(receipt)
    for change in ({"started": 11.0}, {"executable": str(tmp_path / "other.exe")}):
        with pytest.raises(BrowserLaunchError, match="workspace_browser_untrusted"):
            browser._process(BrowserReceipt.model_validate(receipt.model_dump() | change))
    arguments[1] = f"--user-data-dir={tmp_path / 'personal-profile'}"
    with pytest.raises(BrowserLaunchError, match="workspace_browser_untrusted"):
        browser._process(receipt)


def test_windows_discovery_never_uses_path_or_user_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("PATH", "untrusted-browser")
    candidates = module.installed_browsers()
    assert candidates == (
        tmp_path / "Google/Chrome/Application/chrome.exe",
        tmp_path / "Microsoft/Edge/Application/msedge.exe",
    )


def test_macos_discovery_uses_only_application_bundle_executables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PATH", "untrusted-browser")
    assert module.installed_browsers() == tuple(
        base / bundle / "Contents/MacOS" / executable
        for base in (Path("/Applications"), tmp_path / "Applications")
        for bundle, executable in (
            ("Google Chrome.app", "Google Chrome"),
            ("Microsoft Edge.app", "Microsoft Edge"),
            ("Brave Browser.app", "Brave Browser"),
        )
    )
