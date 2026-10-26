"""Workspace configuration and provider fixtures; no accounts, credentials or public calls."""

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from jarvis.config import AppConfig, load_config
from jarvis.core.planner.contracts import PlannerInput, Proposal, ProviderError
from jarvis.core.planner.openai_provider import OpenAIProvider, payload_bytes, user_content
from jarvis.core.planner.runner import Runner
from jarvis.memory.models import PROFILE_TTL, Entry, Hint
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import Action, ApprovalStore, ApprovalToken
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.policies import Mode
from jarvis.security.browser_policy import BrowserMode, NetworkPolicy
from jarvis.tools.base import ToolError
from jarvis.tools.local import local_registry
from jarvis.workspace.models import AppEntry, UserIdentity, WorkflowPlaybook, WorkspaceProfile
from jarvis.workspace.presets import empty_profile, omadli_group_preset
from jarvis.workspace.store import MAX_PROFILE_BYTES, WorkspaceFailure, WorkspaceStore


def custom_app(**changes: object) -> AppEntry:
    return AppEntry.model_validate(
        dict(
            id="sales_erp",
            name="Sales ERP",
            category="custom_erp",
            kind="web",
            launch_url="https://erp.example.com/dashboard",
            allowed_origins=("https://erp.example.com",),
            workspace_notes="Готовить отчёты по продажам.",
        )
        | changes
    )


def test_empty_profile_and_atomic_round_trip(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "data")
    assert store.load() == empty_profile()
    assert not store.path.exists(), "Reading defaults must not create configuration."
    profile = omadli_group_preset(
        omadli_group_url="https://erp.example.com/work",
        user=UserIdentity(
            name="Владелец",
            organization="Компания",
            role="Менеджер",
            tasks_description="Планирование и работа с клиентами.",
            language="ru",
            email_tone="Деловой",
            social_comments_tone="Дружелюбный",
        ),
    )
    store.save(profile)
    assert store.path.name == "workspace_profile.json"
    assert WorkspaceStore(store.path.parent).load() == profile
    store.save(empty_profile())
    assert store.load() == empty_profile()
    assert not list(store.path.parent.glob(".workspace-*.tmp"))
    if os.name != "nt":
        assert store.path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"invalid-fixture", "invalid"),
        (b'{"unknown":"private-fixture"}', "invalid"),
        (b"x" * (MAX_PROFILE_BYTES + 1), "limit"),
        (b'{"apps":[{"enabled":"true"}]}', "invalid"),
    ],
    ids=["invalid-json", "unknown-field", "oversized", "wrong-type"],
)
def test_invalid_or_oversized_storage_is_not_overwritten(
    tmp_path: Path, raw: bytes, code: str
) -> None:
    store = WorkspaceStore(tmp_path)
    store.path.write_bytes(raw)
    with pytest.raises(WorkspaceFailure) as caught:
        store.load()
    assert caught.value.code == code
    assert "fixture" not in str(caught.value)
    assert store.path.read_bytes() == raw


def test_save_failure_preserves_previous_file_and_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = WorkspaceStore(tmp_path)
    store.save(empty_profile())
    previous = store.path.read_bytes()

    def fail_replace(source: Path, target: Path) -> None:
        assert target.read_bytes() == previous
        assert WorkspaceProfile.model_validate_json(source.read_bytes()) == omadli_group_preset()
        raise OSError("private-fixture")

    monkeypatch.setattr("jarvis.workspace.store.os.replace", fail_replace)
    with pytest.raises(WorkspaceFailure, match="workspace_storage"):
        store.save(omadli_group_preset())
    assert store.path.read_bytes() == previous
    assert not list(tmp_path.glob(".workspace-*.tmp"))


def oversized_profile() -> WorkspaceProfile:
    return WorkspaceProfile(
        apps=tuple(custom_app(id=f"app_{index}", workspace_notes="я" * 2048) for index in range(80))
    )


