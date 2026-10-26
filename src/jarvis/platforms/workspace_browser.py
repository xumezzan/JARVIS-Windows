"""Fixed Chrome/Edge launch, isolated profile and OS-owned loopback CDP provenance."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import psutil
from pydantic import Field

from jarvis.tools.base import ToolModel
from jarvis.workspace.store import JsonSettingsStore, WorkspaceFailure

ENDPOINT = "http://127.0.0.1:9222"


class BrowserLaunchError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class BrowserReceipt(ToolModel):
    pid: int = Field(default=0, ge=0)
    started: float = 0.0
    executable: str = ""


def installed_browsers() -> tuple[Path, ...]:
    platform = sys.platform
    if platform == "darwin":
        return tuple(
            base / bundle / "Contents" / "MacOS" / name
            for base in (Path("/Applications"), Path.home() / "Applications")
            for bundle, name in (
                ("Google Chrome.app", "Google Chrome"),
                ("Microsoft Edge.app", "Microsoft Edge"),
            )
        )
    if platform == "win32":
        return tuple(
            Path(base) / product / "Application" / binary
            for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
            if (base := os.environ.get(key)) and Path(base).is_absolute()
            for product, binary in (
                ("Google/Chrome", "chrome.exe"),
                ("Microsoft/Edge", "msedge.exe"),
            )
        )
    return ()


class WorkspaceBrowser:
    def __init__(self, data_dir: Path):
        self.profile = data_dir / "workspace_chrome_profile"
        self.store = JsonSettingsStore(data_dir / "workspace_browser.json", BrowserReceipt)
        self.child: subprocess.Popen[bytes] | None = None

    def _process(self, receipt: BrowserReceipt) -> psutil.Process:
        try:
            process = psutil.Process(receipt.pid)
            allowed = {path.resolve() for path in installed_browsers() if path.is_file()}
            executable = Path(process.exe()).resolve()
            args = process.cmdline()
            if (
                not receipt.pid
                or abs(process.create_time() - receipt.started) > 0.01
                or executable not in allowed
                or executable != Path(receipt.executable)
                or f"--user-data-dir={self.profile}" not in args
                or "--remote-debugging-port=9222" not in args
                or "--remote-debugging-address=127.0.0.1" not in args
            ):
                raise BrowserLaunchError("workspace_browser_untrusted")
            return process
        except (psutil.Error, OSError):
            raise BrowserLaunchError("workspace_browser_untrusted") from None

    def _ready(self, receipt: BrowserReceipt) -> bool:
        process = self._process(receipt)
        try:
            listeners = [
                connection
                for connection in process.net_connections(kind="tcp")
                if connection.status == psutil.CONN_LISTEN and connection.laddr.port == 9222
            ]
            if not listeners:
                return False
            if any(connection.laddr.ip not in ("127.0.0.1", "::1") for connection in listeners):
                raise BrowserLaunchError("workspace_browser_untrusted")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(ENDPOINT + "/json/version", timeout=1) as response:
                if response.status != 200:
                    return False
                raw = response.read(8193)
                if len(raw) > 8192:
                    return False
                value = json.loads(raw)
            websocket = value.get("webSocketDebuggerUrl", "")
            return (
                isinstance(websocket, str)
                and websocket.startswith("ws://127.0.0.1:9222/devtools/browser/")
                and len(websocket) < 300
            )
        except (psutil.Error, OSError, ValueError):
            return False

    def launch(self) -> str:
        if self.profile.is_symlink() or (self.profile.exists() and not self.profile.is_dir()):
            raise BrowserLaunchError("workspace_browser_untrusted")
        try:
            receipt = self.store.load()
        except WorkspaceFailure:
            raise BrowserLaunchError("workspace_browser_untrusted") from None
        try:
            self._process(receipt)
            owned = True
        except BrowserLaunchError:
            owned = False
        if not owned:
            # Reserve before starting. Never attach to an unknown listener, never kill it.
            try:
                with socket.socket() as reserved:
                    reserved.bind(("127.0.0.1", 9222))
            except OSError:
                raise BrowserLaunchError("workspace_browser_port_busy") from None
            executable = next((path for path in installed_browsers() if path.is_file()), None)
            if executable is None:
                raise BrowserLaunchError("workspace_browser_missing")
            self.profile.mkdir(parents=True, exist_ok=True)
            try:
                child = subprocess.Popen(
                    [
                        str(executable),
                        f"--user-data-dir={self.profile}",
                        "--remote-debugging-port=9222",
                        "--remote-debugging-address=127.0.0.1",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "about:blank",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.child = child
                process = psutil.Process(child.pid)
                receipt = BrowserReceipt(
                    pid=child.pid,
                    started=process.create_time(),
                    executable=str(executable.resolve()),
                )
                self.store.save(receipt)
            except (OSError, psutil.Error, WorkspaceFailure):
                raise BrowserLaunchError("workspace_browser_failed") from None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if self._ready(receipt):
                    return ENDPOINT
            except BrowserLaunchError:
                break
            time.sleep(0.1)  # Bounded readiness polling, never a guessed UI-action delay.
        raise BrowserLaunchError("workspace_browser_failed")
