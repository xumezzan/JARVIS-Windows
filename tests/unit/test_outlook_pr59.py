"""Exact external effects, fixtures only, including uncertain POST and account changes."""

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

import pytest
import pytest_asyncio
from keyring.backend import KeyringBackend
from tests.mail_support import FakeCredentials, FakeGraph

from jarvis.mail.credentials import MailFailure
from jarvis.mail.models import Message
from jarvis.mail.session import MailSession
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalAuthority, ApprovalStore
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.policies import Mode
from jarvis.tools.base import ExecutionContext
from jarvis.tools.outlook import register_outlook
from jarvis.tools.registry import ToolRegistry


@dataclass
class MailHarness:
    session: MailSession
    graph: FakeGraph
    credentials: FakeCredentials
    registry: ToolRegistry
    engine: PermissionEngine
    authority: ApprovalAuthority
    approvals: ApprovalStore
    audit: AuditLog
    clock: list[float]

    def arguments(self) -> dict[str, object]:
        assert self.session.account is not None
        return {
            "account": self.session.account.model_dump(),
            "message": Message(
                to=["recipient@example.test"], subject="Exact subject", body="Exact body"
            ).model_dump(),
        }

    def prepare(
        self,
        tool: str = "outlook.send",
        args: dict[str, object] | None = None,
        mode: Mode = Mode.EXECUTE,
    ) -> Action:
        result = self.engine.prepare(tool, args if args is not None else self.arguments(), mode)
        assert isinstance(result, Action), result
        return result


@pytest_asyncio.fixture
async def mail(tmp_path: Path) -> Any:
    credentials, graph = FakeCredentials(), FakeGraph()
    session = MailSession(credentials, graph)
    await session.connect("00000000-0000-0000-0000-000000000001", ExecutionContext(Event()))
    credentials.calls.clear()
    graph.calls.clear()
    registry = ToolRegistry()
    register_outlook(registry, session)
    clock = [0.0]
    approvals = ApprovalStore(clock=lambda: clock[0])
    audit = AuditLog(tmp_path / "audit.sqlite3")
    authority = approvals.take_authority(audit.approved)
    engine = PermissionEngine(registry, approvals, audit)
    yield MailHarness(
        session, graph, credentials, registry, engine, authority, approvals, audit, clock
    )
    session.detach()
    audit.close()


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}
        self.fail = False

    def get_password(self, servicename: str, username: str) -> str | None:
        return self.data.get((servicename, username))

    def set_password(self, servicename: str, username: str, password: str) -> None:
        if self.fail and username == "1":
            raise OSError("fixture store failure")
        assert len(password.encode("utf-16-le")) <= 2560
        self.data[servicename, username] = password

    def delete_password(self, servicename: str, username: str) -> None:
        self.data.pop((servicename, username), None)


@pytest.mark.asyncio
async def test_attachment_helper_regular_size_and_symlink(tmp_path: Path) -> None:
    from jarvis.mail.attachments import read_attachment

    file = tmp_path / "attachment.txt"
    file.write_bytes(b"exact test data")
    assert await read_attachment(str(file)) == b"exact test data"
    alias = tmp_path / "alias.txt"
    alias.symlink_to(file)
    with pytest.raises(MailFailure):
        await read_attachment(str(alias))
    file.write_bytes(b"x" * 32769)
    with pytest.raises(MailFailure):
        await read_attachment(str(file))
    with pytest.raises(MailFailure):
        await read_attachment(str(tmp_path))