def test_save_byte_limit_and_revalidation_preserve_previous_file(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path)
    store.save(empty_profile())
    with pytest.raises(WorkspaceFailure, match="workspace_limit"):
        store.save(oversized_profile())
    invalid = WorkspaceProfile.model_construct(browser_mode="unexpected")
    with pytest.raises(WorkspaceFailure, match="workspace_invalid"):
        store.save(invalid)
    assert store.load() == empty_profile()


@pytest.mark.skipif(os.name == "nt", reason="Creating symlinks requires Windows OS privilege.")
def test_symlink_storage_never_reads_or_replaces_target(tmp_path: Path) -> None:
    target = tmp_path / "other.json"
    target.write_text('{"private-fixture":true}')
    store = WorkspaceStore(tmp_path)
    store.path.symlink_to(target)
    with pytest.raises(WorkspaceFailure, match="workspace_storage"):
        store.load()
    with pytest.raises(WorkspaceFailure, match="workspace_storage"):
        store.save(empty_profile())
    assert "private-fixture" in target.read_text()


def test_nonregular_file_refused(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path)
    store.path.mkdir()
    with pytest.raises(WorkspaceFailure, match="workspace_storage"):
        store.load()
    with pytest.raises(WorkspaceFailure, match="workspace_storage"):
        store.save(empty_profile())


