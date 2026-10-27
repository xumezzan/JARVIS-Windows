"""Bounded installer exercises with synthetic data and disposable storage only.

These checks never create approval authority, read credentials, contact providers,
or inspect user memory. Native credential construction proves availability only,
not whether an account, credential, or operating-system store actually works.
"""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Literal, NoReturn

from keyring.backend import KeyringBackend

from jarvis.core.planner.contracts import Limits
from jarvis.core.planner.offline import OfflineProvider
from jarvis.core.planner.runner import Runner
from jarvis.mail.models import Account, MailResult, Message
from jarvis.mail.session import MailSession
from jarvis.memory.models import PROFILE_TTL, Entry
from jarvis.memory.store import MemoryStore
from jarvis.observability.audit import AuditKind, AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalStore, ApprovalToken
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.tools.base import ExecutionContext, ToolSpec
from jarvis.tools.browser import BrowserCommand, register_browser
from jarvis.tools.local import CheckInput, CheckResult, local_registry
from jarvis.tools.outlook import register_outlook
from jarvis.tools.registry import ToolRegistry


def _require(condition: bool) -> None:
    if not condition:
        raise RuntimeError("offline_check_failed")


def _action(value: Action | Outcome) -> Action:
    if not isinstance(value, Action):
        raise RuntimeError("offline_check_failed")
    return value


def _verified(outcome: Outcome) -> bool:
    return (
        outcome.status is Status.SUCCESS
        and outcome.error is ErrorCode.NONE
        and json.loads(outcome.result_json or "null") == {"completed": True}
    )


async def _permissions(path: Path) -> None:
    registry, outbox = local_registry()
    hooks: list[str] = []
    policy_calls: list[str] = []

    async def preconditions(args: CheckInput, context: ExecutionContext) -> bool:
        hooks.append("preconditions")
        await context.checkpoint()
        return True

    async def execute(args: CheckInput, context: ExecutionContext) -> CheckResult:
        hooks.append("execute")
        await context.checkpoint()
        return CheckResult(completed=True)

    async def verify(args: CheckInput, result: CheckResult, context: ExecutionContext) -> bool:
        hooks.append("verify")
        await context.checkpoint()
        return result.completed

    def policy(args: CheckInput) -> None:
        policy_calls.append("policy")

    registry.register(
        ToolSpec(
            "diagnostic.check",
            "Disposable installer check without external effects.",
            Risk.SAFE,
            CheckInput,
            CheckResult,
            preconditions,
            execute,
            verify,
            timeout_seconds=1,
            policy=policy,
        )
    )
    audit = AuditLog(path)
    engine = PermissionEngine(registry, ApprovalStore(), audit)
    expected: dict[str, Status] = {}
    safe_id = ""
    before_close: list[str] = []
    try:
        safe = _action(engine.prepare("local.check", {"delay_ms": 0}, Mode.EXECUTE))
        safe_id = str(safe.request_id)
        _require(_verified(await engine.execute(safe)))
        expected[safe_id] = Status.SUCCESS

        simulation = _action(engine.prepare("diagnostic.check", {}, Mode.SIMULATION))
        simulated = await engine.execute(simulation)
        _require(simulated.status is Status.SIMULATED and not simulated.may_have_effects)
        _require(not hooks and policy_calls == ["policy"])
        expected[str(simulation.request_id)] = Status.SIMULATED

        confirmation = _action(
            engine.prepare(
                "local.append_message",
                {"recipient": "installer-fixture", "body": "Installer diagnostic message"},
                Mode.EXECUTE,
            )
        )
        denied = await engine.execute(confirmation)
        _require(
            denied.status is Status.DENIED
            and denied.error is ErrorCode.APPROVAL
            and not denied.may_have_effects
            and outbox.count == 0
        )
        expected[str(confirmation.request_id)] = Status.DENIED

        for name in ("local.critical_test", "local.blocked_test"):
            blocked = engine.prepare(name, {}, Mode.EXECUTE)
            _require(isinstance(blocked, Outcome))
            if isinstance(blocked, Outcome):
                _require(
                    blocked.status is Status.DENIED
                    and blocked.error is ErrorCode.POLICY
                    and not blocked.may_have_effects
                )
                expected[str(blocked.request_id)] = Status.DENIED

        cancelled = _action(engine.prepare("diagnostic.check", {}, Mode.EXECUTE))
        engine.cancel(cancelled)
        cancellation = await engine.execute(cancelled)
        _require(
            cancellation.status is Status.CANCELLED
            and cancellation.error is ErrorCode.CANCELLED
            and not cancellation.may_have_effects
            and not hooks
        )
        expected[str(cancelled.request_id)] = Status.CANCELLED
        before_close = audit.recent()
    finally:
        engine.cancel_all()
        audit.close()

    reopened = AuditLog(path)
    try:
        records = reopened.recent()
        _require(records == before_close)
        events = [json.loads(record) for record in records]
        finished = {
            event["request_id"]: event["status"]
            for event in events
            if event["event"] == AuditKind.FINISHED.value
        }
        _require(finished == {request_id: status.value for request_id, status in expected.items()})
        _require(all(event["arguments"] == "[REDACTED]" for event in events))
        _require(
            all(
                event["request_id"] == safe_id
                for event in events
                if event["event"] == AuditKind.STARTED.value
            )
        )
        safe_events = [
            event["event"] for event in reversed(events) if event["request_id"] == safe_id
        ]
        _require(
            safe_events
            == [AuditKind.PREPARED.value, AuditKind.STARTED.value, AuditKind.FINISHED.value]
        )
    finally:
        reopened.close()


