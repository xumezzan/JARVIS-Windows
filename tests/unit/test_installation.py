"""Portable installer failure/recovery checks. No provider credentials or live downloads."""

import hashlib
import io
import json
import stat
import subprocess
import sys
import urllib.request
import zipfile
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from jarvis.installation import setup
from jarvis.installation.assets import SetupError, download, extract_model


def archive(path: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name in names:
            # ZipInfo(name) would rewrite os.sep; assign to keep the literal entry name.
            entry = zipfile.ZipInfo("placeholder")
            entry.filename = name
            bundle.writestr(entry, "fixture")
    return path


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/outside",
        "model/../outside",
        "model/C:stream",
        "model/NUL",
        "model/name.",
        "model/name ",
        "model//name",
        "model/./name",
        "wrong/am/final.mdl",
        "model/a\x01",
        "C:/model/am/final.mdl",
    ],
)
def test_zip_escape_and_windows_aliases_rejected_before_writes(tmp_path: Path, name: str) -> None:
    bundle = archive(tmp_path / "asset.zip", ["model/am/final.mdl", name])
    with pytest.raises(SetupError):
        extract_model(bundle, tmp_path / "output", "model")
    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "outside").exists()


def test_backslash_entry_never_escapes_the_destination(tmp_path: Path) -> None:
    """POSIX rejects the literal alias; Windows zipfile rewrites os.sep on write/read."""
    bundle = archive(tmp_path / "asset.zip", ["model/am/final.mdl", "model\\outside"])
    try:
        extract_model(bundle, tmp_path / "output", "model")
    except SetupError:
        assert not (tmp_path / "output").exists()
    assert not (tmp_path / "outside").exists()


def test_zip_symlinks_and_case_aliases_rejected(tmp_path: Path) -> None:
    path = tmp_path / "asset.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        entry = zipfile.ZipInfo("model/link")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(entry, "../outside")
    with pytest.raises(SetupError):
        extract_model(path, tmp_path / "output", "model")
    archive(path, ["model/am/final.mdl", "model/AM/final.mdl"])
    with pytest.raises(SetupError):
        extract_model(path, tmp_path / "output", "model")


def test_model_handles_spaces_cyrillic_and_refuses_existing_target(tmp_path: Path) -> None:
    target = tmp_path / "Асаль Jarvis"
    bundle = archive(tmp_path / "asset.zip", ["model/am/final.mdl"])
    extract_model(bundle, target, "model")
    assert (target / "model/am/final.mdl").read_text() == "fixture"
    with pytest.raises(SetupError):
        extract_model(bundle, target, "model")


