"""A whole calculator number is bound to one observation, approval and verified digit sequence."""

import json
import os
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from tests.windows_support import target

from jarvis.core.planner.contracts import PlannerInput, Step
from jarvis.core.planner.offline import OfflineProvider
from jarvis.core.planner.runner import Runner
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.platforms.windows import calculator
from jarvis.tools.base import ToolError
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import (
    CalculatorTarget,
    NativeRequest,
    WindowsResult,
    register_windows,
    text_digest,
)


@pytest.fixture
def controls(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, list[Mock]]:
    desktop = Mock()
    items: list[Mock] = []
    for identifier, role, name in (
        ("StandardFunctions", "Group", "Standard functions"),
        ("num1Button", "Button", "One"),
        ("CalculatorResults", "Text", "Display is 0"),
        ("CalculatorExpression", "Text", "Expression is "),
        *((f"num{digit}Button", "Button", digit) for digit in "023456789"),
    ):
        control = Mock()
        control.element_info.automation_id = identifier
        control.element_info.control_type = role
        control.element_info.class_name = "Button" if role == "Button" else ""
        control.element_info.name = name
        control.element_info.process_id = 123
        control.is_visible.return_value = True
        control.is_enabled.return_value = True
        control.runtime = [42, len(items) + 1]
        items.append(control)
    desktop.runtime.side_effect = lambda control: control.runtime
    desktop.resolve.return_value.descendants.return_value = items
    monkeypatch.setenv("PROGRAMFILES", str(Path("C:/Program Files").resolve()))
    executable = (
        Path(os.environ["PROGRAMFILES"])
        / "WindowsApps"
        / "Microsoft.WindowsCalculator_11.2607.0.0_x64__8wekyb3d8bbwe"
        / "CalculatorApp.exe"
    )
    process = desktop.psutil.Process.return_value
    process.pid = 123
    process.exe.return_value = str(executable)
    process.create_time.return_value = 1000.0
    monkeypatch.setattr(calculator, "formatted_number", lambda number: str(int(number)))
    return desktop, items


def observation(desktop: Mock, number: str = "1") -> CalculatorTarget:
    result = calculator.dispatch(
        desktop, NativeRequest(operation="calculator_observe", target=target(), number=number)
    )
    assert result.calculator is not None
    return result.calculator


def test_observe_then_invoke_once_and_independently_verify(
    controls: tuple[Mock, list[Mock]],
) -> None:
    desktop, items = controls
    observed = observation(desktop)
    button, display = items[1:3]
    button.iface_invoke.Invoke.assert_not_called()
    button.iface_invoke.Invoke.side_effect = lambda: setattr(
        display.element_info, "name", "Display is 1"
    )
    result = calculator.dispatch(
        desktop, NativeRequest(operation="calculator_number", target=target(), calculator=observed)
    )
    assert result.calculator_value_sha256 == text_digest("1")
    checked = calculator.dispatch(
        desktop, NativeRequest(operation="calculator_verify", target=target(), calculator=observed)
    )
    assert checked.calculator_value_sha256 == text_digest("1")
    button.iface_invoke.Invoke.assert_called_once_with()
    # Replaying the observation after the digit has landed is refused, never pressed twice.
    with pytest.raises(ToolError, match="target_changed"):
        calculator.dispatch(
            desktop,
            NativeRequest(operation="calculator_number", target=target(), calculator=observed),
        )
    button.iface_invoke.Invoke.assert_called_once_with()


def test_empty_expression_can_be_hidden(controls: tuple[Mock, list[Mock]]) -> None:
    desktop, items = controls
    items[3].is_visible.return_value = False
    assert not observation(desktop).expression.visible


@pytest.mark.asyncio
async def test_button_must_come_from_the_same_task_observation(
    controls: tuple[Mock, list[Mock]], tmp_path: Path
) -> None:
    state = observation(controls[0])
    registry = ToolRegistry()
    register_windows(registry, Mock())
    store = ApprovalStore()
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, store, audit)
        runner = Runner(registry, engine, OfflineProvider(), AsyncMock(), AsyncMock())
        action = engine.prepare(
            "windows.calculator_number",
            {"target": target().model_dump(), "calculator": state.model_dump()},
            Mode.EXECUTE,
        )
        assert isinstance(action, Action)
        runner.steps.append(
            Step(
                "windows.open_app",
                Outcome(
                    uuid4(),
                    Status.SUCCESS,
                    result_json=WindowsResult(target=target()).model_dump_json(),
                ),
            )
        )
        assert not runner._observed_target(action)
        runner.steps.append(
            Step(
                "windows.get_calculator_number",
                Outcome(
                    uuid4(),
                    Status.SUCCESS,
                    result_json=WindowsResult(target=target(), calculator=state).model_dump_json(),
                ),
            )
        )
        assert runner._observed_target(action)
        forged = state.model_copy(
            update={"buttons": [state.buttons[0].model_copy(update={"name": "Other"})]}
        )
        changed = engine.prepare(
            "windows.calculator_number",
            {"target": target().model_dump(), "calculator": forged.model_dump()},
            Mode.EXECUTE,
        )
        assert isinstance(changed, Action) and not runner._observed_target(changed)


