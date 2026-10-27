"""Headless owned JSONL runtime. No Qt imports, hidden windows or Qt workers."""

import argparse
import asyncio
import base64
import json
import os
import sys
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from time import time
from typing import BinaryIO
from uuid import uuid4

from jarvis.browser.host import BrowserHost
from jarvis.config import AppConfig, load_config
from jarvis.core.planner.contracts import Limits, Provider, Step
from jarvis.core.planner.openai_provider import OpenAIProvider
from jarvis.core.planner.runner import Runner
from jarvis.core.report import written
from jarvis.desktop.cloud_consent import (
    CloudPolicyStore,
    CloudReview,
    ReviewedCloudProvider,
    review_reasons,
)
from jarvis.desktop.protocol import MAX_REQUEST, MAX_RESPONSE, Request, Settings
from jarvis.desktop.settings import UiSettings, UiSettingsStore
from jarvis.desktop.system_tools import (
    ChromeCommandProvider,
    DesktopOfflineProvider,
    chrome_command,
    chrome_destination,
    desktop_registry,
)
from jarvis.desktop.voice import DesktopVoice
from jarvis.desktop.watchers import EventWatcher, fingerprint
from jarvis.desktop.workspace import configure as configure_workspace
from jarvis.desktop.workspace import snapshot as workspace_snapshot
from jarvis.mail.credentials import MailFailure
from jarvis.mail.models import Account, Message
from jarvis.mail.session import MailSession
from jarvis.mcp.bridge import register_workspace_mcp
from jarvis.memory.conversation import (
    ConversationRingBuffer,
    Playbook,
    TaskMemoryContext,
    match_playbooks,
)
from jarvis.memory.experience import ExperienceStore, task_snapshot
from jarvis.memory.models import PROFILE_TTL, Hint, MemoryContext
from jarvis.memory.session import SessionContext
from jarvis.memory.store import MemoryFailure, MemoryStore
from jarvis.observability.audit import AuditEvent, AuditKind, AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalStore, ApprovalToken
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.matrix import load_matrix
from jarvis.permissions.policies import Decision, Mode, Risk, Status
from jarvis.platforms.windows.transport import ProcessBackend
from jarvis.platforms.workspace_browser import BrowserLaunchError, WorkspaceBrowser
from jarvis.security.browser_policy import BrowserMode
from jarvis.tools.base import ExecutionContext, ToolError
from jarvis.tools.browser import register_browser
from jarvis.tools.calendar import register_calendar
from jarvis.tools.filesystem import register_filesystem
from jarvis.tools.outlook import register_outlook
from jarvis.tools.windows import Rectangle, register_windows
from jarvis.voice.contracts import ERROR_TEXT, VoiceError, spoken_result
from jarvis.voice.elevenlabs import PREVIEW, ElevenLabsSpeaker
from jarvis.voice.local import LocalRecorder, VoskRecognizer, exchange
from jarvis.workspace.store import WorkspaceFailure, WorkspaceStore


def now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Task:
    id: str = ""
    command: str = field(default="", repr=False)
    state: str = "idle"
    mode: str = "simulation"
    phase: str = "idle"
    active_tool: str = ""
    summary: str = ""
    spoken_summary: str = field(default="", repr=False)
    started_at: str = ""
    steps: list[dict[str, object]] = field(default_factory=list, repr=False)

    def wire(self) -> dict[str, object]:
        return dict(vars(self))


@dataclass
class PendingPrompt:
    id: str
    kind: str
    value: Action | str
    future: asyncio.Future[object]
    expires_at: float

    def wire(self) -> dict[str, object]:
        action = self.value if isinstance(self.value, Action) else None
        return {
            "id": self.id,
            "kind": self.kind,
            "expires_at": self.expires_at,
            "question": self.value if isinstance(self.value, str) else "",
            "signature": action.signature if action else "",
            "action": {
                "tool": action.tool,
                "risk": action.risk.value,
                "mode": action.mode.value,
                "arguments": json.loads(action.payload),
            }
            if action
            else None,
        }


