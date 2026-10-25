"""The typed edge of an MCP call: a small flat argument map in, bounded text out.

A server's own JSON Schema is never compiled into a model here. Building types out of a
value supplied by a service would hand the service the shape of the tool, and the shape is
what the permission engine snapshots and the owner approves. Instead every MCP tool takes
the same strict, flat map, and which keys it may hold comes from the reviewed manifest -
checked by a pure policy hook, so simulation refuses exactly what execution would.
"""

import math
from typing import Literal

from pydantic import Field, field_validator

from jarvis.tools.base import ToolModel

MAX_ARGUMENTS = 16
MAX_VALUE = 2000
MAX_PAYLOAD = 8000
MAX_DATA = 16000

Value = str | int | float | bool


class McpArguments(ToolModel):
    arguments: dict[str, Value] = Field(default_factory=dict, max_length=MAX_ARGUMENTS)

    @field_validator("arguments")
    @classmethod
    def bounded(cls, values: dict[str, Value]) -> dict[str, Value]:
        size = 0
        for name, value in values.items():
            if not name or len(name) > 64 or not name.replace("_", "a").isalnum():
                raise ValueError("An argument name is a short identifier.")
            if isinstance(value, str):
                if len(value) > MAX_VALUE or any(ord(char) in (0, 127) for char in value):
                    raise ValueError("An argument value is short printable text.")
                size += len(value)
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("An argument number is finite.")
            size += len(name)
        if size > MAX_PAYLOAD:
            raise ValueError("The arguments are too large for one call.")
        return values


class McpResult(ToolModel):
    """What the server answered, as data. Bounded, text only, and never an instruction."""

    tool: str = Field(max_length=80)
    state: Literal["answered"] = "answered"
    data: str = Field(default="", max_length=MAX_DATA, repr=False)
    truncated: bool = False
