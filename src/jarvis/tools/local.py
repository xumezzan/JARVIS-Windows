"""Allowlisted local verification tools. No network, files, shell, or external sending."""

import asyncio
from dataclasses import dataclass, field
from threading import Lock
from typing import Literal
from uuid import uuid4

from pydantic import Field

from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec, canonical
from jarvis.tools.registry import ToolRegistry


class CheckInput(ToolModel):
    delay_ms: int = Field(default=100, ge=0, le=10000)
    fail: bool = False


class CheckResult(ToolModel):
    completed: bool


class Attachment(ToolModel):
    name: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class MessageInput(ToolModel):
    service: Literal["local-test-outbox"] = "local-test-outbox"
    action_type: Literal["append_test_message"] = "append_test_message"
    account: str = Field(default="test-account", min_length=1, max_length=200)
    recipient: str = Field(min_length=1, max_length=200)
    subject: str = Field(default="Тест разрешений", max_length=500)
    body: str = Field(min_length=1, max_length=4000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)


class MessageReceipt(ToolModel):
    receipt_id: str


@dataclass
class LocalOutbox:
    _messages: dict[str, str] = field(default_factory=dict, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._messages)

    def matches(self, receipt_id: str, payload: str) -> bool:
        with self._lock:
            return self._messages.get(receipt_id) == payload

    async def append(self, args: MessageInput, context: ExecutionContext) -> MessageReceipt:
        await asyncio.sleep(0.1)
        await context.checkpoint()
        receipt = uuid4().hex
        with self._lock:
            self._messages[receipt] = canonical(args)
        return MessageReceipt(receipt_id=receipt)

    async def verify(
        self,
        args: MessageInput,
        result: MessageReceipt,
        context: ExecutionContext,
    ) -> bool:
        await context.checkpoint()
        return self.matches(result.receipt_id, canonical(args))


async def check_preconditions(args: CheckInput, context: ExecutionContext) -> bool:
    await context.checkpoint()
    return True


async def check_execute(args: CheckInput, context: ExecutionContext) -> CheckResult:
    await asyncio.sleep(args.delay_ms / 1000)
    await context.checkpoint()
    if args.fail:
        raise RuntimeError("Test failure.")
    return CheckResult(completed=True)


async def check_verify(args: CheckInput, result: CheckResult, context: ExecutionContext) -> bool:
    await context.checkpoint()
    return result.completed


async def message_preconditions(args: MessageInput, context: ExecutionContext) -> bool:
    await context.checkpoint()
    return bool(args.recipient.strip()) and bool(args.body.strip())


def local_registry() -> tuple[ToolRegistry, LocalOutbox]:
    registry = ToolRegistry()
    outbox = LocalOutbox()
    for name, risk in (
        ("local.check", Risk.SAFE),
        ("local.critical_test", Risk.CRITICAL),
        ("local.blocked_test", Risk.BLOCKED),
    ):
        registry.register(
            ToolSpec(
                name,
                "Локальная проверка без внешних действий.",
                risk,
                CheckInput,
                CheckResult,
                check_preconditions,
                check_execute,
                check_verify,
                timeout_seconds=2,
            )
        )
    registry.register(
        ToolSpec(
            "local.append_message",
            "Добавить тестовое сообщение только в память процесса.",
            Risk.CONFIRM,
            MessageInput,
            MessageReceipt,
            message_preconditions,
            outbox.append,
            outbox.verify,
        )
    )
    return registry, outbox
