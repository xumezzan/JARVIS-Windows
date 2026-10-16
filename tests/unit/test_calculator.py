"""Calculator input stays bound to a fresh observation and one approved digit invocation."""

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
    return desktop, items


def observation(desktop: Mock) -> CalculatorTarget:
    result = calculator.dispatch(
        desktop, NativeRequest(operation="calculator_observe", target=target(), digit="1")
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
        desktop, NativeRequest(operation="calculator_digit", target=target(), calculator=observed)
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
            NativeRequest(operation="calculator_digit", target=target(), calculator=observed),
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
            "windows.calculator_digit",
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
                "windows.get_calculator_digit",
                Outcome(
                    uuid4(),
                    Status.SUCCESS,
                    result_json=WindowsResult(target=target(), calculator=state).model_dump_json(),
                ),
            )
        )
        assert runner._observed_target(action)
        forged = state.model_copy(
            update={"button": state.button.model_copy(update={"name": "Other"})}
        )
        changed = engine.prepare(
            "windows.calculator_digit",
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
            NativeRequest(operation="calculator_digit", target=target(), calculator=observed),
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
        action = engine.prepare("windows.calculator_digit", args, Mode.EXECUTE)
        assert isinstance(action, Action) and action.risk is Risk.CONFIRM
        assert (await engine.execute(action)).status is Status.DENIED
        backend.call.assert_not_called()
        simulation = engine.prepare("windows.calculator_digit", args, Mode.SIMULATION)
        assert isinstance(simulation, Action)
        assert (
            await engine.execute(simulation, authority.approve(simulation))
        ).status is Status.SIMULATED
        backend.call.assert_not_called()
        approved = engine.prepare("windows.calculator_digit", args, Mode.EXECUTE)
        assert isinstance(approved, Action)
        token = authority.approve(approved)
        assert (await engine.execute(approved, token)).status is Status.SUCCESS
        assert [call.args[0].operation for call in backend.call.call_args_list] == [
            "calculator_check",
            "calculator_digit",
            "calculator_verify",
        ]
        assert (await engine.execute(approved, token)).status is Status.INVALID


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["набери", "введи", "напиши", "напечатай", "нажми"])
async def test_offline_recipe_uses_observed_calculator_controls(
    controls: tuple[Mock, list[Mock]], verb: str
) -> None:
    command = f"открой калькулятор и {verb} 1"
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
        "tool": "windows.get_calculator_digit",
        "args": {"target": target().model_dump(), "digit": "1"},
    }
    state = observation(controls[0])
    steps.append(
        Step(
            "windows.get_calculator_digit",
            Outcome(
                uuid4(),
                Status.SUCCESS,
                result_json=WindowsResult(target=target(), calculator=state).model_dump_json(),
            ),
        )
    )
    assert await propose() == {
        "tool": "windows.calculator_digit",
        "args": {"target": target().model_dump(), "calculator": state.model_dump()},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "открой калькулятор и набери 12",
        "открой калькулятор и напиши 12",
        "открой браузер и нажми кнопку",
        "запусти код и закрой окно",
    ],
)
async def test_unsupported_chains_never_become_application_names(command: str) -> None:
    proposal = await OfflineProvider().propose(PlannerInput(command, (), (), Mode.EXECUTE, "[]"))
    assert proposal.kind == "clarify"