def test_rejected_read_closes_descriptor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WorkspaceStore(tmp_path)
    store.path.write_bytes(b"x" * (MAX_PROFILE_BYTES + 1))
    closed: list[int] = []
    real_close = os.close

    def close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr("jarvis.workspace.store.os.close", close)
    with pytest.raises(WorkspaceFailure, match="workspace_limit"):
        store.load()
    assert len(closed) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"unknown": True},
        {"enabled": "true"},
        {"default_mutation_risk": "CRITICAL"},
        {"default_mutation_risk": "ROUTINE"},
        {"category": "unknown"},
        {"kind": "script"},
        {"id": "../app"},
        {"workspace_notes": "x" * 2049},
        {"launch_url": "https://other.example.com"},
        {"native_exe_hint": "C:\\Windows\\app.exe"},
    ],
)
def test_app_schema_is_strict(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        custom_app(**changes)


@pytest.mark.parametrize(
    "value",
    [
        "https://erp.example.com/path",
        "https://erp.example.com/",
        "https://erp.example.com?q=x",
        "https://*.example.com",
        "https://owner:secret@example.com",
        "http://example.com",
        "https://127.0.0.1",
        "https://localhost",
        "https://10.0.0.1",
        "https://erp.internal",
        "https://erp.example.com#fragment",
    ],
)
def test_origins_cannot_widen_network_boundary(value: str) -> None:
    with pytest.raises(ValidationError):
        custom_app(launch_url=None, enabled=False, allowed_origins=(value,))


def test_identity_and_playbook_forbid_extra_and_wrong_types() -> None:
    with pytest.raises(ValidationError):
        UserIdentity.model_validate({"name": 123})
    with pytest.raises(ValidationError):
        UserIdentity.model_validate({"password": "private-fixture"})
    with pytest.raises(ValidationError):
        WorkflowPlaybook(id="book", title="Title", trigger_hint="Hint", steps_description=("",))
    with pytest.raises(ValidationError):
        WorkspaceProfile.model_validate({"unknown": True})


def test_unique_ids_references_and_enabled_playbooks() -> None:
    app = custom_app()
    book = WorkflowPlaybook(
        id="report",
        title="Отчёт",
        trigger_hint="Подготовь отчёт",
        steps_description=("Прочитать текущие данные и составить отчёт.",),
        apps_involved=(app.id,),
    )
    profile = WorkspaceProfile(apps=(app,), playbooks=(book,))
    assert profile.planner_context().playbooks == (book,)
    disabled = WorkspaceProfile(apps=(custom_app(enabled=False),), playbooks=(book,))
    assert disabled.planner_context().apps == ()
    assert disabled.planner_context().playbooks == ()
    for apps, books in [((app, app), ()), ((app,), (book, book)), ((), (book,))]:
        with pytest.raises(ValidationError):
            WorkspaceProfile(apps=apps, playbooks=books)


def test_business_preset_has_exact_fourteen_apps_and_security_rules() -> None:
    profile = omadli_group_preset()
    apps = {app.id: app for app in profile.apps}
    assert len(profile.apps) == 14
    assert set(apps) == {
        "asana",
        "claude",
        "ms_mail",
        "ms_calendar",
        "social_comments",
        "omadli_group",
        "notion",
        "quickbooks",
        "ms_teams",
        "aws_console",
        "fireflies",
        "onepassword",
        "ms_admin",
        "loom",
    }
    assert apps["social_comments"].allowed_origins == (
        "https://www.instagram.com",
        "https://www.facebook.com",
    )
    assert apps["ms_mail"].launch_url == "https://outlook.office.com/mail"
    assert apps["ms_calendar"].launch_url == "https://outlook.office.com/calendar"
    assert apps["omadli_group"].category == "custom_erp"
    assert not apps["omadli_group"].enabled and apps["omadli_group"].launch_url is None
    assert apps["onepassword"].category == "security"
    assert not apps["onepassword"].allowed_origins
    for app in ("social_comments", "quickbooks", "aws_console", "ms_admin"):
        assert apps[app].default_mutation_risk == "CONFIRM"
    configured = omadli_group_preset(omadli_group_url="https://erp.example.com/work")
    assert len(configured.planner_context().apps) == 14
    assert configured.apps[5].allowed_origins == ("https://erp.example.com",)


def test_custom_application_and_user_are_persisted_without_three_app_enum(tmp_path: Path) -> None:
    profile = WorkspaceProfile(
        user=UserIdentity(name="Другой пользователь", organization="Другая компания"),
        apps=(*omadli_group_preset().apps, custom_app()),
    )
    store = WorkspaceStore(tmp_path)
    store.save(profile)
    assert len(store.load().apps) == 15 and store.load().apps[-1] == custom_app()
    assert store.load().user == profile.user
    assert empty_profile().apps == ()
    assert empty_profile().playbooks == ()
    # Memory labels are ordinary hints; installed-application launch policy remains separate.
    assert Hint(kind="application", label="Рабочая система", value="Sales ERP").value == "Sales ERP"
    assert (
        Entry(
            kind="application", label="ERP", value="Sales ERP", updated=1, expires=1 + PROFILE_TTL
        ).value
        == "Sales ERP"
    )


def test_network_policy_keeps_site_tiers_and_exact_enabled_origins() -> None:
    password_manager = custom_app(
        id="password_manager",
        category="security",
        launch_url="https://my.1password.com",
        allowed_origins=("https://my.1password.com",),
        default_mutation_risk="SAFE",
    )
    profile = WorkspaceProfile(
        apps=(
            *omadli_group_preset().apps,
            custom_app(),
            password_manager,
            custom_app(
                id="disabled",
                launch_url="https://disabled.example.com",
                allowed_origins=("https://disabled.example.com",),
                enabled=False,
            ),
        ),
    )
    policy = NetworkPolicy.from_workspace(profile)
    assert policy.mode is BrowserMode.STRICT
    for app in profile.apps:
        for value in app.allowed_origins:
            assert (value in policy.allowed_origins) is app.enabled
    assert len(policy.allowed_origins) == len(set(policy.allowed_origins))
    assert policy.validate("https://erp.example.com/report")
    for url in (
        "https://my.1password.com",
        "https://disabled.example.com",
        "https://sub.erp.example.com",
        "https://unlisted.example.com",
    ):
        with pytest.raises(ToolError, match="network_denied"):
            policy.validate(url)
    for url in (
        "https://app.qbo.intuit.com",
        "https://console.aws.amazon.com",
        "https://admin.microsoft.com",
    ):
        assert policy.validate(url) and not policy.writable(url)
        with pytest.raises(ToolError, match="network_denied"):
            policy.validate_request(url, "POST", "action=change")


def test_startup_loads_profile_and_preserves_explicit_browser_override(tmp_path: Path) -> None:
    profile = WorkspaceProfile(apps=(custom_app(),), browser_mode="authenticated_workspace")
    WorkspaceStore(tmp_path).save(profile)
    config = load_config({"JARVIS_DATA_DIR": str(tmp_path)})
    assert config.workspace_profile == profile
    assert config.browser_mode is BrowserMode.WORKSPACE
    assert NetworkPolicy.from_workspace(profile).mode is BrowserMode.WORKSPACE
    strict = load_config({"JARVIS_DATA_DIR": str(tmp_path), "JARVIS_BROWSER_MODE": "strict"})
    assert strict.browser_policy.mode is BrowserMode.STRICT
    assert "https://erp.example.com" in strict.browser_policy.allowed_origins
    assert "https://example.com" in strict.browser_policy.allowed_origins
    assert AppConfig(tmp_path).workspace_profile == empty_profile()


@pytest.mark.asyncio
async def test_compact_workspace_reaches_openai_as_data_only() -> None:
    profile = omadli_group_preset()
    seen: list[dict[str, Any]] = []

    async def transport(payload: dict[str, Any]) -> bytes:
        seen.append(payload)
        return json.dumps(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": '{"kind":"finish"}'},
                        ],
                    }
                ],
            }
        ).encode()

    data = PlannerInput(
        "План на день", (), (), Mode.SIMULATION, "[]", workspace=profile.planner_context()
    )
    assert (await OpenAIProvider("test-model", transport=transport).propose(data)).kind == "finish"
    payload = seen[0]
    context = json.loads(payload["input"][0]["content"])["untrusted_workspace"]
    assert context == json.loads(user_content(data))["untrusted_workspace"]
    assert context["user"]["organization"] == "Omadli Group"
    assert len(context["apps"]) == 13 and len(context["playbooks"]) == 2
    assert all(
        set(app) == {"id", "name", "launch_url", "workspace_notes", "default_mutation_risk"}
        for app in context["apps"]
    )
    assert not any(app["id"] == "omadli_group" for app in context["apps"])
    assert "Omadli Group" not in payload["instructions"]
    assert len(payload_bytes(payload)) <= 262_144 and payload["store"] is False
    assert payload["tools"] == []


