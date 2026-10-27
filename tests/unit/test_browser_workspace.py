"""Workspace contracts, per-tab grounding and pure policy; no real accounts or credentials."""

import asyncio
import json
import socket
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from tests.unit.test_browser import TARGET, Probe
from tests.unit.test_planner import Scripted
from tests.unit.test_workspace import custom_app

from jarvis.browser.host import BrowserHost
from jarvis.config import AppConfig, load_config
from jarvis.core.planner.contracts import Step
from jarvis.core.planner.offline import call
from jarvis.core.planner.runner import Runner
from jarvis.observability.audit import AuditLog, ErrorCode
from jarvis.permissions.approvals import Action, ApprovalStore
from jarvis.permissions.engine import Outcome, PermissionEngine
from jarvis.permissions.policies import Mode, Risk, Status
from jarvis.security.browser_policy import BrowserMode, NetworkPolicy
from jarvis.tools.base import ExecutionContext, ToolError, canonical
from jarvis.tools.browser import (
    BrowserCommand,
    BrowserResult,
    ClickElement,
    ElementTarget,
    PageTarget,
    PageView,
    TypeElement,
    register_browser,
)
from jarvis.tools.registry import ToolRegistry
from jarvis.workspace.models import AppEntry, WorkspaceProfile
from jarvis.workspace.presets import omadli_group_preset
from jarvis.workspace.store import WorkspaceStore


def profile() -> WorkspaceProfile:
    return WorkspaceProfile(
        apps=(*omadli_group_preset().apps, custom_app()),
        browser_mode="authenticated_workspace",
    )


def target(**changes: object) -> PageTarget:
    return PageTarget.model_validate(
        TARGET.model_dump()
        | {
            "url": "https://erp.example.com/",
            "origin": "https://erp.example.com",
            "observation_id": "f" * 32,
        }
        | changes
    )


def test_workspace_startup_and_cdp_alias_do_not_touch_a_profile(tmp_path: Path) -> None:
    WorkspaceStore(tmp_path).save(profile())
    config = load_config({"JARVIS_DATA_DIR": str(tmp_path)})
    host = BrowserHost.from_config(config)
    assert host.policy.mode is BrowserMode.WORKSPACE
    assert host.profile_dir == tmp_path / "browser_profile"
    assert not host.headless and not host.profile_dir.exists() and host._thread is None
    attached = load_config(
        {
            "JARVIS_DATA_DIR": str(tmp_path),
            "JARVIS_BROWSER_CDP_URL": "http://127.0.0.1:9222",
        }
    )
    assert attached.browser_cdp_endpoint == "http://127.0.0.1:9222"
    assert attached.browser_profile_dir is None
    with pytest.raises(ValueError, match="Conflicting"):
        load_config(
            {
                "JARVIS_DATA_DIR": str(tmp_path),
                "JARVIS_BROWSER_CDP_URL": "http://127.0.0.1:9222",
                "JARVIS_BROWSER_CDP_ENDPOINT": "http://127.0.0.1:9333",
            }
        )


def test_strict_sandbox_retains_its_original_capability_surface() -> None:
    registry = ToolRegistry()
    register_browser(registry, Probe())
    assert {item["name"] for item in registry.discover()} == {
        "browser.open",
        "browser.navigate",
        "browser.search",
        "browser.click",
        "browser.type",
        "browser.read",
        "browser.get_tabs",
        "browser.close",
    }


