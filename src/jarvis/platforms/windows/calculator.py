"""Microsoft Calculator digit controls, never global keys or arbitrary UIA selectors."""

import locale
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
    desktop: "NativeDesktop", target: WindowTarget, number: str
) -> tuple[CalculatorTarget, dict[str, Any]]:
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
    buttons = {digit: one(f"num{digit}Button", "Button") for digit in sorted(set(number))}
    display = one("CalculatorResults", "Text")
    # Its accessibility identity remains present while the empty expression is hidden.
    expression = one("CalculatorExpression", "Text", hidden_text=True)
    process = desktop.psutil.Process(next(iter(buttons.values())).element_info.process_id)
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
        or any(
            c.element_info.process_id != process.pid
            for c in (*buttons.values(), display, expression)
        )
    ):
        raise ToolError("control_unsupported")
    return CalculatorTarget(
        number=number,
        pid=process.pid,
        process_started=float(process.create_time()),
        executable=str(executable),
        buttons=[element(desktop, button) for button in buttons.values()],
        display=element(desktop, display),
        expression=element(desktop, expression),
    ), buttons


def zero_prefix(observed: CalculatorTarget) -> str:
    match = re.fullmatch(r"([^0-9]*)0", observed.display.name)
    if match is None or re.search(r"[0-9+=*/×÷]", observed.expression.name):
        # Never clear the owner's existing calculation or append to unseen content.
        raise ToolError("target_changed")
    return match.group(1)


def formatted_number(number: str) -> str:
    """Use this helper process's user number format; never guess decimal separators."""
    previous = locale.setlocale(locale.LC_NUMERIC)
    try:
        locale.setlocale(locale.LC_NUMERIC, "")
        return locale.format_string("%d", int(number), grouping=True)
    finally:
        locale.setlocale(locale.LC_NUMERIC, previous)


def matches_value(
    observed: CalculatorTarget, current: CalculatorTarget, prefix: str, number: str
) -> bool:
    # Every identity and state except the expected display text must still match.
    same = current.model_copy(
        update={"display": current.display.model_copy(update={"name": observed.display.name})}
    )
    if same != observed or not current.display.name.startswith(prefix):
        return False
    value = current.display.name[len(prefix) :]
    # Calculator and the Windows locale can use different nonbreaking space variants.
    spaces = str.maketrans({"\u00a0": " ", "\u202f": " "})
    return value.translate(spaces) in (
        str(int(number)),
        formatted_number(number).translate(spaces),
    )


def dispatch(desktop: "NativeDesktop", request: NativeRequest) -> WindowsResult:
    if request.target is None:
        raise ToolError("native_failure")
    target = request.target
    if request.operation == "calculator_observe":
        if request.number is None:
            raise ToolError("native_failure")
        found, _ = snapshot(desktop, target, request.number)
        zero_prefix(found)
        return WindowsResult(target=target, calculator=found)
    observed = request.calculator
    if observed is None:
        raise ToolError("native_failure")
    prefix = zero_prefix(observed)
    if request.operation in ("calculator_check", "calculator_number"):
        current, buttons = snapshot(desktop, target, observed.number)
        if current != observed:
            raise ToolError("target_changed")
        if request.operation == "calculator_check":
            return WindowsResult(target=target)
        entered = ""
        for digit in observed.number:
            fresh, buttons = snapshot(desktop, target, observed.number)
            if fresh != current:
                raise ToolError("target_changed")
            # One Invoke per requested digit, never Clear/Equals, no replay after uncertainty.
            buttons[digit].iface_invoke.Invoke()
            entered += digit
            deadline = time.monotonic() + 1
            while True:
                after, _ = snapshot(desktop, target, observed.number)
                if matches_value(observed, after, prefix, entered):
                    current = after
                    break
                if after != current or time.monotonic() >= deadline:
                    raise ToolError("target_changed")
                time.sleep(0.05)
    # Re-observe independently even after the final per-digit read-back.
    final, _ = snapshot(desktop, target, observed.number)
    if not matches_value(observed, final, prefix, observed.number):
        raise ToolError("target_changed")
    return WindowsResult(
        target=target, calculator_value_sha256=text_digest(str(int(observed.number)))
    )
