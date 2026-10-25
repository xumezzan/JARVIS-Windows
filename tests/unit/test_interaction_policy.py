"""Action-specific risk policy, exercised through the production permission gateway."""

import json
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from tests.unit.test_browser import BUTTON, FIELD, TARGET, Probe
from tests.unit.test_windows_cross_app import ELEMENT, RECT
from tests.windows_support import WindowsProbe, target

from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.interaction import requires_confirmation
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.tools.browser import register_browser
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import clipboard_digest, register_windows


@pytest.mark.parametrize(
    "label",
    [
        "Оплатить 100 ₽",
        "Удалить файл",
        "Отправить письмо",
        "Купить сейчас",
        "О&тправить",
        "ОТПРАВИТЬ",
        "От\u200bправить",
        "Подтвердить заказ",
        "Закрыть",
        "Закрыть без сохранения",
        "Pay now",
        "Delete permanently",
        "SEND",
        "Submit",
        "Buy now",
        "Purchase",
        "Checkout",
        "Discard changes",
        "sendButton",
        "delete_file",
        "Ｐａｙ",
        "Close",
        "Exit",
        "Quit",
        "Не сохранять",
        "Don't save",
        "×",
        "X",
    ],
)
def test_external_and_destructive_labels_require_confirmation(label: str) -> None:
    assert requires_confirmation(label)


@pytest.mark.parametrize("label", ["Search", "Next", "Поиск", "Открыть", "Select tab", "Copy"])
def test_navigation_labels_are_safe(label: str) -> None:
    assert not requires_confirmation(label)


CASES: list[tuple[str, object, Risk]] = [
    ("browser.open", {"url": TARGET.url}, Risk.SAFE),
    ("browser.search", {"query": "Jarvis"}, Risk.SAFE),
    ("browser.navigate", {"target": TARGET.model_dump(), "url": TARGET.url}, Risk.SAFE),
    ("browser.type", {"target": TARGET.model_dump(), "element": FIELD, "text": "x"}, Risk.SAFE),
    ("browser.close", {"target": TARGET.model_dump()}, Risk.SAFE),
    (
        "browser.close",
        {"target": TARGET.model_dump() | {"has_unsaved_changes": True}},
        Risk.CONFIRM,
    ),
    ("windows.type_text", {"target": target().model_dump(), "text": "x"}, Risk.SAFE),
    ("windows.focus_app", {"target": target().model_dump()}, Risk.SAFE),
    ("windows.clipboard_get", {}, Risk.SAFE),
    ("windows.clipboard_set", {"text": "x"}, Risk.SAFE),
    (
        "windows.click_element",
        {"target": target().model_dump(), "x": 20, "y": 30, "window_rectangle": RECT.model_dump()},
        Risk.CONFIRM,
    ),
]
for label, risk in (
    ("Next", Risk.SAFE),
    ("Search", Risk.SAFE),
    ("Поиск", Risk.SAFE),
    ("Оплатить", Risk.CONFIRM),
    ("Удалить", Risk.CONFIRM),
    ("Отправить", Risk.CONFIRM),
    ("Купить", Risk.CONFIRM),
    ("Close", Risk.CONFIRM),
):
    CASES.extend(
        [
            (
                "browser.click",
                {"target": TARGET.model_dump(), "element": BUTTON | {"name": label}},
                risk,
            ),
            (
                "windows.click_element",
                {
                    "target": target().model_dump(),
                    "element": ELEMENT.model_dump() | {"name": label},
                },
                risk,
            ),
        ]
    )
for key in ("Ctrl+C", "Ctrl+V", "Ctrl+A", "Tab", "Enter", "Alt+F4"):
    CASES.append(
        (
            "windows.send_hotkey",
            {
                "target": target().model_dump(),
                "hotkey": key,
                "clipboard_sha256": clipboard_digest("x"),
            },
            Risk.CONFIRM if key in ("Enter", "Alt+F4") else Risk.SAFE,
        )
    )


@pytest.mark.parametrize("name,arguments,risk", CASES)
@pytest.mark.asyncio
async def test_action_risk_and_unapproved_simulation(
    tmp_path: Path, name: str, arguments: object, risk: Risk
) -> None:
    browser = Probe()
    windows = WindowsProbe()
    registry = ToolRegistry()
    register_browser(registry, browser)
    register_windows(registry, windows)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, ApprovalStore(), audit)
        action = engine.prepare(name, arguments)
        assert isinstance(action, Action) and action.risk is risk
        outcome = await engine.execute(action)
        assert outcome.status is (Status.SIMULATED if risk is Risk.SAFE else Status.DENIED)
        if risk is Risk.CONFIRM:
            real = engine.prepare(name, arguments, Mode.EXECUTE)
            assert isinstance(real, Action)
            assert (await engine.execute(real)).status is Status.DENIED
        assert not browser.calls and not windows.calls
        records = [json.loads(record) for record in audit.recent()]
        assert all(record["risk"] == risk.value for record in records)
        assert {record["event"] for record in records} == {"prepared", "finished"}


@pytest.mark.asyncio
async def test_forging_sensitive_click_as_safe_cannot_bypass_approval(tmp_path: Path) -> None:
    probe = Probe()
    registry = ToolRegistry()
    register_browser(registry, probe)
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, ApprovalStore(), audit)
        action = engine.prepare(
            "browser.click", {"target": TARGET.model_dump(), "element": BUTTON}, Mode.EXECUTE
        )
        assert isinstance(action, Action) and action.risk is Risk.CONFIRM
        assert (await engine.execute(replace(action, risk=Risk.SAFE))).status is Status.DENIED
        assert not probe.calls
