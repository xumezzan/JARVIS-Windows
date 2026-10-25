"""File policy and tools: what is reachable, what is refused, and what never leaves the disk."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from jarvis.files.policy import FilePolicy
from jarvis.observability.audit import AuditLog, ErrorCode
from jarvis.permissions.approvals import ApprovalAuthority, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.tools.base import ToolError
from jarvis.tools.files import register_files
from jarvis.tools.local import local_registry


class Recorder:
    """A backend that records the two effects the operating system owns."""

    def __init__(self) -> None:
        self.recycled: list[Path] = []
        self.opened: list[Path] = []

    async def recycle(self, path: Path) -> None:
        self.recycled.append(path)
        path.unlink()

    async def open_with_default_app(self, path: Path) -> None:
        self.opened.append(path)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    folder = tmp_path / "Documents"
    folder.mkdir()
    (folder / "отчёт.txt").write_text("первая строка", encoding="utf-8")
    (folder / "notes.md").write_text("# заметки", encoding="utf-8")
    (folder / "снимок.png").write_bytes(b"\x89PNG\r\n")
    nested = folder / "проект"
    nested.mkdir()
    (nested / "отчёт итог.txt").write_text("вложенный", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("не для ассистента", encoding="utf-8")
    return folder


@pytest.fixture
def policy(home: Path) -> FilePolicy:
    return FilePolicy((str(home),))


@dataclass
class Session:
    """One engine, one backend recorder and the single approval issuer the store hands out."""

    engine: PermissionEngine
    files: Recorder
    authority: ApprovalAuthority

    async def run(self, tool: str, arguments: dict[str, object]) -> Outcome:
        action = self.engine.prepare(tool, arguments, Mode.EXECUTE)
        if isinstance(action, Outcome):
            return action
        token = self.authority.approve(action) if action.risk is Risk.CONFIRM else None
        return await self.engine.execute(action, token)


@pytest.fixture
def session(policy: FilePolicy, tmp_path: Path) -> Iterator[Session]:
    registry, _ = local_registry()
    recorder = Recorder()
    register_files(registry, policy, recorder)
    audit = AuditLog(tmp_path / "audit.sqlite3")
    store = ApprovalStore()
    engine = PermissionEngine(registry, store, audit)
    yield Session(engine, recorder, store.take_authority(audit.approved))
    audit.close()


def test_only_allowed_folders_are_reachable(policy: FilePolicy, home: Path, tmp_path: Path) -> None:
    assert policy.check(str(home / "отчёт.txt")) == home / "отчёт.txt"
    for denied in (
        str(tmp_path / "secret.txt"),
        str(home / ".." / "secret.txt"),
        "relative.txt",
        "",
        str(home / "x\x00y"),
    ):
        with pytest.raises(ToolError, match="path_denied"):
            policy.check(denied)


def test_programs_are_never_written_or_opened(policy: FilePolicy, home: Path) -> None:
    for name in ("run.exe", "install.bat", "script.ps1", "shortcut.lnk", "code.js"):
        with pytest.raises(ToolError, match="file_unsupported"):
            policy.writable(policy.check(str(home / name)))
        with pytest.raises(ToolError, match="file_unsupported"):
            policy.openable(policy.check(str(home / name)))
    # An ordinary document may be opened, but only plain text may be read or written as text.
    assert policy.openable(policy.check(str(home / "снимок.png")))
    with pytest.raises(ToolError, match="file_unsupported"):
        policy.readable_text(policy.check(str(home / "снимок.png")))


def test_an_empty_policy_denies_everything(home: Path) -> None:
    with pytest.raises(ToolError, match="path_denied"):
        FilePolicy(()).check(str(home / "отчёт.txt"))


@pytest.mark.asyncio
async def test_finding_and_reading_stay_inside_the_policy(session: Session, home: Path) -> None:
    found = await session.run("files.find", {"query": "отчёт"})
    assert found.status is Status.SUCCESS and found.result_json is not None
    assert "отчёт.txt" in found.result_json and "отчёт итог.txt" in found.result_json
    assert "secret.txt" not in found.result_json

    content = await session.run("files.read_text", {"path": str(home / "отчёт.txt")})
    assert content.status is Status.SUCCESS and content.result_json is not None
    assert "первая строка" in content.result_json

    # The folder policy is decided while the action is prepared, so a path outside the
    # allowed folders never becomes an action at all.
    outside = await session.run("files.read_text", {"path": str(home.parent / "secret.txt")})
    assert outside.status is Status.INVALID and outside.error is ErrorCode.INVALID_REQUEST


@pytest.mark.asyncio
async def test_writing_keeps_existing_content_unless_replacement_is_asked(
    session: Session, home: Path
) -> None:
    target = home / "новый.txt"
    created = await session.run("files.write_text", {"path": str(target), "text": "привет"})
    assert created.status is Status.SUCCESS
    assert target.read_text(encoding="utf-8") == "привет"

    again = await session.run("files.write_text", {"path": str(target), "text": "другое"})
    assert again.status is Status.ERROR and again.error is ErrorCode.FILE_CONFLICT
    assert target.read_text(encoding="utf-8") == "привет"

    replaced = await session.run(
        "files.write_text", {"path": str(target), "text": "другое", "overwrite": True}
    )
    assert replaced.status is Status.SUCCESS
    assert target.read_text(encoding="utf-8") == "другое"
    assert not list(home.glob("*.jarvis-part"))


@pytest.mark.asyncio
async def test_a_program_is_never_created(session: Session, home: Path) -> None:
    result = await session.run(
        "files.write_text", {"path": str(home / "payload.bat"), "text": "echo"}
    )
    assert result.status is Status.INVALID
    assert not (home / "payload.bat").exists()


@pytest.mark.asyncio
async def test_renaming_stays_a_name_and_refuses_collisions(session: Session, home: Path) -> None:
    escape = await session.run(
        "files.rename", {"path": str(home / "notes.md"), "new_name": "../notes.md"}
    )
    assert escape.status is Status.INVALID
    assert (home / "notes.md").exists()

    collision = await session.run(
        "files.rename", {"path": str(home / "notes.md"), "new_name": "отчёт.txt"}
    )
    assert collision.status is Status.ERROR and collision.error is ErrorCode.FILE_CONFLICT

    moved = await session.run(
        "files.rename", {"path": str(home / "notes.md"), "new_name": "заметки.md"}
    )
    assert moved.status is Status.SUCCESS
    assert (home / "заметки.md").exists() and not (home / "notes.md").exists()


@pytest.mark.asyncio
async def test_deleting_only_goes_to_the_recycle_bin(session: Session, home: Path) -> None:
    result = await session.run("files.recycle", {"path": str(home / "отчёт.txt")})
    assert result.status is Status.SUCCESS
    assert session.files.recycled == [home / "отчёт.txt"]

    # A whole folder is not removed by one command: the precondition refuses it.
    folder = await session.run("files.recycle", {"path": str(home / "проект")})
    assert folder.status is Status.DENIED and folder.error is ErrorCode.PRECONDITION
    assert (home / "проект").is_dir()


@pytest.mark.asyncio
async def test_opening_hands_a_document_to_its_own_application(
    session: Session, home: Path
) -> None:
    result = await session.run("files.open", {"path": str(home / "снимок.png")})
    assert result.status is Status.SUCCESS and session.files.opened == [home / "снимок.png"]

    (home / "run.exe").write_bytes(b"MZ")
    program = await session.run("files.open", {"path": str(home / "run.exe")})
    assert program.status is Status.INVALID
    assert session.files.opened == [home / "снимок.png"]


@pytest.mark.asyncio
async def test_simulation_decides_the_path_without_running_anything(
    session: Session, home: Path
) -> None:
    denied = session.engine.prepare(
        "files.write_text",
        {"path": str(home.parent / "secret.txt"), "text": "x"},
        Mode.SIMULATION,
    )
    assert isinstance(denied, Outcome) and denied.status is Status.INVALID

    action = session.engine.prepare(
        "files.recycle", {"path": str(home / "отчёт.txt")}, Mode.SIMULATION
    )
    assert not isinstance(action, Outcome)
    outcome = await session.engine.execute(action, None)
    assert outcome.status is Status.DENIED  # CONFIRM without a token never runs.
    assert (home / "отчёт.txt").exists() and not session.files.recycled


@pytest.mark.asyncio
async def test_listing_reports_a_folder_without_following_it_outside(
    session: Session, home: Path
) -> None:
    listing = await session.run("files.list_folder", {"folder": str(home)})
    assert listing.status is Status.SUCCESS and listing.result_json is not None
    assert "проект" in listing.result_json and "secret.txt" not in listing.result_json

    outside = await session.run("files.list_folder", {"folder": str(home.parent)})
    assert outside.status is Status.INVALID
