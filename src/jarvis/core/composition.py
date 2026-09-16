"""The single place where tools, connectors, permissions and audit are wired together.

Assembly used to live inside the planner window. It moves here because it is not a user
interface concern: as services are added, one composition root decides what exists in a
session, and the UI receives a finished workbench. Nothing here draws, and nothing here
holds a credential — connectors obtain their own through the OS credential store.

Approval authority is taken exactly once and handed to the caller, so the UI remains the
only issuer of an approval token.
"""

from dataclasses import dataclass
from pathlib import Path

from jarvis.browser.host import BrowserHost
from jarvis.config import AppConfig
from jarvis.connectors.base import ConnectorRegistry
from jarvis.connectors.fireflies.connector import FirefliesConnector
from jarvis.connectors.fireflies.tools import register_fireflies
from jarvis.connectors.microsoft.calendar import CalendarConnector
from jarvis.connectors.microsoft.tools import register_calendar
from jarvis.core.workflow.store import WorkflowStore
from jarvis.files.policy import FilePolicy
from jarvis.knowledge.store import KnowledgeStore
from jarvis.mail.session import MailSession
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import ApprovalAuthority, ApprovalStore
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.matrix import PermissionMatrix, load_matrix
from jarvis.platforms.files import LocalFiles
from jarvis.platforms.windows.transport import ProcessBackend
from jarvis.security.browser_policy import NetworkPolicy
from jarvis.tools.browser import register_browser
from jarvis.tools.files import register_files
from jarvis.tools.local import LocalOutbox, local_registry
from jarvis.tools.outlook import register_outlook
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import WindowsBackend, register_windows

MATRIX_FILE = "permissions.json"


@dataclass(frozen=True)
class Workbench:
    """One session's executable surface. The registry is sealed by the engine."""

    registry: ToolRegistry
    engine: PermissionEngine
    authority: ApprovalAuthority
    audit: AuditLog
    matrix: PermissionMatrix
    connectors: ConnectorRegistry
    knowledge: KnowledgeStore
    workflows: WorkflowStore
    browser_host: BrowserHost
    files: FilePolicy
    mail_session: MailSession
    outbox: LocalOutbox

    def shutdown(self) -> None:
        """Release the browser first, then the audit log, so the last events are written."""
        try:
            self.browser_host.shutdown()
        finally:
            self.audit.close()


def build(
    config: AppConfig,
    *,
    browser_host: BrowserHost | None = None,
    windows_backend: WindowsBackend | None = None,
    mail_session: MailSession | None = None,
) -> Workbench:
    """Compose a session. Every caller gets the same surface, in the same order."""
    matrix = load_matrix(Path(config.data_dir) / MATRIX_FILE)
    connectors = ConnectorRegistry()
    knowledge = KnowledgeStore(config.data_dir / "knowledge.sqlite3")
    workflows = WorkflowStore(config.data_dir / "workflows.sqlite3")
    host = browser_host or BrowserHost(NetworkPolicy(config.browser_origins))
    registry, outbox = local_registry()
    register_browser(registry, host, host.policy)
    register_windows(registry, windows_backend or ProcessBackend())
    files = FilePolicy(config.file_roots)
    register_files(registry, files, LocalFiles())
    session = mail_session or MailSession()
    register_outlook(registry, session)
    calendar = CalendarConnector(session)
    register_calendar(registry, calendar, matrix)
    connectors.add(calendar)
    fireflies = FirefliesConnector()
    register_fireflies(registry, fireflies, matrix)
    connectors.add(fireflies)
    audit = AuditLog(config.data_dir / "audit.sqlite3")
    approvals = ApprovalStore()
    engine = PermissionEngine(registry, approvals, audit)
    return Workbench(
        registry=registry,
        engine=engine,
        authority=approvals.take_authority(audit.approved),
        audit=audit,
        matrix=matrix,
        connectors=connectors,
        knowledge=knowledge,
        workflows=workflows,
        browser_host=host,
        files=files,
        mail_session=session,
        outbox=outbox,
    )