def test_workspace_navigation_is_exact_and_private_opt_in_is_narrow() -> None:
    policy = NetworkPolicy.from_workspace(profile())
    assert policy.validate("https://erp.example.com/#/sales")
    for url in (
        "https://sub.erp.example.com",
        "https://unlisted.example.com",
        "file:///etc/passwd",
        "https://127.0.0.1",
        "https://localhost",
        "https://169.254.169.254",
        "https://192.168.1.1",
        "https://10.1.2.3",
    ):
        with pytest.raises(ToolError):
            policy.validate(url)
    with pytest.raises(ValidationError):
        custom_app(category="productivity", allow_private_network=True)
    for origin in ("https://127.0.0.1", "https://10.1.1.1", "https://localhost"):
        with pytest.raises(ValidationError):
            custom_app(
                launch_url=None,
                enabled=False,
                allowed_origins=(origin,),
                allow_private_network=True,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address,allowed",
    [
        ("10.1.2.3", True),
        ("192.168.2.3", True),
        ("172.16.2.3", True),
        ("fd00::1", True),
        ("127.0.0.1", False),
        ("169.254.169.254", False),
        ("::1", False),
        ("fe80::1", False),
    ],
)
async def test_private_dns_requires_exact_enabled_corporate_grant(
    monkeypatch: pytest.MonkeyPatch, address: str, allowed: bool
) -> None:
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(
            return_value=[
                (
                    socket.AF_INET6 if ":" in address else socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (address, 443),
                )
            ]
        ),
    )
    app = custom_app(allow_private_network=True)
    granted = NetworkPolicy.from_workspace(WorkspaceProfile(apps=(app,)))
    if allowed:
        assert await granted.validate_live_request("https://erp.example.com/report")
    else:
        with pytest.raises(ToolError):
            await granted.validate_live_request("https://erp.example.com/report")
    for other in (
        NetworkPolicy.from_workspace(WorkspaceProfile(apps=(custom_app(),))),
        NetworkPolicy.from_workspace(WorkspaceProfile(apps=(custom_app(enabled=False),))),
    ):
        with pytest.raises(ToolError):
            await other.validate_live_request("https://erp.example.com/report")


def test_browser_storage_is_unreachable_through_both_filesystem_contracts(tmp_path: Path) -> None:
    config = AppConfig(tmp_path / "data", file_roots=(str(tmp_path),))
    stored = config.data_dir / "browser_profile" / "Preferences.json"
    with pytest.raises(ToolError):
        config.file_policy.check(str(stored))
    assert not config.file_policy.inside(stored)
    with pytest.raises(ToolError):
        config.filesystem_policy.path(str(stored))
    assert config.file_policy.check(str(tmp_path / "document.txt"))


@pytest.mark.parametrize(
    "name",
    [
        "Publish",
        "Post",
        "Comment",
        "Send",
        "Delete",
        "Pay",
        "Опубликовать",
        "Отправить",
        "Удалить",
    ],
)
def test_publication_buttons_remain_confirm(name: str, tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_browser(
        registry, Probe(), NetworkPolicy.from_workspace(profile()), workspace=profile()
    )
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, ApprovalStore(), audit)
        action = engine.prepare(
            "browser.click",
            {
                "target": target().model_dump(),
                "element": ElementTarget(
                    role="button", name=name, fingerprint="d" * 64, mark_id=1
                ).model_dump(),
            },
            Mode.EXECUTE,
        )
        assert isinstance(action, Action) and action.risk is Risk.CONFIRM


@pytest.mark.parametrize(
    "key,risk",
    [
        ("Enter", Risk.CONFIRM),
        ("Escape", Risk.SAFE),
        ("Tab", Risk.SAFE),
        ("PageDown", Risk.SAFE),
        ("PageUp", Risk.SAFE),
    ],
)
def test_only_fixed_browser_keys_and_exact_targets(key: str, risk: Risk, tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_browser(
        registry, Probe(), NetworkPolicy.from_workspace(profile()), workspace=profile()
    )
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, ApprovalStore(), audit)
        action = engine.prepare("browser.press_key", {"target": target().model_dump(), "key": key})
        assert isinstance(action, Action) and action.risk is risk
        assert isinstance(
            engine.prepare(
                "browser.press_key",
                {
                    "target": target().model_dump(),
                    "key": "Control+V",
                },
            ),
            Outcome,
        )