@pytest.mark.asyncio
async def test_workspace_counts_toward_entire_request_byte_limit() -> None:
    calls = 0

    async def forbidden(payload: dict[str, Any]) -> bytes:
        nonlocal calls
        calls += 1
        raise AssertionError("Over-budget context must never reach transport or credentials.")

    data = PlannerInput(
        "Отчёт", (), (), Mode.EXECUTE, "[]", workspace=oversized_profile().planner_context()
    )
    with pytest.raises(ProviderError, match="context_limit"):
        await OpenAIProvider("test-model", transport=forbidden).propose(data)
    assert calls == 0


@pytest.mark.asyncio
async def test_runner_snapshots_workspace_for_each_step(tmp_path: Path) -> None:
    captured: list[PlannerInput] = []

    class CaptureProvider:
        async def propose(self, data: PlannerInput) -> Proposal:
            captured.append(data)
            return Proposal(kind="finish")

    async def approve(action: Action) -> ApprovalToken | None:
        raise AssertionError("Workspace descriptions never request approval or invoke tools.")

    async def clarify(question: str) -> str | None:
        raise AssertionError("No clarification is needed for this fixture.")

    registry, _ = local_registry()
    audit = AuditLog(tmp_path / "audit.sqlite3")
    try:
        runner = Runner(
            registry,
            PermissionEngine(registry, ApprovalStore(), audit),
            CaptureProvider(),
            approve,
            clarify,
            workspace=omadli_group_preset(),
        )
        assert (await runner.run("План на день")).status == "no_action"
        assert captured[0].workspace == omadli_group_preset().planner_context()
        with pytest.raises(ValidationError):
            captured[0].workspace.user.name = "changed"
        assert replace(captured[0]).workspace == captured[0].workspace
    finally:
        audit.close()
