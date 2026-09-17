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
from jarvis.connectors.fireflies.mapping import MAPPERS as FIREFLIES_MAPPERS
from jarvis.connectors.fireflies.tools import register_fireflies
from jarvis.connectors.mcp.connector import McpConnector
from jarvis.connectors.mcp.manifest import Server, load_servers
from jarvis.connectors.mcp.tools import register_mcp
from jarvis.connectors.microsoft.calendar import CalendarConnector
from jarvis.connectors.microsoft.mapping import MAPPERS as CALENDAR_MAPPERS
from jarvis.connectors.microsoft.tools import register_calendar
from jarvis.core.context.learning import MemoryLearner
from jarvis.core.routines.proposals import SuggestionQueue
from jarvis.core.routines.runner import RoutineRunner
from jarvis.core.routines.state import RoutineState
from jarvis.core.routines.triggers import builtin
from jarvis.core.workflow.store import WorkflowStore
from jarvis.files.policy import FilePolicy
from jarvis.knowledge.harvest import GraphHarvester
from jarvis.knowledge.store import KnowledgeStore
from jarvis.mail.session import MailSession
from jarvis.memory.derived import DerivedStore
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
ROUTINE_FILE = "routines.json"
MCP_FILE = "mcp.json"


def reviewed_servers(path: Path) -> tuple[Server, ...]:
    """Only servers whose tool set the owner passed through exist for the planner.

    An unreviewed server, a server that changed its tools, and a file that cannot be
    read all lead to the same place: no tools. The panel tells the owner why; the
    session simply does not gain a surface nobody approved.
    """
    try:
        return tuple(server for server in load_servers(path) if server.current)
    except ValueError:
        return ()


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
    harvester: GraphHarvester
    derived: DerivedStore
    learner: MemoryLearner
    workflows: WorkflowStore
    routines: RoutineRunner
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
    # What the owner's own finished runs taught, kept apart from the labels they typed.
    derived = DerivedStore(config.data_dir / "derived.sqlite3")
    workflows = WorkflowStore(config.data_dir / "workflows.sqlite3")
    # Each connector says how to read its own answers; the harvester only applies them.
    harvester = GraphHarvester(knowledge, {**CALENDAR_MAPPERS, **FIREFLIES_MAPPERS})
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
    for server in reviewed_servers(Path(config.data_dir) / MCP_FILE):
        try:
            mcp = McpConnector(server)
            register_mcp(registry, mcp, matrix)
        except ValueError:
            continue  # One unusable server must not cost the session the others.
        connectors.add(mcp)
    audit = AuditLog(config.data_dir / "audit.sqlite3")
    approvals = ApprovalStore()
    engine = PermissionEngine(registry, approvals, audit)
    # Background work is composed here like everything else, and starts switched off. The
    # runner is given the registry, the engine and the journal - never approval authority.
    routines = RoutineRunner(
        registry,
        engine,
        workflows,
        RoutineState(Path(config.data_dir) / ROUTINE_FILE),
        SuggestionQueue(),
        builtin(files.roots[0] if files.roots else None),
    )
    return Workbench(
        registry=registry,
        engine=engine,
        authority=approvals.take_authority(audit.approved),
        audit=audit,
        matrix=matrix,
        connectors=connectors,
        knowledge=knowledge,
        harvester=harvester,
        derived=derived,
        learner=MemoryLearner(derived),
        workflows=workflows,
        routines=routines,
        browser_host=host,
        files=files,
        mail_session=session,
        outbox=outbox,
    )
