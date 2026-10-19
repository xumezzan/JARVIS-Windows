"""A bounded calculator number, observed and approved before any numeric button invocation."""

from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext, ToolModel, ToolSpec
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import (
    CalculatorNumber,
    CalculatorTarget,
    NativeRequest,
    WindowsBackend,
    WindowsResult,
    WindowTarget,
    text_digest,
)


class ObserveCalculator(ToolModel):
    target: WindowTarget
    number: CalculatorNumber


class EnterCalculatorNumber(ToolModel):
    target: WindowTarget
    calculator: CalculatorTarget


def register_calculator(registry: ToolRegistry, backend: WindowsBackend) -> None:
    async def observe_check(args: ObserveCalculator, context: ExecutionContext) -> bool:
        await context.checkpoint()
        return True

    async def observe(args: ObserveCalculator, context: ExecutionContext) -> WindowsResult:
        return await backend.call(
            NativeRequest(operation="calculator_observe", target=args.target, number=args.number),
            context,
        )

    async def observed(
        args: ObserveCalculator, result: WindowsResult, context: ExecutionContext
    ) -> bool:
        await context.checkpoint()
        return (
            result.target == args.target
            and result.calculator is not None
            and result.calculator.number == args.number
        )

    async def ready(args: EnterCalculatorNumber, context: ExecutionContext) -> bool:
        await backend.call(
            NativeRequest(
                operation="calculator_check", target=args.target, calculator=args.calculator
            ),
            context,
        )
        return True

    async def enter(args: EnterCalculatorNumber, context: ExecutionContext) -> WindowsResult:
        return await backend.call(
            NativeRequest(
                operation="calculator_number", target=args.target, calculator=args.calculator
            ),
            context,
        )

    async def verified(
        args: EnterCalculatorNumber, result: WindowsResult, context: ExecutionContext
    ) -> bool:
        expected = text_digest(str(int(args.calculator.number)))
        if result.target != args.target or result.calculator_value_sha256 != expected:
            return False
        checked = await backend.call(
            NativeRequest(
                operation="calculator_verify", target=args.target, calculator=args.calculator
            ),
            context,
        )
        return checked.calculator_value_sha256 == expected

    cancellation = "The helper is killed on stop; a partially entered number is never retried."
    registry.register(
        ToolSpec(
            "windows.get_calculator_number",
            "Найти цифровые кнопки и нулевой дисплей обычного калькулятора Windows.",
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
            "windows.calculator_number",
            "Ввести целое число до 15 цифр в наблюдаемый пустой калькулятор и проверить дисплей.",
            Risk.CONFIRM,
            EnterCalculatorNumber,
            WindowsResult,
            ready,
            enter,
            verified,
            timeout_seconds=30,
            cancellation=cancellation,
        )
    )
