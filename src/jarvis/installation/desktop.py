"""Prepare prebuilt Electron in an inactive slot; never install Node or run npm there."""

import json
import os
import re
import shutil
import ssl
import stat
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from jarvis.installation.assets import SetupError, digest


def checked_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(
            not part
            or part in (".", "..")
            or part.endswith((".", " "))
            or re.search(r'[\\:<>"|?*\x00-\x1f]', part)
            or re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", part)
            for part in value.rstrip("/").split("/")
        )
    ):
        raise SetupError("Unsafe desktop asset path.")
    return path


def verify_prebuilt(repository: Path) -> Path:
    assets = repository / "src" / "jarvis" / "desktop_assets"
    try:
        manifest_path = assets / "manifest.json"
        if manifest_path.stat().st_size > 131072:
            raise ValueError
        manifest = json.loads(manifest_path.read_bytes())
        for section, base in (("sources", repository / "desktop"), ("files", assets)):
            entries = manifest[section]
            if not isinstance(entries, dict) or not 1 <= len(entries) <= 1000:
                raise ValueError
            for name, expected in entries.items():
                relative = checked_relative(name)
                source = base.joinpath(*relative.parts)
                if source.is_symlink() or any(
                    p.is_symlink() for p in source.parents if p != base.parent
                ):
                    raise ValueError
                if not source.is_file() or digest(source) != expected:
                    raise ValueError
        if not (assets / "dist" / "index.html").is_file():
            raise ValueError
        return assets
    except (OSError, ValueError, KeyError, TypeError):
        raise SetupError(
            "Prebuilt Electron UI is missing or stale. Rebuild desktop before publishing."
        ) from None


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        self.count = 0

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        self.count += 1
        parsed = urlsplit(newurl)
        if (
            self.count > 3
            or parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.hostname
            not in {
                "github.com",
                "release-assets.githubusercontent.com",
                "objects.githubusercontent.com",
            }
        ):
            raise SetupError("Electron asset redirected outside the official release hosts.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_electron(asset: dict[str, Any], target: Path) -> None:
    version = asset.get("version", "")
    expected = asset.get("sha256", "")
    size = asset.get("size", 0)
    if (
        not isinstance(version, str)
        or not re.fullmatch(r"\d+\.\d+\.\d+", version)
        or not isinstance(expected, str)
        or not re.fullmatch(r"[a-f0-9]{64}", expected)
        or not isinstance(size, int)
        or not 0 < size < 300_000_000
    ):
        raise SetupError("Invalid Electron release manifest.")
    url = f"https://github.com/electron/electron/releases/download/v{version}/electron-v{version}-win32-x64.zip"
    if asset.get("url") != url:
        raise SetupError("Electron must come from the reviewed official release.")
    if target.is_file() and target.stat().st_size == size and digest(target) == expected:
        return
    temporary = target.with_suffix(".part")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        ReleaseRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    deadline = time.monotonic() + 300
    try:
        temporary.unlink(missing_ok=True)
        with opener.open(url, timeout=30) as response, temporary.open("xb") as output:
            count = 0
            while block := response.read(65536):
                count += len(block)
                if count > size or time.monotonic() > deadline:
                    raise SetupError("Electron download exceeded its limit.")
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if temporary.stat().st_size != size or digest(temporary) != expected:
            raise SetupError("Electron integrity check failed.")
        temporary.replace(target)
    except OSError:
        raise SetupError(
            "Electron download failed. Check network and rerun installation."
        ) from None
    finally:
        temporary.unlink(missing_ok=True)


def extract_electron(archive: Path, destination: Path) -> None:
    if destination.exists():
        raise SetupError("Electron needs an unused inactive-slot directory.")
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if (
                not entries
                or len(entries) > 50000
                or sum(e.file_size for e in entries) > 2_000_000_000
            ):
                raise SetupError("Electron archive exceeds extraction limits.")
            names: set[str] = set()
            for entry in entries:
                relative = checked_relative(entry.filename)
                key = relative.as_posix().casefold()
                if (
                    key in names
                    or entry.flag_bits & 1
                    or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFDIR, stat.S_IFREG)
                ):
                    raise SetupError("Unsafe Electron archive.")
                names.add(key)
            destination.mkdir(parents=True)
            for entry in entries:
                output = destination.joinpath(*checked_relative(entry.filename).parts)
                if entry.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(entry) as source, output.open("xb") as target:
                        shutil.copyfileobj(source, target, length=65536)
    except (OSError, zipfile.BadZipFile):
        raise SetupError("Electron extraction failed.") from None
    if not (destination / "electron.exe").is_file():
        raise SetupError("Electron executable is missing.")


def prepare_desktop(slot: Path, repository: Path, root: Path) -> None:
    assets = verify_prebuilt(repository)
    try:
        manifest = json.loads((repository / "scripts/windows/assets.json").read_bytes())
        archive = root / "cache" / "electron-win32-x64.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        download_electron(manifest["electron"], archive)
        destination = slot / "desktop"
        extract_electron(archive, destination)
        app = destination / "resources" / "app"
        shutil.copytree(assets, app)
        (app / "python-runtime.json").write_text(
            json.dumps(
                {
                    "python": str(slot / "venv" / "Scripts" / "python.exe"),
                    "root": str(slot),
                }
            ),
            encoding="utf-8",
        )
    except (OSError, KeyError, ValueError):
        raise SetupError(
            "Electron preparation failed; the previous active slot is preserved."
        ) from None
