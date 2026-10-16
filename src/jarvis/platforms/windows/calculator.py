"""Microsoft Calculator digit controls, never global keys or arbitrary UIA selectors."""

import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis.tools.base import ToolError
from jarvis.tools.windows import (
    CalculatorElement,
    CalculatorTarget,
    NativeRequest,
    WindowsResult,
    WindowTarget,
    text_digest,
)

if TYPE_CHECKING:
    from jarvis.platforms.windows.native import NativeDesktop


def element(desktop: "NativeDesktop", control: Any) -> CalculatorElement:
    return CalculatorElement(
        runtime_id=desktop.runtime(control),
        automation_id=str(control.element_info.automation_id),
        control_type=control.element_info.control_type,
        class_name=str(control.element_info.class_name),
        name=str(control.element_info.name),
        visible=bool(control.is_visible()),
    )


def snapshot(
    desktop: "NativeDesktop", target: WindowTarget, digit: str
) -> tuple[CalculatorTarget, Any]:
    window = desktop.resolve(target)
    controls = window.descendants()

    def one(identifier: str, role: str, *, hidden_text: bool = False) -> Any:
        matches = [
            control
            for control in controls
            if control.element_info.automation_id == identifier
            and control.element_info.control_type == role
            and (hidden_text or control.is_visible())
            and control.is_enabled()
        ]
        if len(matches) != 1:
            raise ToolError("control_unsupported")
        return matches[0]

    one("StandardFunctions", "Group")
    button = one(f"num{digit}Button", "Button")
    display = one("CalculatorResults", "Text")
    # Its accessibility identity remains present while the empty expression is hidden.
    expression = one("CalculatorExpression", "Text", hidden_text=True)
    process = desktop.psutil.Process(button.element_info.process_id)
    executable = Path(process.exe()).resolve()
    apps = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "WindowsApps"
    # ApplicationFrameHost may own the top-level window; the controls must belong to the
    # actual protected Microsoft package, and retain that process's complete identity.
    if (
        executable.name.casefold() != "calculatorapp.exe"
        or executable.parent.parent != apps.resolve()
        or not re.fullmatch(
            r"Microsoft\.WindowsCalculator_[0-9.]+_(?:x64|x86|arm64)__8wekyb3d8bbwe",
            executable.parent.name,
        )
        or any(c.element_info.process_id != process.pid for c in (display, expression))
    ):
        raise ToolError("control_unsupported")
    return CalculatorTarget(
        digit=digit,
        pid=process.pid,
        process_started=float(process.create_time()),
        executable=str(executable),
        button=element(desktop, button),
        display=element(desktop, display),
        expression=element(desktop, expression),
    ), button


def zero_prefix(observed: CalculatorTarget) -> str:
    match = re.fullmatch(r"([^0-9]*)0", observed.display.name)
    if match is None or re.search(r"[0-9+=*/×÷]", observed.expression.name):
        # Never clear the owner's existing calculation or append to unseen content.
        raise ToolError("target_changed")
    return match.group(1)


def dispatch(desktop: "NativeDesktop", request: NativeRequest) -> WindowsResult:
    if request.target is None:
        raise ToolError("native_failure")
    target = request.target
    if request.operation == "calculator_observe":
        if request.digit is None:
            raise ToolError("native_failure")
        found, _ = snapshot(desktop, target, request.digit)
        zero_prefix(found)
        return WindowsResult(target=target, calculator=found)
    observed = request.calculator
    if observed is None:
        raise ToolError("native_failure")
    prefix = zero_prefix(observed)
    if request.operation in ("calculator_check", "calculator_digit"):
        current, button = snapshot(desktop, target, observed.digit)
        if current != observed:
            raise ToolError("target_changed")
        if request.operation == "calculator_check":
            return WindowsResult(target=target)
        # Exactly one invocation on the re-observed digit button. Never Clear or Equals.
        button.iface_invoke.Invoke()
    expected = observed.model_copy(
        update={"display": observed.display.model_copy(update={"name": prefix + observed.digit})}
    )
    deadline = time.monotonic() + 2
    while True:
        current, _ = snapshot(desktop, target, observed.digit)
        if current == expected:
            return WindowsResult(target=target, calculator_value_sha256=text_digest(observed.digit))
        if request.operation != "calculator_digit" or time.monotonic() >= deadline:
            raise ToolError("target_changed")
        time.sleep(0.05)
