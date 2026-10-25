"""Bounded private JSONL. UI replies reference a pending immutable prompt, never a token."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from jarvis.mail.models import Message
from jarvis.memory.models import Entry, Hint
from jarvis.tools.base import ToolModel

MAX_REQUEST = 131072
MAX_RESPONSE = 1048576


class Settings(ToolModel):
    provider: Literal["offline", "openai"] = "offline"
    model: str = Field(default="", max_length=100)
    auto_start: bool = False
    speech_enabled: bool = False
    task_memory_enabled: bool = True
    watch_clipboard: bool = False
    watch_mail: bool = False
    watch_speech: bool = False


class Reply(ToolModel):
    prompt_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    signature: str = Field(default="", max_length=64)
    approved: bool = False
    answer: str = Field(default="", max_length=4000, repr=False)


class MemoryEdit(ToolModel):
    operation: Literal[
        "read",
        "save",
        "delete",
        "clear",
        "session_add",
        "session_delete",
        "session_clear",
        "experience_delete",
        "experience_clear",
    ]
    entry: Entry | None = None
    previous: Entry | None = None
    hint: Hint | None = None
    record_id: str = Field(default="", pattern=r"^(?:[a-f0-9]{32})?$")

    @model_validator(mode="after")
    def record_scope(self) -> "MemoryEdit":
        if (self.operation == "experience_delete") != bool(self.record_id):
            raise ValueError("Record ID outside deletion.")
        return self


class MailCommand(ToolModel):
    operation: Literal[
        "connect",
        "disconnect",
        "list",
        "read",
        "local_draft",
        "save_draft",
        "send",
        "attach",
        "clear_attachments",
        "load_draft",
    ]
    client_id: str = Field(default="", max_length=36)
    consent: bool = False
    message_id: str = Field(default="", max_length=512)
    draft_id: str = Field(default="", max_length=32)
    message: Message | None = None
    name: str = Field(default="", max_length=120)
    content: str = Field(default="", max_length=43692, repr=False)


class ElevenCommand(ToolModel):
    operation: Literal["voices", "set", "delete", "select", "speak"]
    key: str = Field(default="", max_length=4096, repr=False)
    voice_id: str = Field(default="", max_length=64)
    consent: bool = False


class Request(ToolModel):
    id: UUID
    operation: Literal[
        "status",
        "run",
        "cancel",
        "open",
        "configure",
        "respond",
        "voice_start",
        "voice_release",
        "voice_cancel",
        "voice_wake_resume",
        "voice_model",
        "speak_result",
        "memory",
        "mail",
        "eleven",
        "target_presented",
        "clipboard_sample",
        "watcher_action",
    ]
    sample_id: str = Field(default="", pattern=r"^(?:[a-f0-9]{32})?$")
    clipboard_text: str = Field(default="", max_length=16000, repr=False)
    notification_id: str = Field(default="", pattern=r"^(?:[a-f0-9]{32})?$")
    dismiss: bool = False
    command: str = Field(default="", max_length=4000, repr=False)
    mode: Literal["simulation", "execute"] = "simulation"
    panel: Literal["task", "settings", "memory", "outlook", "voice", "tools"] = "task"
    cloud_consent: bool = False
    memory_ids: tuple[str, ...] = Field(default=(), max_length=8)
    session_memory: bool = False
    memory_consent: bool = False
    conversation_consent: bool = False
    model_path: str = Field(default="", max_length=2000, repr=False)
    settings: Settings | None = None
    reply: Reply | None = None
    memory: MemoryEdit | None = None
    mail: MailCommand | None = None
    eleven: ElevenCommand | None = None
    target_id: str = Field(default="", pattern=r"^(?:[a-f0-9]{32})?$")

    @model_validator(mode="after")
    def scoped_fields(self) -> "Request":
        if (self.operation == "clipboard_sample") != bool(self.sample_id):
            raise ValueError("Clipboard receipt outside sampling.")
        if self.operation != "clipboard_sample" and self.clipboard_text:
            raise ValueError("Clipboard text outside sampling.")
        if (self.operation == "watcher_action") != bool(self.notification_id):
            raise ValueError("Notification outside action.")
        if self.operation != "watcher_action" and self.dismiss:
            raise ValueError("Dismiss outside notification.")
        if (self.operation == "target_presented") != bool(self.target_id):
            raise ValueError("Target receipt outside presentation.")
        if self.operation == "run":
            if not self.command.strip():
                raise ValueError("Empty task.")
        elif (
            self.command
            or self.cloud_consent
            or self.memory_ids
            or self.memory_consent
            or self.session_memory
            or self.conversation_consent
        ):
            raise ValueError("Task fields outside run.")
        if self.operation not in {"run", "voice_start", "mail"} and self.mode != "simulation":
            raise ValueError("Mode outside task.")
        if self.operation != "open" and self.panel != "task":
            raise ValueError("Panel outside open.")
        if (self.operation == "voice_model") != bool(self.model_path):
            raise ValueError("Model path outside selection.")
        for operation, value in (
            ("configure", self.settings),
            ("respond", self.reply),
            ("memory", self.memory),
            ("mail", self.mail),
            ("eleven", self.eleven),
        ):
            if (self.operation == operation) != (value is not None):
                raise ValueError("Payload outside its operation.")
        return self