class DesktopService:
    def __init__(
        self,
        config: AppConfig,
        write: Callable[[dict[str, object]], None],
        *,
        provider: Provider | None = None,
        limits: Limits | None = None,
    ) -> None:
        self.config, self.write = config, write
        config.data_dir.mkdir(parents=True, exist_ok=True)
        self.task = Task()
        self.started_count = self.completed_count = 0
        self.history: deque[dict[str, object]] = deque(maxlen=20)
        self.notice = ""
        self.cloud_review: CloudReview | None = None
        self.workspace_state: dict[str, object] | None = None
        self.closing = False
        self.ui_store = UiSettingsStore(config.data_dir)
        self.ui_settings = self.ui_store.load()
        self.cloud_policy = CloudPolicyStore(config.data_dir).load()
        self.settings = Settings.model_validate(
            self.ui_settings.model_dump(
                include={
                    "provider",
                    "auto_start",
                    "speech_enabled",
                    "task_memory_enabled",
                    "wake_word",
                    "auto_stop_on_silence",
                }
            )
            | {
                "model": config.planner_model or self.ui_settings.planner_model,
                "wake_word": os.environ.get(
                    "JARVIS_VOICE_WAKE_WORD", str(int(self.ui_settings.wake_word))
                )
                == "1",
                "auto_stop_on_silence": os.environ.get(
                    "JARVIS_VOICE_AUTO_STOP_ON_SILENCE",
                    str(int(self.ui_settings.auto_stop_on_silence)),
                )
                == "1",
            }
        )
        self.override_provider = provider
        self.limits = limits or Limits()
        self.host = BrowserHost.from_config(config)
        self.registry, self.outbox = desktop_registry(config.data_dir)
        register_filesystem(self.registry, config.filesystem_policy)
        register_browser(self.registry, self.host, self.host.policy, workspace=self.host.workspace)
        register_windows(self.registry, ProcessBackend(before_click=self._highlight))
        self.mail_session = MailSession()
        register_outlook(self.registry, self.mail_session)
        matrix = load_matrix(config.data_dir / "permissions.json")
        self.calendar = register_calendar(self.registry, self.mail_session, matrix)
        register_workspace_mcp(self.registry, config.workspace_profile.mcp_servers, matrix)
        self.audit = AuditLog(config.data_dir / "audit.sqlite3")
        store = ApprovalStore()
        self.approval_store = store
        self.engine = PermissionEngine(self.registry, store, self.audit)
        self._authority = store.take_authority(self.audit.approved)
        self.runner: Runner | None = None
        self.worker: asyncio.Task[None] | None = None
        self.auxiliary: asyncio.Task[None] | None = None
        self.cancelled = Event()
        self.prompt: PendingPrompt | None = None
        self.memory_store = MemoryStore(config.data_dir / "memory.sqlite3")
        self.experience_store = ExperienceStore(self.memory_store.path)
        self.conversation = ConversationRingBuffer()
        self.playbooks: tuple[Playbook, ...] = ()
        self.session_memory = SessionContext()
        self.entries: list[dict[str, object]] = []
        self.mail_result: dict[str, object] = {}
        self.attachments: list[dict[str, object]] = []
        self.eleven: dict[str, object] = {}
        self.cloud_voice: tuple[str, str] | None = None
        self.voice = DesktopVoice(
            self.publish,
            self.transcript,
            self._voice_start,
            self.cancel,
            model_path=self.ui_settings.vosk_model_path,
            wake_word=self.settings.wake_word,
            auto_stop_on_silence=self.settings.auto_stop_on_silence,
        )
        self.voice.auto_start = self.settings.auto_start
        self.voice.speech_enabled = self.settings.speech_enabled
        self.workspace_browser: dict[str, object] = {"state": "disconnected"}
        self.browser_launcher = WorkspaceBrowser(config.data_dir)
        self._wake_blocked = False
        self.last_speech = ""
        self._task_cancelled = False
        self.target_receipt: tuple[str, asyncio.Future[None]] | None = None
        self.clipboard_receipt: tuple[str, asyncio.Future[str]] | None = None
        self.watchers = EventWatcher(
            self._clipboard,
            self._watch_mail,
            lambda: not self.closing and not self.busy and self.voice.task is None,
            self.publish,
            self.voice.speak,
        )

    @property
    def busy(self) -> bool:
        return self.worker is not None or self.auxiliary is not None

    def snapshot(self) -> dict[str, object]:
        return {
            "ready": not self.closing,
            "can_run": not self.busy and self.voice.task is None and self.cloud_review is None,
            "platform": sys.platform,
            "provider": self.settings.provider,
            "model": self.settings.model,
            "settings": self.settings.model_dump(mode="json"),
            "task": self.task.wire(),
            "history": list(self.history),
            "started_count": self.started_count,
            "completed_count": self.completed_count,
            "outlook_connected": self.mail_session.account is not None,
            "account": self.mail_session.account.model_dump(mode="json")
            if self.mail_session.account
            else None,
            "voice_phase": self.voice.phase,
            "voice_status": self.voice.status,
            "voice_background": self.voice.background,
            "wake_enabled": self.voice.wake_enabled,
            "wake_listening": self.voice.wake_task is not None,
            "wake_suspended": self.voice.wake_suspended,
            "native_window_visible": False,
            "notice": self.notice,
            "watcher": self.watchers.snapshot(),
            "prompt": self.prompt.wire() if self.prompt else None,
            "cloud_review": self.cloud_review.wire() if self.cloud_review else None,
            "memory_entries": self.entries,
            "recent_tasks": [item.model_dump(mode="json") for item in self.conversation.read()],
            "playbooks": [
                item.model_dump(mode="json")
                for item in self.playbooks
                if 0 <= time() - item.created < PROFILE_TTL
            ],
            "session_memory": [item.model_dump(mode="json") for item in self.session_memory.read()],
            "mail_result": self.mail_result,
            "drafts": [
                {"id": key, "subject": message.subject}
                for key, message in self.mail_session.drafts()
            ],
            "attachments": self.attachments,
            "eleven": self.eleven,
            "spoken_text": self.last_speech,
            "tools": [
                {key: item[key] for key in ("name", "description", "risk")}
                for item in self.registry.discover()
            ],
            "workspace": self.workspace_state,
            "workspace_browser": self.workspace_browser,
        }

    def publish(self) -> None:
        if not self.closing:
            self.write({"event": "state", "state": self.snapshot()})
            self.voice.reconcile_wake(
                not self.busy
                and self.prompt is None
                and self.cloud_review is None
                and not self._wake_blocked
            )

    def transcript(self, text: str) -> None:
        self.write({"event": "transcript", "text": text[:4000]})

    def _event(self, kind: str, value: object) -> None:
        self.task.phase = kind
        if kind == "executing" and isinstance(value, str):
            self.task.active_tool = value
        if kind == "tool" and isinstance(value, Step):
            self.task.steps.append(
                {
                    "tool": value.tool,
                    "status": value.outcome.status.value,
                    "error": value.outcome.error.value,
                    "may_have_effects": value.outcome.may_have_effects,
                    "result": (value.outcome.result_json or "")[:2000],
                    "at": now(),
                }
            )
        self.publish()

    async def _clipboard(self) -> str:
        if not self.settings.watch_clipboard or self.closing:
            return ""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        receipt = uuid4().hex, future
        self.clipboard_receipt = receipt
        self.write({"event": "clipboard_poll", "id": receipt[0]})
        try:
            async with asyncio.timeout(2):
                return await receipt[1]
        finally:
            if self.clipboard_receipt is receipt:
                self.clipboard_receipt = None

    async def _watch_tool(self, tool: str, arguments: dict[str, object]) -> dict[str, object]:
        if tool not in {"outlook.list", "outlook.read", "outlook.local_draft"}:
            raise ValueError("watcher_unavailable")
        action = self.engine.prepare(tool, arguments, Mode.EXECUTE)
        if isinstance(action, Outcome) or action.risk is not Risk.SAFE:
            raise ValueError("watcher_unavailable")
        try:
            outcome = await self.engine.execute(action)
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise asyncio.CancelledError
            if outcome.status is not Status.SUCCESS or not outcome.result_json:
                raise ValueError("watcher_unavailable")
            result: dict[str, object] = json.loads(outcome.result_json)
            return result
        finally:
            self.engine.cancel(action)

    async def _watch_mail(self) -> tuple[Account, list[dict[str, object]]] | None:
        account = self.mail_session.account
        if account is None:
            return None
        result = await self._watch_tool(
            "outlook.list",
            {
                "account": account.model_dump(mode="json"),
                "folder": "inbox",
                "limit": 10,
            },
        )
        if not self.mail_session.matches(account):
            return None
        rows = json.loads(str(result.get("data", "[]")))
        return account, rows

    async def _watcher_action(self, raw: Request) -> None:
        item = self.watchers.get(raw.notification_id)
        if item.kind != "mail":
            if not self.settings.watch_clipboard:
                raise ValueError("watcher_expired")
            text = await self._clipboard()
            if fingerprint(text) != item.digest:
                self.watchers.dismiss(item.id)
                raise ValueError("watcher_expired")
            instruction = (
                "Разбери причину ошибки" if item.kind == "error" else "Переведи на русский"
            )
            self.transcript(instruction + ":\n" + text[:3500])
            self.notice = "Текст вставлен в команду. Проверьте его перед запуском." + (
                " Вставлены первые 3500 символов." if len(text) > 3500 else ""
            )
        else:
            account = item.account
            if (
                not self.settings.watch_mail
                or account is None
                or not self.mail_session.matches(account)
            ):
                raise ValueError("watcher_expired")
            current = await self._watch_tool(
                "outlook.read",
                {
                    "account": account.model_dump(mode="json"),
                    "message_id": item.message_id,
                },
            )
            message = json.loads(str(current.get("data", "{}")))
            if (
                message.get("id") != item.message_id
                or message.get("from", {}).get("emailAddress", {}).get("address") != item.recipient
                or message.get("subject") != item.subject
            ):
                raise ValueError("watcher_expired")
            draft = Message(
                to=[item.recipient],
                subject=("Re: " + item.subject)[:500],
                body="Здравствуйте! Спасибо за письмо. Я ознакомлюсь с ним и подготовлю ответ.",
            )
            self.mail_result = await self._watch_tool(
                "outlook.local_draft",
                {
                    "account": account.model_dump(mode="json"),
                    "action_type": "local_draft",
                    "message": draft.model_dump(mode="json"),
                },
            )
            self.mail_result["state"] = "loaded_draft"
            self.notice = (
                "Черновик ответа создан в RAM. Проверьте адрес и отредактируйте текст в Outlook."
            )
        self.watchers.dismiss(item.id)

    async def _highlight(self, rectangle: Rectangle) -> None:
        """A display receipt is not action approval; it can only release an audited adapter."""
        receipt = uuid4().hex, asyncio.get_running_loop().create_future()
        self.target_receipt = receipt
        self.write({"event": "target", "id": receipt[0], "rectangle": rectangle.model_dump()})
        try:
            async with asyncio.timeout(3):
                await receipt[1]
        except TimeoutError:
            raise ToolError("native_timeout") from None
        finally:
            self.target_receipt = None

    async def _ask(self, kind: str, value: Action | str) -> object:
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        prompt = PendingPrompt(
            uuid4().hex, kind, value, future, time() + (60 if kind == "approval" else 120)
        )
        self.prompt = prompt
        self.task.phase = kind
        self.publish()
        try:
            async with asyncio.timeout(60 if kind == "approval" else 120):
                return await future
        except TimeoutError:
            self.notice = "Время ответа истекло. Действие не подтверждено."
            return None
        finally:
            if isinstance(value, Action) and not future.done():
                self.engine.cancel(value)
            if self.prompt is prompt:
                self.prompt = None
            self.publish()

    async def _approve(self, action: Action) -> ApprovalToken | None:
        result = await self._ask("approval", action)
        return result if isinstance(result, ApprovalToken) else None

    async def _clarify(self, question: str) -> str | None:
        result = await self._ask("clarification", question)
        return result if isinstance(result, str) else None

    def _respond(self, raw: Request) -> None:
        reply, prompt = raw.reply, self.prompt
        if (
            reply is None
            or prompt is None
            or prompt.future.done()
            or reply.prompt_id != prompt.id
            or time() >= prompt.expires_at
        ):
            raise ValueError("prompt_expired")
        if prompt.kind == "approval" and isinstance(prompt.value, Action):
            if reply.signature != prompt.value.signature or reply.answer:
                raise ValueError("prompt_changed")
            token = self._authority.approve(prompt.value) if reply.approved else None
            prompt.future.set_result(token)
        elif prompt.kind == "clarification":
            if reply.approved or reply.signature:
                raise ValueError("invalid_answer")
            prompt.future.set_result(reply.answer.strip() or None)
        else:
            raise ValueError("invalid_answer")
        self.prompt = None

    def _voice_start(self, command: str, mode: Mode) -> bool:
        return self.start(command, mode)

    def _clear_cloud_review(self) -> None:
        pending, self.cloud_review = self.cloud_review, None
        if pending is not None and pending.future is not None and not pending.future.done():
            pending.future.cancel()

    def _prepare_cloud_request(self, raw: Request) -> tuple[Request, bool] | None:
        self._clear_cloud_review()
        local = self.override_provider is None and chrome_destination(raw.command) is not None
        if self.settings.provider != "openai" or local:
            return raw, False
        reasons = review_reasons(raw.command)
        if raw.cloud_consent and not reasons:
            # Compatibility for an explicit one-task grant from existing trusted clients.
            return raw, True
        if self.cloud_policy.routine_cloud_consent and not reasons:
            return raw.model_copy(
                update={
                    "cloud_consent": True,
                    "conversation_consent": True,
                    "memory_consent": True,
                }
            ), False
        self.cloud_review = CloudReview(
            uuid4().hex,
            raw,
            reasons or ("Разрешение на обработку задачи в OpenAI",),
            time() + 300,
            self.settings.model_dump_json(),
            self.settings.task_memory_enabled,
        )
        self.notice = ""
        self.publish()
        return None

    async def _launch_request(self, raw: Request, *, reviewed: bool = False) -> bool:
        if self.busy or self.voice.task is not None:
            raise ValueError("busy")
        if reviewed:
            authorized = raw
        else:
            prepared = self._prepare_cloud_request(raw)
            if prepared is None:
                return False
            authorized, reviewed = prepared
        return self.start(
            authorized.command,
            Mode(authorized.mode),
            cloud_consent=authorized.cloud_consent,
            memory=await self._memory_context(authorized),
            conversation_consent=authorized.conversation_consent,
            _authorized=True,
            _reviewed=reviewed,
            _request=authorized,
        )

    async def _respond_cloud_review(self, raw: Request) -> None:
        reply, pending = raw.cloud_review, self.cloud_review
        if (
            reply is None
            or pending is None
            or reply.id != pending.id
            or time() >= pending.expires_at
        ):
            self._clear_cloud_review()
            raise ValueError("cloud_review_expired")
        if pending.settings_snapshot != self.settings.model_dump_json():
            self._clear_cloud_review()
            raise ValueError("cloud_review_changed")
        self.cloud_review = None  # Consume before starting any work or resolving a waiter.
        if not reply.allow_current:
            if pending.future is not None and not pending.future.done():
                pending.future.cancel()
            self.cancel()
            self.notice = "Передача задачи в OpenAI отменена."
            return
        self.notice = ""
        if pending.future is not None:
            if pending.future.done():
                raise ValueError("cloud_review_expired")
            pending.future.set_result(reply.allow_history)
        else:
            await self._launch_request(
                pending.request.model_copy(
                    update={
                        "cloud_consent": True,
                        "memory_consent": True,
                        "conversation_consent": reply.allow_history,
                    }
                ),
                reviewed=True,
            )

    async def _review_more_cloud_context(self, raw: Request) -> bool:
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        pending = CloudReview(
            uuid4().hex,
            raw,
            ("Задача стала многошаговой или требует передачи скриншота",),
            time() + 60,
            self.settings.model_dump_json(),
            self.settings.task_memory_enabled,
            future,
        )
        self.cloud_review = pending
        self.task.phase = "cloud_review"
        self.publish()
        try:
            return await asyncio.wait_for(asyncio.shield(future), 60)
        except TimeoutError:
            raise asyncio.CancelledError from None
        finally:
            if self.cloud_review is pending:
                self._clear_cloud_review()

    def start(
        self,
        command: str,
        mode: Mode,
        *,
        cloud_consent: bool = False,
        memory: MemoryContext | None = None,
        conversation_consent: bool = False,
        _authorized: bool = False,
        _reviewed: bool = False,
        _request: Request | None = None,
    ) -> bool:
        if self.busy or self.voice.task is not None or self.closing:
            raise ValueError("busy")
        request = _request or Request(
            id=uuid4(),
            operation="run",
            command=command,
            mode=mode.value,
            cloud_consent=cloud_consent,
            conversation_consent=conversation_consent,
        )
        if not _authorized:
            prepared = self._prepare_cloud_request(request)
            if prepared is None:
                return False
            request, _reviewed = prepared
            cloud_consent = request.cloud_consent
            conversation_consent = request.conversation_consent
        provider = self.override_provider
        direct_target = chrome_destination(command) if provider is None else None
        if direct_target is not None:
            provider = ChromeCommandProvider(chrome_command(command))
        if self.settings.provider == "openai" and direct_target is None:
            provider = provider or OpenAIProvider(self.settings.model)
            provider = ReviewedCloudProvider(
                provider,
                lambda: self._review_more_cloud_context(request),
                reviewed=_reviewed,
                history_allowed=conversation_consent,
            )
        provider = provider or DesktopOfflineProvider()
        self.voice.wake_review = False
        self.voice.pause_wake()
        self.notice = ""
        self.last_speech = ""
        self._task_cancelled = False
        self.cloud_voice = None
        self.task = Task(
            id=uuid4().hex,
            command=command,
            state="working",
            mode=mode.value,
            phase="thinking",
            started_at=now(),
        )
        self.started_count += 1
        self.runner = Runner(
            self.registry,
            self.engine,
            provider,
            self._approve,
            self._clarify,
            limits=self.limits,
            memory=memory,
            workspace=self.config.workspace_profile,
            notify=self._event,
        )
        include_history = self.settings.task_memory_enabled and (
            self.settings.provider != "openai" or conversation_consent
        )
        self.worker = asyncio.create_task(self._run(command, mode, self.runner, include_history))
        self.publish()
        return True

    async def _load_experience(self) -> None:
        tasks, self.playbooks = await asyncio.to_thread(self.experience_store.load)
        self.conversation.replace(tasks)

    async def _run(self, command: str, mode: Mode, runner: Runner, include_history: bool) -> None:
        try:
            await self.voice.stop_wake()
            try:
                await self._load_experience()
            except MemoryFailure:
                self.conversation.clear()
                self.playbooks = ()
                self.notice = "Память задач недоступна. Задача продолжится без прошлого контекста."
            if include_history:
                runner.task_memory = TaskMemoryContext(
                    recent=self.conversation.read(),
                    playbooks=match_playbooks(command, self.playbooks),
                )
            outcome = await runner.run(command, mode)
            self.task.state, self.task.phase = outcome.status, "idle"
            self.task.summary = outcome.summary[:3000]
            if any(step.tool == "windows.open_chrome_url" for step in outcome.steps):
                self.task.summary = ("\n".join(written(outcome)) + "\n" + outcome.summary)[:3000]
            self.task.spoken_summary = outcome.spoken_summary
            self.last_speech = spoken_result(outcome)
            if outcome.status == "finished":
                self.completed_count += 1
            if self.settings.task_memory_enabled and mode is Mode.EXECUTE:
                try:
                    snapshot = task_snapshot(command, outcome)
                    if snapshot is not None:
                        await asyncio.to_thread(self.experience_store.remember, snapshot)
                        await self._load_experience()
                except (MemoryFailure, ValueError):
                    self.notice = "Задача завершена, но её контекст не удалось сохранить."
            self.history.appendleft(
                {
                    "id": self.task.id,
                    "command": self.task.command[:120],
                    "state": outcome.status,
                    "mode": self.task.mode,
                    "at": now(),
                    "steps": len(outcome.steps),
                }
            )
            if (
                self.voice.speech_enabled
                and outcome.status != "cancelled"
                and not self._task_cancelled
            ):
                await self.voice.drain()
                # LLM prose stays local unless separately authorized after its exact text is shown.
                if not self._task_cancelled and not self.closing:
                    self.voice.speak(self.last_speech)
        finally:
            self._clear_cloud_review()
            self.worker = None
            self.runner = None
            self.publish()

    def cancel(self) -> None:
        self._clear_cloud_review()
        self._task_cancelled = True
        self.cloud_voice = None
        self.voice.cancel()
        self.cancelled.set()
        if self.runner is not None:
            self.runner.cancel()
        if self.auxiliary is not None:
            self.auxiliary.cancel()
        if self.prompt is not None:
            prompt, self.prompt = self.prompt, None
            if isinstance(prompt.value, Action):
                self.engine.cancel(prompt.value)
            if not prompt.future.done():
                prompt.future.set_result(None)
        self.publish()

    async def _memory_context(self, raw: Request) -> MemoryContext:
        entries = await asyncio.to_thread(self.memory_store.read)
        selected = [item for item in entries if item.id in raw.memory_ids]
        if len(selected) != len(set(raw.memory_ids)):
            raise ValueError("memory_changed")
        context = MemoryContext(
            profile=tuple(
                Hint(kind=item.kind, label=item.label, value=item.value) for item in selected
            ),
            session=self.session_memory.read() if raw.session_memory else (),
        )
        if self.settings.provider == "openai" and not context.empty and not raw.memory_consent:
            raise ValueError("memory_consent_required")
        return context

    async def handle(self, raw: Request) -> None:
        if self.closing:
            return
        error = ""
        preempt = raw.operation in {
            "workspace",
            "workspace_browser",
            "run",
            "configure",
            "voice_start",
            "voice_model",
            "speak_result",
            "memory",
            "mail",
            "eleven",
            "watcher_action",
            "cancel",
            "voice_wake_resume",
        }
        if preempt:
            self._wake_blocked = True
            self.voice.pause_wake()
            await self.watchers.pause()
        try:
            if raw.operation == "clipboard_sample":
                sample = self.clipboard_receipt
                if sample is None or sample[0] != raw.sample_id or sample[1].done():
                    raise ValueError("watcher_expired")
                sample[1].set_result(raw.clipboard_text)
            elif raw.operation == "watcher_action":
                if raw.dismiss:
                    self.watchers.dismiss(raw.notification_id)
                else:
                    if self.busy or self.voice.task is not None:
                        raise ValueError("busy")
                    self.auxiliary = asyncio.create_task(self._auxiliary(raw))
            elif raw.operation == "run":
                if not await self._launch_request(raw):
                    error = "cloud_consent_required"
            elif raw.operation == "cloud_review":
                await self._respond_cloud_review(raw)
            elif raw.operation == "cancel":
                self.watchers.notices.clear()
                self.cancel()
            elif raw.operation == "respond":
                self._respond(raw)
            elif raw.operation == "target_presented":
                receipt = self.target_receipt
                if receipt is None or receipt[0] != raw.target_id or receipt[1].done():
                    raise ValueError("invalid_target_receipt")
                receipt[1].set_result(None)
            elif raw.operation == "workspace":
                if self.busy or self.voice.task is not None or self.prompt is not None:
                    raise ValueError("busy")
                self.auxiliary = asyncio.create_task(self._workspace(raw))
                try:
                    await self.auxiliary
                finally:
                    self.auxiliary = None
            elif raw.operation == "workspace_browser":
                if self.busy or self.voice.task is not None or self.prompt is not None:
                    raise ValueError("busy")
                self.auxiliary = asyncio.create_task(self.connect_workspace_browser())
                try:
                    await self.auxiliary
                finally:
                    self.auxiliary = None
            elif raw.operation == "configure":
                self._clear_cloud_review()
                if self.busy or self.voice.task is not None:
                    raise ValueError("busy")
                assert raw.settings is not None
                if raw.settings.model:
                    OpenAIProvider(raw.settings.model)
                persisted = UiSettings.model_validate(
                    self.ui_settings.model_dump()
                    | raw.settings.model_dump(
                        exclude={
                            "model",
                            "watch_clipboard",
                            "watch_mail",
                            "watch_speech",
                        }
                    )
                    | {"planner_model": raw.settings.model}
                )
                await asyncio.to_thread(self.ui_store.save, persisted)
                self.ui_settings = persisted
                await self.voice.stop_wake()
                self.settings = raw.settings
                self.voice.auto_start = self.settings.auto_start
                self.voice.speech_enabled = self.settings.speech_enabled
                self.voice.wake_enabled = self.settings.wake_word
                self.voice.wake_suspended = False
                if isinstance(self.voice.recorder, LocalRecorder):
                    self.voice.recorder.auto_stop_on_silence = self.settings.auto_stop_on_silence
                self.cloud_voice = None
                await self.watchers.configure(
                    self.settings.watch_clipboard,
                    self.settings.watch_mail,
                    self.settings.watch_speech,
                )
                if not self.settings.task_memory_enabled:
                    self.conversation.clear()
                    self.playbooks = ()
            elif raw.operation == "voice_start":
                if self.auxiliary is not None:
                    raise ValueError("busy")
                self.voice.record(Mode(raw.mode), self.worker is not None)
            elif raw.operation == "voice_model":
                if self.busy or self.voice.task is not None:
                    raise ValueError("busy")
                await self.voice.stop_wake()
                if isinstance(self.voice.recognizer, VoskRecognizer):
                    await asyncio.to_thread(self.voice.recognizer.shutdown)
                self.voice.recognizer = VoskRecognizer(Path(raw.model_path))
                self.voice.recognizer.warmup()
                persisted = UiSettings.model_validate(
                    self.ui_settings.model_dump() | {"vosk_model_path": raw.model_path}
                )
                await asyncio.to_thread(self.ui_store.save, persisted)
                self.ui_settings = persisted
            elif raw.operation == "voice_wake_resume":
                if self.busy or self.voice.task is not None or not self.voice.wake_enabled:
                    raise ValueError("busy")
                self.voice.wake_suspended = False
                self.voice.wake_review = False
                self.voice.wake_mode = Mode(raw.mode)
            elif raw.operation == "voice_release":
                self.voice.release()
            elif raw.operation == "voice_cancel":
                self.voice.cancel()
            elif raw.operation == "speak_result":
                if self.busy or not self.last_speech:
                    raise ValueError("busy")
                speaker = None
                if self.cloud_voice is not None:
                    voice, account = self.cloud_voice
                    self.cloud_voice = None
                    speaker = ElevenLabsSpeaker(voice, account, self.last_speech)
                self.voice.speak(self.last_speech, speaker)
            elif raw.operation in {"memory", "mail", "eleven"}:
                if self.busy or self.voice.task is not None:
                    raise ValueError("busy")
                self.cancelled = Event()
                self.auxiliary = asyncio.create_task(self._auxiliary(raw))
            elif raw.operation == "open":
                # Navigation is entirely in React. Opening a page performs no effect.
                self.notice = ""
        except VoiceError as exc:
            error = exc.code
            self.notice = ERROR_TEXT[exc.code]
        except WorkspaceFailure as exc:
            error = str(exc)
        except BrowserLaunchError as exc:
            error = str(exc)
        except ValueError as exc:
            allowed = {
                "busy",
                "watcher_expired",
                "prompt_expired",
                "prompt_changed",
                "invalid_answer",
                "memory_changed",
                "memory_consent_required",
                "cloud_review_expired",
                "cloud_review_changed",
                "workspace_changed",
            }
            error = str(exc) if str(exc) in allowed else "invalid_request"
        except Exception:
            error = "runtime_operation_failed"
        finally:
            self._wake_blocked = False
            if preempt and raw.operation != "cancel":
                self.watchers.resume()
        self.write({"id": str(raw.id), "ok": not error, "error": error, "state": self.snapshot()})
        self.publish()

    async def _workspace(self, raw: Request) -> None:
        assert raw.workspace is not None
        await self.voice.stop_wake()
        profile = await asyncio.to_thread(
            configure_workspace, WorkspaceStore(self.config.data_dir), raw.workspace
        )
        if raw.workspace.operation != "read":
            persisted = UiSettings.model_validate(
                self.ui_settings.model_dump() | {"browser_mode": profile.browser_mode}
            )
            await asyncio.to_thread(self.ui_store.save, persisted)
            self.ui_settings = persisted
        self.workspace_state = workspace_snapshot(
            profile, self.config.workspace_profile, self.config.browser_mode.value
        )
        self.notice = (
            "Рабочее пространство сохранено. Перезапустите Jarvis, чтобы применить настройки."
            if raw.workspace.operation != "read"
            else ""
        )

    async def connect_workspace_browser(self) -> None:
        """A visible owner's control replaces an idle runtime, never an in-flight action."""
        await self.voice.stop_wake()
        self.workspace_browser = {"state": "connecting"}
        self.publish()
        host: BrowserHost | None = None
        try:
            endpoint = await asyncio.to_thread(self.browser_launcher.launch)
            active = self.config.workspace_profile
            active = type(active).model_validate(
                active.model_dump() | {"browser_mode": "authenticated_workspace"}
            )
            config = replace(
                self.config,
                browser_mode=BrowserMode.WORKSPACE,
                browser_cdp_endpoint=endpoint,
                workspace_profile=active,
            )
            host = BrowserHost.from_config(config)
            # Re-compose the same trusted surface against the existing mail session,
            # audit and approval store; all previous task observations are discarded.
            registry, outbox = desktop_registry(config.data_dir)
            register_filesystem(registry, config.filesystem_policy)
            register_browser(registry, host, host.policy, workspace=host.workspace)
            register_windows(registry, ProcessBackend(before_click=self._highlight))
            register_outlook(registry, self.mail_session)
            matrix = load_matrix(config.data_dir / "permissions.json")
            calendar = register_calendar(registry, self.mail_session, matrix)
            register_workspace_mcp(registry, active.mcp_servers, matrix)
            engine = PermissionEngine(registry, self.approval_store, self.audit)
            prepared = engine.prepare("browser.get_tabs", {}, Mode.EXECUTE)
            try:
                if isinstance(prepared, Outcome):
                    raise BrowserLaunchError("workspace_browser_failed")
                outcome = await engine.execute(prepared)
                if outcome.status is not Status.SUCCESS:
                    raise BrowserLaunchError("workspace_browser_failed")
            except BaseException:
                await asyncio.to_thread(host.shutdown)
                raise
            finally:
                if not isinstance(prepared, Outcome):
                    engine.cancel(prepared)
            persisted = UiSettings.model_validate(
                self.ui_settings.model_dump()
                | {
                    "browser_mode": "authenticated_workspace",
                    "managed_browser": True,
                }
            )
            profile_store = WorkspaceStore(config.data_dir)
            profile = await asyncio.to_thread(profile_store.load)
            profile = type(profile).model_validate(
                profile.model_dump()
                | {
                    "browser_mode": "authenticated_workspace",
                }
            )
            await asyncio.to_thread(profile_store.save, profile)
            await asyncio.to_thread(self.ui_store.save, persisted)
            await asyncio.to_thread(self.host.shutdown)
            self.host, self.config, self.registry, self.engine = host, config, registry, engine
            self.calendar, self.outbox = calendar, outbox
            self.runner = None
            self.ui_settings = persisted
            self.workspace_browser = {"state": "connected", "endpoint": endpoint}
            self.notice = "Рабочий браузер подключён. Войдите в сервисы в его отдельном окне."
            if self.workspace_state is not None:
                self.workspace_state = workspace_snapshot(
                    profile, active, config.browser_mode.value
                )
        except BaseException:
            if host is not None and host is not self.host:
                await asyncio.to_thread(host.shutdown)
            self.workspace_browser = {"state": "disconnected"}
            raise

    async def _auxiliary(self, raw: Request) -> None:
        try:
            await self.voice.stop_wake()
            if raw.operation == "watcher_action":
                await self._watcher_action(raw)
            elif raw.memory is not None:
                await self._memory(raw)
            elif raw.mail is not None:
                await self._mail(raw)
            elif raw.eleven is not None:
                await self._eleven(raw)
        except asyncio.CancelledError:
            self.notice = "Операция отменена. Уже выданные действия не отозваны."
        except ValueError as exc:
            self.notice = (
                "Подсказка истекла или данные изменились. Дождитесь нового события."
                if str(exc) == "watcher_expired"
                else "Не удалось выполнить операцию. Проверьте данные и настройки."
            )
        except (MemoryFailure, MailFailure, VoiceError) as exc:
            self.notice = ERROR_TEXT.get(exc.code, "Операция недоступна: " + exc.code)
        except Exception:
            self.notice = "Не удалось выполнить операцию. Проверьте данные и настройки."
        finally:
            self.auxiliary = None
            if self.worker is None:
                self.task.phase = "idle"
            self.publish()

    async def _memory(self, raw: Request) -> None:
        edit = raw.memory
        assert edit is not None
        if edit.operation == "read":
            entries = await asyncio.to_thread(self.memory_store.read, self.cancelled)
            await self._load_experience()
        elif edit.operation in {"experience_delete", "experience_clear"}:
            await asyncio.to_thread(self.experience_store.forget, edit.record_id, self.cancelled)
            await self._load_experience()
            entries = await asyncio.to_thread(self.memory_store.read, self.cancelled)
        elif edit.operation == "save" and edit.entry is not None:
            entries = await asyncio.to_thread(
                self.memory_store.save, edit.entry, edit.previous, self.cancelled
            )
        elif edit.operation == "delete" and edit.previous is not None:
            entries = await asyncio.to_thread(
                self.memory_store.delete, edit.previous, self.cancelled
            )
        elif edit.operation == "clear":
            entries = await asyncio.to_thread(self.memory_store.clear, self.cancelled)
        else:
            if edit.operation == "session_add" and edit.hint is not None:
                self.session_memory.add(edit.hint)
            elif edit.operation == "session_delete" and edit.hint is not None:
                for hint in self.session_memory.read():
                    if hint == edit.hint:
                        self.session_memory.delete(hint)
            elif edit.operation == "session_clear":
                self.session_memory.clear()
            else:
                raise ValueError("invalid_request")
            entries = await asyncio.to_thread(self.memory_store.read, self.cancelled)
        self.entries = [item.model_dump(mode="json") for item in entries]

    async def _mail(self, raw: Request) -> None:
        item = raw.mail
        assert item is not None
        if item.operation in {"connect", "disconnect"}:
            if item.operation == "connect" and not item.consent:
                raise ValueError("connection_consent_required")
            await self._connection(item.operation, item.client_id)
            return
        if item.operation == "load_draft":
            draft = next(
                (message for key, message in self.mail_session.drafts() if key == item.draft_id),
                None,
            )
            if draft is None:
                raise ValueError("draft_changed")
            self.mail_result = {"state": "loaded_draft", "data": draft.model_dump_json()}
            return
        if item.operation == "attach":
            content = base64.b64decode(item.content, validate=True)
            if len(content) > 32768:
                raise ValueError("attachment_limit")
            attached = self.mail_session.attach(item.name, content)
            self.attachments.append(attached.model_dump(mode="json"))
            return
        if item.operation == "clear_attachments":
            self.mail_session.clear_attachments()
            self.attachments.clear()
            return
        account = self.mail_session.account
        if account is None:
            raise MailFailure("mail_disconnected")
        arguments: dict[str, object] = {"account": account.model_dump(mode="json")}
        tool = {
            "list": "outlook.list",
            "read": "outlook.read",
            "local_draft": "outlook.local_draft",
            "save_draft": "outlook.save_draft",
            "send": "outlook.send",
        }[item.operation]
        if item.operation == "read":
            # Only a message ID observed in this account/session's actual listing.
            data = json.loads(str(self.mail_result.get("data", "[]")))
            if self.mail_result.get("state") != "listed" or not any(
                row.get("id") == item.message_id for row in data
            ):
                raise ValueError("unobserved_target")
            arguments["message_id"] = item.message_id
        elif item.operation in {"local_draft", "save_draft", "send"}:
            if item.message is None:
                raise ValueError("invalid_request")
            arguments.update(
                message=item.message.model_dump(mode="json"),
                action_type={
                    "local_draft": "local_draft",
                    "save_draft": "remote_draft",
                    "send": "send_mail",
                }[item.operation],
            )
        action = self.engine.prepare(tool, arguments, Mode(raw.mode))
        if isinstance(action, Outcome):
            raise ValueError("invalid_request")
        try:
            token = await self._approve(action) if action.risk is Risk.CONFIRM else None
            outcome = await self.engine.execute(action, token)
            self.mail_result = json.loads(outcome.result_json or "{}")
            self.notice = "Outlook: " + outcome.status.value
        finally:
            self.engine.cancel(action)

    async def _connection(self, operation: str, client: str) -> None:
        request_id = uuid4()
        self.mail_result.clear()
        self.attachments.clear()
        self.watchers.reset_mail()

        def audit(kind: AuditKind, status: Status | None = None) -> None:
            self.audit.write(
                AuditEvent(
                    kind,
                    request_id,
                    "outlook.account",
                    Risk.SAFE,
                    Mode.EXECUTE,
                    Decision.ALLOW,
                    status,
                    error=ErrorCode.NONE
                    if status in (None, Status.SUCCESS)
                    else ErrorCode.EXECUTION,
                    may_have_effects=True,
                    actor="user_ui_connection",
                )
            )

        if operation == "connect":
            from uuid import UUID

            client = str(UUID(client))
        audit(AuditKind.STARTED)
        try:
            async with asyncio.timeout(180):
                if operation == "connect":
                    await self.mail_session.connect(client, ExecutionContext(self.cancelled))
                else:
                    await self.mail_session.disconnect()
            audit(AuditKind.FINISHED, Status.SUCCESS)
        except BaseException:
            self.mail_session.detach()
            audit(AuditKind.FINISHED, Status.ERROR)
            raise

    async def _eleven(self, raw: Request) -> None:
        item = raw.eleven
        assert item is not None
        self.cloud_voice = None
        if item.operation in {"voices", "set", "delete"}:
            self.eleven = {}
            request: dict[str, object] = {"operation": item.operation}
            if item.operation == "set":
                request["key"] = item.key
            self.eleven = await exchange(request, seconds=55, helper="jarvis.platforms.elevenlabs")
        elif item.operation in {"select", "speak"}:
            account = self.eleven.get("account")
            voices = self.eleven.get("voices", [])
            if not item.consent or not isinstance(account, str) or not isinstance(voices, list):
                raise VoiceError("eleven_consent")
            if not any(voice.get("id") == item.voice_id for voice in voices):
                raise VoiceError("eleven_voice")
            if item.operation == "speak":
                await ElevenLabsSpeaker(item.voice_id, account, PREVIEW).speak(PREVIEW)
            elif self.last_speech:
                # Exact current, displayed result, one use; starting another task clears it.
                self.cloud_voice = (item.voice_id, account)
                self.notice = "Разрешена одна озвучка текущего итога. Нажмите «Озвучить итог»."
            else:
                raise VoiceError("eleven_consent")

    async def shutdown(self) -> None:
        if self.closing:
            return
        self.closing = True
        await self.watchers.close()
        self.cancel()
        await self.voice.shutdown()
        pending = [item for item in (self.worker, self.auxiliary) if item is not None]
        await asyncio.gather(*pending, return_exceptions=True)
        self.session_memory.clear()
        self.conversation.clear()
        self.playbooks = ()
        self.entries.clear()
        self.mail_session.detach()
        await asyncio.to_thread(self.host.shutdown)
        self.audit.close()


