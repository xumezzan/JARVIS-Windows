"""Validated task-local notes; Runner commits verified updates to its own RAM dictionary."""

from pydantic import Field

from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry

MAX_SCRATCHPAD_BYTES = 32768
MAX_SCRATCHPAD_KEYS = 32


class ScratchpadWrite(ToolModel):
    key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    value: str = Field(max_length=8192, repr=False)


def register_scratchpad(registry: ToolRegistry) -> None:
    async def check(args: ScratchpadWrite, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return True

    async def execute(args: ScratchpadWrite, context: ExecutionContext) -> ScratchpadWrite:
        await context.checkpoint()
        return args

    async def verify(
        args: ScratchpadWrite, result: ScratchpadWrite, context: ExecutionContext
    ) -> bool:
        await context.checkpoint()
        return args == result

    registry.register(
        ToolSpec(
            "memory.scratchpad_write",
            "Set a task-local scratchpad key to a text value (numbers as text). "
            "Overwrites the key; available in every following planner input. "
            "Cleared from Runner at task end; desktop memory may retain successful final notes. "
            "Never store approval authority or credentials.",
            Risk.SAFE,
            ScratchpadWrite,
            ScratchpadWrite,
            check,
            execute,
            verify,
        )
    )