@pytest.mark.parametrize("change", ["value", "expression", "button", "process", "mode"])
def test_changed_state_never_invokes(controls: tuple[Mock, list[Mock]], change: str) -> None:
    desktop, items = controls
    observed = observation(desktop)
    if change == "value":
        items[2].element_info.name = "Display is 5"
    elif change == "expression":
        items[3].element_info.name = "Expression is 7 +"
    elif change == "button":
        items[1].runtime = [99]
    elif change == "process":
        desktop.psutil.Process.return_value.create_time.return_value = 1001.0
    else:
        items[0].element_info.automation_id = "ScientificFunctions"
    with pytest.raises(ToolError):
        calculator.dispatch(
            desktop,
            NativeRequest(operation="calculator_number", target=target(), calculator=observed),
        )
    items[1].iface_invoke.Invoke.assert_not_called()


@pytest.mark.parametrize("change", ["value", "package", "duplicate", "owner"])
def test_unusable_calculator_is_not_offered(controls: tuple[Mock, list[Mock]], change: str) -> None:
    desktop, items = controls
    if change == "value":
        items[2].element_info.name = "Display is 10"
    elif change == "package":
        desktop.psutil.Process.return_value.exe.return_value = "C:/untrusted/CalculatorApp.exe"
    elif change == "duplicate":
        items.append(items[1])
    else:
        items[2].element_info.process_id = 999
    with pytest.raises(ToolError):
        observation(desktop)
    items[1].iface_invoke.Invoke.assert_not_called()


@pytest.mark.asyncio
async def test_effect_requires_approval_and_simulation_calls_nothing(
    controls: tuple[Mock, list[Mock]], tmp_path: Path
) -> None:
    observed = observation(controls[0])
    backend = Mock()
    backend.call = AsyncMock(
        return_value=WindowsResult(target=target(), calculator_value_sha256=text_digest("1"))
    )
    registry = ToolRegistry()
    register_windows(registry, backend)
    store = ApprovalStore()
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, store, audit)
        authority = store.take_authority(audit.approved)
        args = {"target": target().model_dump(), "calculator": observed.model_dump()}
        action = engine.prepare("windows.calculator_number", args, Mode.EXECUTE)
        assert isinstance(action, Action) and action.risk is Risk.CONFIRM
        assert (await engine.execute(action)).status is Status.DENIED
        backend.call.assert_not_called()
        simulation = engine.prepare("windows.calculator_number", args, Mode.SIMULATION)
        assert isinstance(simulation, Action)
        assert (
            await engine.execute(simulation, authority.approve(simulation))
        ).status is Status.SIMULATED
        backend.call.assert_not_called()
        approved = engine.prepare("windows.calculator_number", args, Mode.EXECUTE)
        assert isinstance(approved, Action)
        token = authority.approve(approved)
        assert (await engine.execute(approved, token)).status is Status.SUCCESS
        assert [call.args[0].operation for call in backend.call.call_args_list] == [
            "calculator_check",
            "calculator_number",
            "calculator_verify",
        ]
        assert (await engine.execute(approved, token)).status is Status.INVALID


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["набери", "введи", "напиши", "напечатай", "нажми"])
@pytest.mark.parametrize("number", ["1", "26", "102202", "999999999999999"])
async def test_offline_recipe_uses_observed_calculator_controls(
    controls: tuple[Mock, list[Mock]], verb: str, number: str
) -> None:
    command = f"открой калькулятор и {verb} {number}"
    provider = OfflineProvider()
    steps: list[Step] = []

    async def propose() -> dict[str, object]:
        proposal = await provider.propose(
            PlannerInput(command, (), tuple(steps), Mode.EXECUTE, "[]")
        )
        return {"tool": proposal.tool, "args": json.loads(proposal.arguments or "{}")}

    assert await propose() == {"tool": "windows.open_app", "args": {"app": "калькулятор"}}
    steps.append(
        Step(
            "windows.open_app",
            Outcome(
                uuid4(),
                Status.SUCCESS,
                result_json=WindowsResult(target=target()).model_dump_json(),
            ),
        )
    )
    assert await propose() == {
        "tool": "windows.get_calculator_number",
        "args": {"target": target().model_dump(), "number": number},
    }
    state = observation(controls[0], number)
    steps.append(
        Step(
            "windows.get_calculator_number",
            Outcome(
                uuid4(),
                Status.SUCCESS,
                result_json=WindowsResult(target=target(), calculator=state).model_dump_json(),
            ),
        )
    )
    assert await propose() == {
        "tool": "windows.calculator_number",
        "args": {"target": target().model_dump(), "calculator": state.model_dump()},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "открой калькулятор и набери 1234567890123456",
        "открой калькулятор и напиши 2+6",
        "открой калькулятор и набери -26",
        "открой калькулятор и набери 2,6",
        "открой браузер и нажми кнопку",
        "запусти код и закрой окно",
    ],
)
async def test_unsupported_chains_never_become_application_names(command: str) -> None:
    proposal = await OfflineProvider().propose(PlannerInput(command, (), (), Mode.EXECUTE, "[]"))
    assert proposal.kind == "clarify"


