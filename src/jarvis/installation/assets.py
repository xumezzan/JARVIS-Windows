"""Bounded HTTPS downloads and fail-closed archive extraction for repository setup."""

import hashlib
import os
import re
import shutil
import ssl
import stat
import time
import urllib.request
import zipfile
from email.message import Message
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn


class SetupError(Exception):
    """Only fixed, actionable diagnostics; never include child output or credentials."""


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> NoReturn:
        raise SetupError("Asset redirect refused. Review the official source manifest.")


def download(url: str, target: Path, sha256: str, size: int) -> None:
    """Publish only exact manifest bytes; offline repeat uses a verified cache."""
    if not url.startswith("https://alphacephei.com/vosk/models/") or not re.fullmatch(
        r"[0-9a-f]{64}", sha256
    ):
        raise SetupError("Invalid official asset manifest.")
    if not 0 < size <= 100_000_000:
        raise SetupError("Invalid asset size.")
    if target.is_file() and target.stat().st_size == size and digest(target) == sha256:
        return
    partial = target.with_suffix(".part")
    partial.unlink(missing_ok=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_default_certs()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        NoRedirect(),
        urllib.request.HTTPSHandler(context=context),
    )
    deadline = time.monotonic() + 300
    try:
        with opener.open(url, timeout=30) as response, partial.open("xb") as output:
            count = 0
            while chunk := response.read(65536):
                count += len(chunk)
                if count > size or time.monotonic() > deadline:
                    raise SetupError("Asset download exceeded its size or time limit. Rerun setup.")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if partial.stat().st_size != size or digest(partial) != sha256:
            raise SetupError("Asset integrity check failed. Rerun setup; do not use this download.")
        partial.replace(target)
    except OSError:
        raise SetupError(
            "Asset download failed. Check network, disk space and permissions; rerun."
        ) from None
    finally:
        partial.unlink(missing_ok=True)


def extract_model(archive: Path, destination: Path, directory: str) -> None:
    """Validate all entries before writing; reject Windows path aliases and zip bombs."""
    if destination.exists():
        raise SetupError("Model destination must be an unused setup directory.")
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if (
                not entries
                or len(entries) > 10000
                or sum(i.file_size for i in entries) > 500_000_000
            ):
                raise SetupError("Model archive exceeds extraction limits.")
            seen: set[str] = set()
            for item in entries:
                path = PurePosixPath(item.filename)
                parts = item.filename.rstrip("/").split("/")
                invalid = any(
                    not p
                    or p in {".", ".."}
                    or p.endswith((".", " "))
                    or re.search(r'[\\:<>"|?*\x00-\x1f]', p)
                    or re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", p)
                    for p in parts
                )
                kind = stat.S_IFMT(item.external_attr >> 16)
                key = str(path).casefold()
                if (
                    invalid
                    or path.is_absolute()
                    or parts[0] != directory
                    or key in seen
                    or kind not in {0, stat.S_IFREG, stat.S_IFDIR}
                    or item.flag_bits & 1
                ):
                    raise SetupError("Unsafe model archive. Review the official asset.")
                seen.add(key)
            destination.mkdir(parents=True)
            for item in entries:
                target = destination.joinpath(*PurePosixPath(item.filename).parts)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(item) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, length=65536)
        if not (destination / directory / "am" / "final.mdl").is_file():
            raise SetupError("Russian model is incomplete.")
    except (OSError, zipfile.BadZipFile):
        raise SetupError(
            "Model extraction failed. Check archive, disk space and permissions."
        ) from None
