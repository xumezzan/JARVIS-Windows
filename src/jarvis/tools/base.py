"""Typed adapter contracts; adapters are trusted code, not a code sandbox."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from threading import Event
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict

from jarvis.permissions.policies import Risk


class ToolError(Exception):
    """Finite adapter failures; never transport native exception text to UI or audit."""

    def __init__(
        self,
        code: Literal[
            "unsupported_platform",
            "application_missing",
            "target_changed",
            "control_unsupported",
            "native_timeout",
            "native_failure",
            "network_denied",
            "page_changed",
            "browser_unavailable",
            "browser_timeout",
            "browser_failure",
            "browser_cleanup",
        ],
    ) -> None:
        self.code = code
        super().__init__(code)


class ToolModel(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", frozen=True, revalidate_instances="always"
    )


def canonical(model: ToolModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


@dataclass(frozen=True)
class ExecutionContext:
    cancelled: Event

    async def checkpoint(self) -> None:
        await asyncio.sleep(0)
        if self.cancelled.is_set():
            raise asyncio.CancelledError


P = TypeVar("P", bound=ToolModel)
R = TypeVar("R", bound=ToolModel)


@dataclass(frozen=True)
class ToolSpec[Params: ToolModel, Result: ToolModel]:
    name: str
    description: str
    risk: Risk
    parameters: type[Params]
    result: type[Result]
    preconditions: Callable[[Params, ExecutionContext], Awaitable[bool]]
    execute: Callable[[Params, ExecutionContext], Awaitable[Result]]
    verify: Callable[[Params, Result, ExecutionContext], Awaitable[bool]]
    timeout_seconds: float = 5.0
    cancellation: str = "Cooperative asyncio cancellation; no rollback claim."
    idempotency: str = "No automatic retry; one execution per prepared request."
    policy: Callable[[Params], None] | None = None  # Pure, synchronous; also runs in simulation.