def check_permissions() -> None:
    """Exercise actual permission decisions, simulation, cancellation and durable audit."""
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-permissions-check-") as directory:
            asyncio.run(_permissions(Path(directory) / "audit.sqlite3"))
    except Exception:
        raise RuntimeError("offline_permissions_failed") from None


async def _planner(path: Path) -> None:
    registry, outbox = local_registry()
    audit = AuditLog(path)
    engine = PermissionEngine(registry, ApprovalStore(), audit)

    async def approve(action: Action) -> ApprovalToken | None:
        raise RuntimeError("offline_check_failed")

    async def clarify(question: str) -> str | None:
        raise RuntimeError("offline_check_failed")

    try:
        runner = Runner(
            registry,
            engine,
            OfflineProvider(),
            approve,
            clarify,
            limits=Limits(max_steps=2, max_questions=0, provider_seconds=1, total_seconds=5),
        )
        result = await runner.run("проверь систему дважды", Mode.EXECUTE)
        _require(result.status == "finished" and len(result.steps) == 2)
        _require(
            all(step.tool == "local.check" and _verified(step.outcome) for step in result.steps)
        )
        _require(outbox.count == 0 and runner.active is None)
    finally:
        engine.cancel_all()
        audit.close()


def check_planner() -> None:
    """Run the offline two-step recipe through the real Runner and PermissionEngine."""
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-planner-check-") as directory:
            asyncio.run(_planner(Path(directory) / "audit.sqlite3"))
    except Exception:
        raise RuntimeError("offline_planner_failed") from None


def check_memory() -> None:
    """Save, reopen, edit, delete and clear synthetic labels in a disposable database."""
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-check-") as directory:
            path = Path(directory) / "memory.sqlite3"
            now = 100000
            first = Entry(
                kind="project",
                label="Diagnostic project",
                value="Alpha",
                updated=now,
                expires=now + PROFILE_TTL,
            )
            store = MemoryStore(path, clock=lambda: now)
            _require(store.save(first) == (first,))
            reopened = MemoryStore(path, clock=lambda: now)
            _require(reopened.read() == (first,))
            edited = Entry.model_validate(first.model_dump() | {"value": "Beta"})
            _require(reopened.save(edited, first) == (edited,))
            _require(store.read() == (edited,))
            _require(reopened.delete(edited) == () and store.read() == ())
            _require(store.save(first) == (first,))
            _require(store.clear() == () and reopened.read() == ())
    except Exception:
        raise RuntimeError("offline_memory_failed") from None


class _NoBrowserEffects:
    called = False

    async def call(self, command: BrowserCommand, context: ExecutionContext) -> NoReturn:
        self.called = True
        raise RuntimeError("offline_check_failed")


async def _browser_policy(path: Path) -> None:
    registry = ToolRegistry()
    backend = _NoBrowserEffects()
    register_browser(registry, backend)
    audit = AuditLog(path)
    engine = PermissionEngine(registry, ApprovalStore(), audit)
    target = {
        "tab_id": "0" * 32,
        "document_id": "1" * 32,
        "url": "https://example.com/",
        "origin": "https://example.com",
        "fingerprint": "2" * 64,
    }
    try:
        valid_inputs: tuple[tuple[str, object], ...] = (
            ("browser.get_tabs", {}),
            ("browser.read", {"target": target}),
            ("browser.open", {"url": "https://example.com/"}),
        )
        for name, parameters in valid_inputs:
            action = _action(engine.prepare(name, parameters, Mode.SIMULATION))
            _require(action.risk is Risk.SAFE)
            simulated = await engine.execute(action)
            _require(
                simulated.status is Status.SIMULATED
                and simulated.error is ErrorCode.NONE
                and not simulated.may_have_effects
            )

        sending = _action(
            engine.prepare(
                "browser.click",
                {
                    "target": target,
                    "element": {
                        "role": "button",
                        "name": "Send",
                        "fingerprint": "3" * 64,
                        "request": {"url": "https://example.com/submit"},
                    },
                },
                Mode.SIMULATION,
            )
        )
        _require(sending.risk is Risk.CONFIRM)
        denied = await engine.execute(sending)
        _require(
            denied.status is Status.DENIED
            and denied.error is ErrorCode.APPROVAL
            and not denied.may_have_effects
        )

        invalid_inputs: tuple[tuple[str, object], ...] = (
            ("browser.open", {"url": "http://127.0.0.1/"}),
            ("browser.open", {"url": "file:///installer-fixture"}),
            ("browser.read", {"target": target, "selector": "body"}),
            (
                "browser.click",
                {
                    "target": target,
                    "element": {
                        "role": "button",
                        "name": "Diagnostic submit",
                        "fingerprint": "3" * 64,
                        "request": {
                            "url": "https://example.com/submit",
                            "method": "POST",
                            "body": "fixture=value",
                        },
                    },
                },
            ),
        )
        for name, parameters in invalid_inputs:
            rejected = engine.prepare(name, parameters, Mode.SIMULATION)
            network_rejection = name != "browser.read"
            _require(
                isinstance(rejected, Outcome)
                and rejected.status is (Status.DENIED if network_rejection else Status.INVALID)
                and rejected.error
                is (ErrorCode.NETWORK_DENIED if network_rejection else ErrorCode.INVALID_REQUEST)
                and not rejected.may_have_effects
            )
        _require(not backend.called)
    finally:
        engine.cancel_all()
        audit.close()


