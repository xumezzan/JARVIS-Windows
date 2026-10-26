"""Owner-only settings operations, separate from the planner's registered tool surface."""

import json
from hashlib import sha256

from pydantic import ValidationError

from jarvis.desktop.protocol import WorkspaceCommand, WorkspaceSettings
from jarvis.workspace.models import WorkspaceProfile
from jarvis.workspace.presets import omadli_group_preset
from jarvis.workspace.store import WorkspaceFailure, WorkspaceStore


def revision(profile: WorkspaceProfile) -> str:
    return sha256(profile.model_dump_json().encode()).hexdigest()


def configure(store: WorkspaceStore, command: WorkspaceCommand) -> WorkspaceProfile:
    current = store.load()
    if command.operation == "read":
        return current
    if revision(current) != command.revision:
        raise ValueError("workspace_changed")
    if command.profile is not None:
        try:
            checked = WorkspaceSettings.model_validate_json(json.dumps(command.profile))
            updated = WorkspaceProfile.model_validate(current.model_dump() | checked.model_dump())
        except ValidationError:
            raise WorkspaceFailure("invalid") from None
    else:
        # Selecting a preset adds missing apps/recipes, preserving custom entries,
        # edited URLs, identity, browser mode and independently reviewed MCP servers.
        preset = omadli_group_preset()
        apps = {item.id: item for item in preset.apps}
        apps.update((item.id, item) for item in current.apps)
        books = {item.id: item for item in preset.playbooks}
        books.update((item.id, item) for item in current.playbooks)
        if len(apps) > 128 or len(books) > 64:
            raise WorkspaceFailure("limit")
        user = current.user.model_dump()
        if not current.user.organization:
            user["organization"] = preset.user.organization
        updated = WorkspaceProfile.model_validate(
            current.model_dump()
            | {"apps": tuple(apps.values()), "playbooks": tuple(books.values()), "user": user}
        )
    store.save(updated)
    return updated


def snapshot(
    profile: WorkspaceProfile, active: WorkspaceProfile, browser_mode: str
) -> dict[str, object]:
    editable = WorkspaceSettings.model_validate(
        profile.model_dump(include={"user", "apps", "browser_mode"})
    )
    return {
        "profile": editable.model_dump(mode="json"),
        "revision": revision(profile),
        "restart_required": profile != active,
        "active_browser_mode": browser_mode,
    }
