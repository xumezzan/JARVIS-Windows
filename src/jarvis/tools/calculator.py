"""A single numeric calculator input, observed and approved before its one UIA invocation."""

from pydantic import Field

from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import (
    CalculatorTarget,
    NativeRequest,
    WindowsBackend,
    WindowsResult,
    WindowTarget,
    text_digest,
)


class ObserveCalculator(ToolModel):
    target: WindowTarget
    digit: str = Field(pattern=r"^[0-9]$")


class EnterCalculatorDigit(ToolModel):
    target: WindowTarget
    calculator: CalculatorTarget


def register_calculator(registry: ToolRegistry, backend: WindowsBackend) -> None:
    async def observe_check(args: ObserveCalculator, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return True

    async def observe(args: ObserveCalculator, context: ExecutionContext) -> WindowsResult:
        return await backend.call(
            NativeRequest(operation="calculator_observe", target=args.target, digit=args.digit),
            context,
        )

    async def observed(
        args: ObserveCalculator, result: WindowsResult, context: ExecutionContext
    ) -> bool:
        await context.checkpoint()
        return (
            result.target == args.target
            and result.calculator is not None
            and result.calculator.digit == args.digit
        )

    async def ready(args: EnterCalculatorDigit, context: ExecutionContext) -> bool:
        await backend.call(
            NativeRequest(
                operation="calculator_check", target=args.target, calculator=args.calculator
            ),
            context,
        )
        return True

    async def enter(args: EnterCalculatorDigit, context: ExecutionContext) -> WindowsResult:
        return await backend.call(
            NativeRequest(
                operation="calculator_digit", target=args.target, calculator=args.calculator
            ),
            context,
        )

    async def verified(
        args: EnterCalculatorDigit, result: WindowsResult, context: ExecutionContext
    ) -> bool:
        expected = text_digest(args.calculator.digit)
        if result.target != args.target or result.calculator_value_sha256 != expected:
            return False
        checked = await backend.call(
            NativeRequest(
                operation="calculator_verify", target=args.target, calculator=args.calculator
            ),
            context,
        )
        return checked.calculator_value_sha256 == expected

    cancellation = "The helper is killed on stop; an issued digit is never automatically retried."
    registry.register(
        ToolSpec(
            "windows.get_calculator_digit",
            "Найти цифровую кнопку и нулевой дисплей обычного калькулятора Windows.",
            Risk.SAFE,
            ObserveCalculator,
            WindowsResult,
            observe_check,
            observe,
            observed,
            timeout_seconds=30,
            cancellation=cancellation,
        )
    )
    registry.register(
        ToolSpec(
            "windows.calculator_digit",
            "Ввести одну цифру в наблюдаемый пустой калькулятор Windows и проверить дисплей.",
            Risk.CONFIRM,
            EnterCalculatorDigit,
            WindowsResult,
            ready,
            enter,
            verified,
            timeout_seconds=30,
            cancellation=cancellation,
        )
    )