def check_browser_policy() -> None:
    """Exercise pure browser policy and SAFE simulation without a browser or network."""
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-browser-policy-check-") as directory:
            asyncio.run(_browser_policy(Path(directory) / "audit.sqlite3"))
    except Exception:
        raise RuntimeError("offline_browser_policy_failed") from None


class _NoMailCredentials:
    called = False

    async def call(
        self,
        operation: Literal["connect", "silent", "consent", "disconnect"],
        client_id: str,
        home_id: str = "",
        surface: str = "",
    ) -> NoReturn:
        self.called = True
        raise RuntimeError("offline_check_failed")


class _NoMailTransport:
    called = False

    async def request(
        self,
        token: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> NoReturn:
        self.called = True
        raise RuntimeError("offline_check_failed")


def _empty_mail_session(session: MailSession) -> bool:
    return session.account is None and session.drafts() == ()


async def _outlook_local(path: Path) -> None:
    credentials = _NoMailCredentials()
    transport = _NoMailTransport()
    session = MailSession(credentials=credentials, graph=transport)
    # Synthetic identity exists only in this disposable session; no connect or token lookup.
    account = Account(
        user_id="installer-fixture", address="installer@example.invalid", session="0" * 32
    )
    session.account = account
    message = Message(
        to=["recipient@example.invalid"], subject="Installer diagnostic", body="Synthetic draft"
    )
    registry = ToolRegistry()
    register_outlook(registry, session)
    audit = AuditLog(path)
    engine = PermissionEngine(registry, ApprovalStore(), audit)
    parameters = {"account": account.model_dump(), "message": message.model_dump()}
    try:
        action = _action(engine.prepare("outlook.local_draft", parameters, Mode.EXECUTE))
        _require(action.risk is Risk.SAFE)
        outcome = await engine.execute(action)
        _require(outcome.status is Status.SUCCESS and outcome.error is ErrorCode.NONE)
        result = MailResult.model_validate_json(outcome.result_json or "null")
        _require(
            result.state == "local_draft"
            and result.account == account
            and not result.delivery_verified
            and result.data == message.model_dump_json()
            and session.drafts() == ((result.message_id, message),)
            and session.draft_matches(result.message_id, message)
        )
        changed_account = _action(
            engine.prepare(
                "outlook.local_draft",
                parameters | {"account": account.model_dump() | {"session": "1" * 32}},
                Mode.EXECUTE,
            )
        )
        mismatch = await engine.execute(changed_account)
        _require(
            mismatch.status is Status.DENIED
            and mismatch.error is ErrorCode.PRECONDITION
            and not mismatch.may_have_effects
            and session.drafts() == ((result.message_id, message),)
        )
        remote_draft = _action(engine.prepare("outlook.save_draft", parameters, Mode.SIMULATION))
        _require(remote_draft.risk is Risk.SAFE)
        _require((await engine.execute(remote_draft)).status is Status.SIMULATED)
        remote = _action(engine.prepare("outlook.send", parameters, Mode.EXECUTE))
        _require(remote.risk is Risk.CONFIRM)
        denied = await engine.execute(remote)
        _require(
            denied.status is Status.DENIED
            and denied.error is ErrorCode.APPROVAL
            and not denied.may_have_effects
        )
        session.detach()
        _require(_empty_mail_session(session) and not credentials.called and not transport.called)
    finally:
        session.detach()
        engine.cancel_all()
        audit.close()


def check_outlook_local() -> None:
    """Exercise a synthetic RAM draft through real mail tools; never connect to Outlook."""
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-outlook-local-check-") as directory:
            asyncio.run(_outlook_local(Path(directory) / "audit.sqlite3"))
    except Exception:
        raise RuntimeError("offline_outlook_local_failed") from None


def check_credentials_backend() -> None:
    """Construct the explicit native backend; never read, write or validate a credential."""
    try:
        from jarvis.platforms.credentials import native_store

        _require(isinstance(native_store(), KeyringBackend))
    except Exception:
        raise RuntimeError("credentials_backend_unavailable") from None