@pytest.mark.parametrize("number", ["26", "101", "00026", "0", "999999999999999"])
def test_whole_number_is_entered_once_in_order_and_read_back(
    controls: tuple[Mock, list[Mock]], number: str
) -> None:
    desktop, items = controls
    observed = observation(desktop, number)
    pressed: list[str] = []
    for button in items:
        identifier = button.element_info.automation_id
        if identifier.startswith("num"):
            digit = identifier[3]

            def press(value: str = digit) -> None:
                pressed.append(value)
                items[2].element_info.name = "Display is " + str(int("".join(pressed)))

            button.iface_invoke.Invoke.side_effect = press
    result = calculator.dispatch(
        desktop, NativeRequest(operation="calculator_number", target=target(), calculator=observed)
    )
    assert "".join(pressed) == number
    assert result.calculator_value_sha256 == text_digest(str(int(number)))
    assert calculator.dispatch(
        desktop, NativeRequest(operation="calculator_verify", target=target(), calculator=observed)
    ).calculator_value_sha256 == text_digest(str(int(number)))


@pytest.mark.parametrize("failure", ["wrong_display", "changed_button", "invoke_error", "timeout"])
def test_partial_entry_never_repeats_a_digit_or_continues_after_uncertainty(
    controls: tuple[Mock, list[Mock]], failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop, items = controls
    observed = observation(desktop, "26")
    buttons = {item.element_info.automation_id: item for item in items}

    def first() -> None:
        if failure == "invoke_error":
            raise ToolError("native_failure")
        if failure == "wrong_display":
            items[2].element_info.name = "Display is 9"
        elif failure == "changed_button":
            items[2].element_info.name = "Display is 2"
            buttons["num6Button"].runtime = [99]

    buttons["num2Button"].iface_invoke.Invoke.side_effect = first
    if failure == "timeout":
        monkeypatch.setattr(
            "jarvis.platforms.windows.calculator.time.monotonic", Mock(side_effect=[0, 2])
        )
    with pytest.raises(ToolError):
        calculator.dispatch(
            desktop,
            NativeRequest(operation="calculator_number", target=target(), calculator=observed),
        )
    buttons["num2Button"].iface_invoke.Invoke.assert_called_once_with()
    buttons["num6Button"].iface_invoke.Invoke.assert_not_called()


def test_readback_uses_user_grouping_without_removing_decimal_or_arithmetic_characters(
    controls: tuple[Mock, list[Mock]], monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop, items = controls
    observed = observation(desktop, "26000")
    monkeypatch.setattr(calculator, "formatted_number", lambda number: "26\u00a0000")
    items[2].element_info.name = "Display is 26\u202f000"
    assert calculator.dispatch(
        desktop, NativeRequest(operation="calculator_verify", target=target(), calculator=observed)
    ).calculator_value_sha256 == text_digest("26000")
    for value in ("26,000", "26.000", "26+000", "260000", "26 00 0"):
        items[2].element_info.name = "Display is " + value
        with pytest.raises(ToolError, match="target_changed"):
            calculator.dispatch(
                desktop,
                NativeRequest(operation="calculator_verify", target=target(), calculator=observed),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "открой калькулятор и введи число 26",
        "Открой калькулятор и набери 26.",
        "запусти calc и напечатай 26!",
    ],
)
async def test_number_phrasing_does_not_ask_an_unnecessary_question(command: str) -> None:
    proposal = await OfflineProvider().propose(PlannerInput(command, (), (), Mode.EXECUTE, "[]"))
    assert proposal.tool == "windows.open_app"


@pytest.mark.parametrize("number", ["", "-26", "2.6", "2,6", "2+6", "1\n", "١٢", "1" * 16])
def test_numeric_request_is_bounded_and_cannot_name_a_non_numeric_control(number: str) -> None:
    with pytest.raises(ValueError):
        NativeRequest(operation="calculator_observe", target=target(), number=number)


def test_locale_format_is_restored_after_grouping(monkeypatch: pytest.MonkeyPatch) -> None:
    set_locale = Mock(side_effect=["previous", "user", "restored"])
    monkeypatch.setattr("jarvis.platforms.windows.calculator.locale.setlocale", set_locale)
    monkeypatch.setattr(
        "jarvis.platforms.windows.calculator.locale.format_string", Mock(return_value="26 000")
    )
    assert calculator.formatted_number("26000") == "26 000"
    assert set_locale.call_args_list[-1].args[1] == "previous"