@pytest.mark.asyncio
async def test_new_tools_simulate_without_adapters_or_credentials(tmp_path: Path) -> None:
    probe = Probe()
    registry = ToolRegistry()
    register_browser(registry, probe, NetworkPolicy.from_workspace(profile()), workspace=profile())
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        store = ApprovalStore()
        engine = PermissionEngine(registry, store, audit)
        authority = store.take_authority(audit.approved)
        for name, args, risk in [
            ("open_workspace_app", {"app_id": "sales_erp"}, Risk.SAFE),
            (
                "scroll",
                {"target": target().model_dump(), "direction": "down", "amount": 500},
                Risk.SAFE,
            ),
            ("trigger_1password_autofill", {"target": target().model_dump()}, Risk.CONFIRM),
            (
                "type",
                {
                    "target": target().model_dump(),
                    "element": {
                        "role": "textbox",
                        "name": "Comment draft",
                        "fingerprint": "d" * 64,
                    },
                    "text": "Draft",
                },
                Risk.SAFE,
            ),
        ]:
            action = engine.prepare("browser." + name, args, Mode.SIMULATION)
            assert isinstance(action, Action) and action.risk is risk
            token = authority.approve(action) if risk is Risk.CONFIRM else None
            assert (await engine.execute(action, token)).status is Status.SIMULATED
        assert not probe.calls
        assert isinstance(
            engine.prepare(
                "browser.open_workspace_app",
                {
                    "app_id": "sales_erp",
                    "url": "https://untrusted.example.com",
                },
            ),
            Outcome,
        )
        assert isinstance(
            engine.prepare("browser.open_workspace_app", {"app_id": "onepassword"}), Outcome
        )


@pytest.mark.parametrize("app_id", ["quickbooks", "aws_console", "ms_admin"])
def test_financial_admin_writes_cannot_be_downgraded_by_app_metadata(
    tmp_path: Path, app_id: str
) -> None:
    entry = next(app for app in profile().apps if app.id == app_id)
    assert entry.launch_url is not None
    registry = ToolRegistry()
    register_browser(
        registry, Probe(), NetworkPolicy.from_workspace(profile()), workspace=profile()
    )
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        engine = PermissionEngine(registry, ApprovalStore(), audit)
        page = target(url=entry.launch_url, origin=entry.allowed_origins[0])
        cases: list[tuple[str, dict[str, Any]]] = [
            ("click", {"element": {"role": "button", "name": "Pay", "fingerprint": "d" * 64}}),
            (
                "click",
                {
                    "element": {
                        "role": "link",
                        "name": "Pay",
                        "fingerprint": "d" * 64,
                        "request": {"url": entry.launch_url, "method": "GET"},
                    }
                },
            ),
            (
                "type",
                {
                    "element": {"role": "textbox", "name": "Amount", "fingerprint": "e" * 64},
                    "text": "1",
                },
            ),
            ("press_key", {"key": "Enter"}),
        ]
        for tool, args in cases:
            denied = engine.prepare("browser." + tool, {"target": page.model_dump(), **args})
            assert isinstance(denied, Outcome) and denied.status is Status.DENIED
            assert denied.error is ErrorCode.NETWORK_DENIED


