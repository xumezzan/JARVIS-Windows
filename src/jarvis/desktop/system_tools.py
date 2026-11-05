"""Actual bounded OS observations for the desktop, through the permission engine."""

import os
import platform
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.local import LocalOutbox
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.scratchpad import register_scratchpad


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