def read_pipe(
    stream: BinaryIO, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[Request | None]
) -> None:
    try:
        while True:
            line = stream.readline(MAX_REQUEST + 1)
            if not line or len(line) > MAX_REQUEST or not line.endswith(b"\n"):
                break
            try:
                request = Request.model_validate_json(line)
            except ValueError:
                break
            asyncio.run_coroutine_threadsafe(queue.put(request), loop).result(timeout=10)
    finally:
        if not loop.is_closed():
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)


async def serve() -> int:
    def write(value: dict[str, object]) -> None:
        payload = (json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode()
        if len(payload) > MAX_RESPONSE:
            raise ValueError("Desktop response limit.")
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()

    try:
        service = DesktopService(load_config(), write)
    except Exception:
        write({"event": "fatal", "error": "runtime_initialization_failed"})
        return 1
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Request | None] = asyncio.Queue(maxsize=8)
    Thread(target=read_pipe, args=(sys.stdin.buffer, loop, queue), daemon=True).start()
    service.publish()
    if service.ui_settings.managed_browser and service.config.browser_mode is BrowserMode.WORKSPACE:
        service.auxiliary = asyncio.create_task(service.connect_workspace_browser())
        try:
            await service.auxiliary
        except (BrowserLaunchError, WorkspaceFailure, ToolError, ValueError):
            await asyncio.to_thread(service.host.shutdown)
            service.notice = (
                "Рабочий браузер недоступен. Нажмите «Открыть рабочий браузер» в Пространстве."
            )
        finally:
            service.auxiliary = None
        service.publish()
    service.voice.wake_allowed = True
    service.publish()
    try:
        while (request := await queue.get()) is not None:
            await service.handle(request)
        return 0
    finally:
        await service.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis private headless desktop runtime")
    parser.add_argument("--stdio", action="store_true", required=True)
    parser.parse_args()
    return asyncio.run(serve())


if __name__ == "__main__":
    raise SystemExit(main())