@pytest.mark.parametrize("failure", ["interrupt", "network", "hash", "size", "none"])
def test_download_publishes_only_complete_verified_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    content = b"official fixture"
    expected = hashlib.sha256(content).hexdigest()
    target = tmp_path / "asset.zip"
    target.write_bytes(b"old cache")
    target.with_suffix(".part").write_bytes(b"interrupted previous attempt")

    class Response(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            if failure == "interrupt":
                raise KeyboardInterrupt
            if failure == "network":
                raise OSError("SECRET must not be printed")
            return super().read(size)

    class Opener:
        def open(self, *args: Any, **kwargs: Any) -> Response:
            return Response(b"wrong" if failure == "hash" else content)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    if failure == "none":
        download("https://alphacephei.com/vosk/models/test.zip", target, expected, len(content))
        assert target.read_bytes() == content
        monkeypatch.setattr(urllib.request, "build_opener", lambda *args: pytest.fail("Network"))
        download("https://alphacephei.com/vosk/models/test.zip", target, expected, len(content))
    else:
        with pytest.raises(KeyboardInterrupt if failure == "interrupt" else SetupError) as error:
            download(
                "https://alphacephei.com/vosk/models/test.zip",
                target,
                expected,
                2 if failure == "size" else len(content),
            )
        assert "SECRET" not in str(error.value)
        assert target.read_bytes() == b"old cache"
    assert not target.with_suffix(".part").exists()


def test_no_credentials_or_download_overrides_in_installer_children() -> None:
    env = setup.clean_environment(
        {
            "LOCALAPPDATA": "C:/Users/Асаль",
            "SystemRoot": "C:/Windows",
            "OPENAI_API_KEY": "secret",
            "HTTP_PROXY": "secret",
            "PIP_EXTRA_INDEX_URL": "secret",
            "PLAYWRIGHT_DOWNLOAD_HOST": "secret",
            "SSLKEYLOGFILE": "secret",
            "PYTHONPATH": "secret",
        }
    )
    assert "secret" not in str(env)
    assert env["LOCALAPPDATA"].endswith("Асаль")


def test_inventory_detects_deleted_and_modified_components(tmp_path: Path) -> None:
    slot = tmp_path / "slot"
    slot.mkdir()
    executable = slot / "python.exe"
    executable.write_bytes(b"fixture")
    setup.atomic_json(slot / "receipt.json", {"source": "source", "files": setup.inventory(slot)})
    assert setup.healthy(slot, "source")
    assert not setup.healthy(slot, "other source")
    executable.write_bytes(b"tampered")
    assert not setup.healthy(slot, "source")
    executable.unlink()
    assert not setup.healthy(slot, "source")


@pytest.mark.parametrize("phase", ["prepare", "check", "launch", "success", "repeat"])
def test_two_slot_failure_keeps_active_app_and_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    root = tmp_path / "Jarvis Install Асаль"
    root.mkdir()
    repository = tmp_path / "repo"
    manifest = repository / "scripts/windows/assets.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"vosk":{"directory":"model"}}')
    (root / "slots/a").mkdir(parents=True)
    (root / "slots/a/preserved").write_text("old app")
    data = tmp_path / "Jarvis/memory.sqlite3"
    data.parent.mkdir()
    data.write_bytes(b"user profile sentinel")
    setup.atomic_json(root / "active.json", {"slot": "a", "model": "model"})
    monkeypatch.setattr(setup, "source_digest", lambda repo: "source")
    monkeypatch.setattr(setup, "healthy", lambda slot, fingerprint: phase == "repeat")
    called: list[str] = []

    def prepare(*args: Any) -> Path:
        called.append("prepare")
        if phase == "prepare":
            raise SetupError("network failure")
        slot = root / "slots/b"
        slot.mkdir()
        return slot

    def check(*args: Any) -> dict[str, Any]:
        called.append("check")
        if phase == "check":
            raise SetupError("missing runtime")
        return {}

    def launch(*args: Any) -> None:
        called.append("launch")
        if phase == "launch":
            raise SetupError("no visible window")

    monkeypatch.setattr(setup, "prepare_slot", prepare)
    monkeypatch.setattr(setup, "check_slot", check)
    monkeypatch.setattr(setup, "launch", launch)
    if phase in {"success", "repeat"}:
        setup.install(repository, root)
        assert setup.active_slot(root) == ("a" if phase == "repeat" else "b")
        assert ("prepare" in called) == (phase == "success")
        report = json.loads((root / "installation-report.json").read_text())
        assert report["windows_mvp_acceptance"] == "pending"
    else:
        with pytest.raises(SetupError):
            setup.install(repository, root)
        assert setup.active_slot(root) == "a"
    assert (root / "slots/a/preserved").read_text() == "old app"
    assert data.read_bytes() == b"user profile sentinel"


def test_invalid_pointer_does_not_select_or_delete_directory(tmp_path: Path) -> None:
    setup.atomic_json(tmp_path / "active.json", {"slot": "../user-data"})
    with pytest.raises(SetupError):
        setup.active_slot(tmp_path)


def test_subprocess_arguments_are_literal_and_error_is_sanitized(tmp_path: Path) -> None:
    value = 'Асаль space & $evil; "quotes"'
    # Production always supplies the UTF-8 child env; Windows text stdout adds a return.
    literal = setup.run(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", value], {"PYTHONUTF8": "1"}
    )
    assert literal.replace("\r\n", "\n") == value + "\n"
    with pytest.raises(SetupError) as error:
        setup.run([sys.executable, "-c", "print('SECRET'); raise SystemExit(1)"], {})
    assert "SECRET" not in str(error.value)


def test_subprocess_timeout_reaps_child(monkeypatch: pytest.MonkeyPatch) -> None:
    processes: list[subprocess.Popen[bytes]] = []
    original = subprocess.Popen

    def start(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", start)
    with pytest.raises(subprocess.TimeoutExpired):
        setup.run([sys.executable, "-c", "import time; time.sleep(30)"], {}, seconds=1)
    assert processes[0].poll() is not None


@pytest.mark.parametrize("kind", ["is_symlink", "is_junction"])
def test_installation_refuses_links_before_touching_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    root = tmp_path / "installation"
    root.mkdir()
    outside = tmp_path / "user data"
    outside.mkdir()
    link = root / "slots"
    link.mkdir()
    monkeypatch.setattr(Path, kind, lambda path: path == link)
    with pytest.raises(SetupError):
        setup.reject_links(root)
    assert outside.is_dir()


@pytest.mark.parametrize("relative", [False, True])
def test_setup_refuses_user_data_inside_managed_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: bool,
) -> None:
    monkeypatch.setenv(
        "JARVIS_DATA_DIR", "slots/a/data" if relative else str(tmp_path / "slots/a/data")
    )
    with pytest.raises(SetupError, match="JARVIS_DATA_DIR"):
        setup.install(tmp_path / "repository", tmp_path)
    assert not (tmp_path / "cache").exists()


def test_atomic_pointer_failure_keeps_previous_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "active.json"
    setup.atomic_json(path, {"slot": "a"})

    def fail(*args: Any, **kwargs: Any) -> None:
        raise PermissionError

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(PermissionError):
        setup.atomic_json(path, {"slot": "b"})
    assert setup.active_slot(tmp_path) == "a"


def test_download_redirects_are_not_followed() -> None:
    from jarvis.installation.assets import NoRedirect

    with pytest.raises(SetupError, match="redirect"):
        NoRedirect().redirect_request(
            urllib.request.Request("https://alphacephei.com/vosk/models/test.zip"),
            None,
            302,
            "redirect",
            Message(),
            "http://localhost/",
        )
