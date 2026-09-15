"""Registration and schema discovery; no planner-facing execution method."""

import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, P, R, ToolModel, ToolSpec, canonical


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    risk: Risk
    parameters: type[ToolModel]
    result: type[ToolModel]
    timeout_seconds: float
    cancellation: str
    idempotency: str
    check: Callable[[str, ExecutionContext], Awaitable[bool]]
    run: Callable[[str, ExecutionContext], Awaitable[ToolModel]]
    verify: Callable[[str, ToolModel, ExecutionContext], Awaitable[bool]]

    def normalize(self, arguments: object) -> str:
        # Validate Python input before JSON serialization to reject coercion and extra fields.
        return canonical(self.parameters.model_validate(arguments, strict=True))


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._sealed = False

    def register(self, spec: ToolSpec[P, R]) -> None:
        if self._sealed:
            raise ValueError("Registry is sealed for execution.")
        if not re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", spec.name):
            raise ValueError("Invalid tool name.")
        if spec.name in self._tools:
            raise ValueError("Duplicate tool name.")
        if not isinstance(spec.risk, Risk):
            raise ValueError("Tool risk must be a trusted Risk value.")
        if not math.isfinite(spec.timeout_seconds) or not 0 < spec.timeout_seconds <= 60:
            raise ValueError("Tool timeout must be between 0 and 60 seconds.")

        async def check(payload: str, context: ExecutionContext) -> bool:
            return await spec.preconditions(spec.parameters.model_validate_json(payload), context)

        async def run(payload: str, context: ExecutionContext) -> ToolModel:
            result = await spec.execute(spec.parameters.model_validate_json(payload), context)
            return spec.result.model_validate(result, strict=True)

        async def verify(payload: str, result: ToolModel, context: ExecutionContext) -> bool:
            return await spec.verify(
                spec.parameters.model_validate_json(payload),
                spec.result.model_validate(result, strict=True),
                context,
            )

        self._tools[spec.name] = RegisteredTool(
            spec.name,
            spec.description,
            spec.risk,
            spec.parameters,
            spec.result,
            spec.timeout_seconds,
            spec.cancellation,
            spec.idempotency,
            check,
            run,
            verify,
        )

    def seal(self) -> None:
        self._sealed = True

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def discover(self) -> list[dict[str, object]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "risk": tool.risk.value,
                "parameters": tool.parameters.model_json_schema(),
                "result": tool.result.model_json_schema(),
                "timeout": tool.timeout_seconds,
                "cancellation": tool.cancellation,
                "idempotency": tool.idempotency,
            }
            for tool in self._tools.values()
        ]