def test_marks_have_strict_bounds_and_exactly_one_target_form() -> None:
    for model in (ClickElement, TypeElement):
        fields: dict[str, Any] = {"target": target().model_dump()}
        if model is TypeElement:
            fields["text"] = "draft"
        for bad in (0, 97, True, "1"):
            with pytest.raises(ValidationError):
                model.model_validate(fields | {"mark_id": bad})
        with pytest.raises(ValidationError):
            model.model_validate(fields)
        with pytest.raises(ValidationError):
            model.model_validate(
                fields
                | {
                    "mark_id": 1,
                    "element": {
                        "role": "textbox",
                        "name": "Draft",
                        "fingerprint": "d" * 64,
                    },
                }
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("observe_other_tab", [False, True])
@pytest.mark.parametrize(
    "tool,role,risk",
    [
        ("browser.click", "button", Risk.CONFIRM),
        ("browser.type", "textbox", Risk.SAFE),
    ],
)
async def test_runner_resolves_marks_before_approval_and_rejects_old_tab_snapshot(
    tmp_path: Path,
    tool: str,
    role: str,
    risk: Risk,
    observe_other_tab: bool,
) -> None:
    element = ElementTarget.model_validate(
        {
            "role": role,
            "name": "Post" if role == "button" else "Draft",
            "fingerprint": "d" * 64,
            "mark_id": 1,
        }
    )
    first = PageView(target=target(), title="ERP", text="Ready", elements=[element])
    second = first.model_copy(update={"target": target(observation_id="e" * 32)})
    seen: list[BrowserCommand] = []

    class Backend:
        async def call(self, command: BrowserCommand, context: ExecutionContext) -> BrowserResult:
            seen.append(command)
            return BrowserResult(page=second)

    args = {"target": first.target.model_dump(), "mark_id": 1}
    if tool == "browser.type":
        args["text"] = "Draft"
    registry = ToolRegistry()
    register_browser(
        registry, Backend(), NetworkPolicy.from_workspace(profile()), workspace=profile()
    )
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        store = ApprovalStore()
        authority = store.take_authority(audit.approved)
        approved = AsyncMock(side_effect=authority.approve)
        runner = Runner(
            registry,
            PermissionEngine(registry, store, audit),
            Scripted([call(tool, args), call(tool, args)]),
            approved,
            AsyncMock(),
        )
        runner.steps.append(
            Step(
                "browser.read",
                Outcome(uuid4(), Status.SUCCESS, result_json=canonical(BrowserResult(page=first))),
            )
        )
        if observe_other_tab:
            other = first.model_copy(update={"target": target(tab_id="d" * 32)})
            runner.steps.append(
                Step(
                    "browser.read",
                    Outcome(
                        uuid4(),
                        Status.SUCCESS,
                        result_json=canonical(BrowserResult(page=other)),
                    ),
                )
            )
        result = await runner.run("Synthetic draft", Mode.EXECUTE)
        assert result.error == "unobserved_target"
        issued = [command for command in seen if command.phase == "run"]
        assert len(issued) == 1
        snapshot = json.loads(issued[0].parameters)
        assert (
            snapshot["element"] == element.model_dump(mode="json") and snapshot["mark_id"] is None
        )
        assert approved.await_count == int(risk is Risk.CONFIRM)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["none", "other-tab", "failed-mutation", "tab-list", "duplicate"]
)
async def test_runner_refuses_missing_stale_or_ambiguous_browser_marks(
    tmp_path: Path,
    source: str,
) -> None:
    element = ElementTarget(role="button", name="Post", fingerprint="d" * 64, mark_id=1)
    view = PageView(target=target(), title="ERP", text="Ready", elements=[element])
    backend = Probe()
    registry = ToolRegistry()
    register_browser(
        registry, backend, NetworkPolicy.from_workspace(profile()), workspace=profile()
    )
    with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
        approve = AsyncMock()
        runner = Runner(
            registry,
            PermissionEngine(registry, ApprovalStore(), audit),
            Scripted([call("browser.click", {"target": view.target.model_dump(), "mark_id": 1})]),
            approve,
            AsyncMock(),
        )
        if source == "other-tab":
            view = view.model_copy(update={"target": target(tab_id="d" * 32)})
        if source == "duplicate":
            view = view.model_copy(update={"elements": [element, element]})
        if source != "none":
            runner.steps.append(
                Step(
                    "browser.read",
                    Outcome(
                        uuid4(),
                        Status.SUCCESS,
                        result_json=canonical(BrowserResult(page=view)),
                    ),
                )
            )
        if source == "failed-mutation":
            runner.steps.append(
                Step(
                    "browser.click",
                    Outcome(uuid4(), Status.ERROR, may_have_effects=True),
                    payload=canonical(ClickElement(target=target(), element=element)),
                )
            )
        if source == "tab-list":
            runner.steps.append(
                Step(
                    "browser.get_tabs",
                    Outcome(
                        uuid4(),
                        Status.SUCCESS,
                        result_json=canonical(BrowserResult(tabs=[target()])),
                    ),
                )
            )
        result = await runner.run("Synthetic post", Mode.EXECUTE)
        assert result.error == "unobserved_target"
        assert not backend.calls and not approve.await_count


def test_corporate_profile_accepts_named_internal_host_but_requires_explicit_flag() -> None:
    app = AppEntry(
        id="erp",
        name="ERP",
        category="custom_erp",
        kind="web",
        launch_url="https://erp.company.internal/home",
        allowed_origins=("https://erp.company.internal",),
        allow_private_network=True,
    )
    assert NetworkPolicy.from_workspace(WorkspaceProfile(apps=(app,))).validate(
        app.launch_url or ""
    )
    with pytest.raises(ValidationError):
        AppEntry.model_validate(app.model_dump() | {"allow_private_network": False})
