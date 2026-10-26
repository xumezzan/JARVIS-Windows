"""Actual bounded OS observations for the desktop, through the permission engine."""

import os
import platform
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from jarvis.core.planner.contracts import PlannerInput, Proposal
from jarvis.core.planner.offline import OfflineProvider, call
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolError, ToolModel, ToolSpec
from jarvis.tools.local import LocalOutbox
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.scratchpad import register_scratchpad
from jarvis.tools.windows import chrome_url


def chrome_command(command: str) -> str | None:
    """Only an explicit, single URL navigation from the owner's command."""
    match = re.fullmatch(
        r"(?:(?:открой|открыть|запусти)\s+(?:браузер|chrome|хром)\s+и\s+)?"
        r"(?:открой|открыть|перейди|перейти)(?:\s+на)?(?:\s+сайт)?\s+"
        r"((?:https://)?[a-zA-Z0-9][^\s]*)",
        command.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return None
    value = match.group(1)
    if "." not in value or "://" in value and not value.lower().startswith("https://"):
        return None
    try:
        return chrome_url(value if value.lower().startswith("https://") else "https://" + value)
    except ToolError:
        return None


class ChromeCommandProvider:
    """A literal address needs no cloud planner or account/page observations."""

    def __init__(self, url: str) -> None:
        self.url = url

    async def propose(self, data: PlannerInput) -> Proposal:
        if data.steps:
            return Proposal(kind="finish")
        return call("windows.open_chrome_url", {"url": self.url})


class SystemInput(ToolModel):
    pass


class SystemInfo(ToolModel):
    system: str = Field(min_length=1, max_length=80)
    release: str = Field(min_length=1, max_length=100)
    architecture: str = Field(min_length=1, max_length=80)
    python: str = Field(min_length=1, max_length=30)
    cpu_count: int = Field(ge=1, le=65536)
    disk_total_bytes: int = Field(ge=0)
    disk_free_bytes: int = Field(ge=0)
    observed_at: str = Field(max_length=60)


def desktop_registry(directory: Path) -> tuple[ToolRegistry, LocalOutbox]:
    registry = ToolRegistry()
    register_scratchpad(registry)

    async def precondition(args: SystemInput, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return directory.is_dir()

    async def execute(args: SystemInput, context: ExecutionContext) -> SystemInfo:
        await context.checkpoint()
        disk = shutil.disk_usage(directory)
        return SystemInfo(
            system=platform.system(),
            release=platform.release(),
            architecture=platform.machine(),
            python=platform.python_version(),
            cpu_count=os.cpu_count() or 1,
            disk_total_bytes=disk.total,
            disk_free_bytes=disk.free,
            observed_at=datetime.now(UTC).isoformat(),
        )

    async def verify(args: SystemInput, result: SystemInfo, context: ExecutionContext) -> bool:
        await context.checkpoint()
        observed = datetime.fromisoformat(result.observed_at)
        return (
            result.system == platform.system()
            and result.architecture == platform.machine()
            and result.python == platform.python_version()
            and 0 <= result.disk_free_bytes <= result.disk_total_bytes
            and 0 <= (datetime.now(UTC) - observed).total_seconds() < 5
        )

    registry.register(
        ToolSpec(
            name="system.info",
            description="Сведения об ОС, архитектуре, Python, процессорах и свободном месте.",
            risk=Risk.SAFE,
            parameters=SystemInput,
            result=SystemInfo,
            preconditions=precondition,
            execute=execute,
            verify=verify,
        )
    )
    # Kept only to satisfy the existing window's internal type; no test outbox tool is registered.
    return registry, LocalOutbox()


class DesktopOfflineProvider:
    """Use the existing finite recipes, replacing the old teaching check with real OS data."""

    def __init__(self) -> None:
        self.provider = OfflineProvider()

    async def propose(self, data: PlannerInput) -> Proposal:
        result = await self.provider.propose(data)
        if result.kind == "call" and result.tool == "local.check":
            return result.model_copy(update={"tool": "system.info"})
        if result.kind == "clarify":
            return result.model_copy(
                update={"question": result.question.replace("учебные", "локальные")}
            )
        return result
