"""Strict, bounded workspace data. Notes and playbooks never grant tool permissions."""

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, StringConstraints, field_validator, model_validator

from jarvis.security.browser_policy import NetworkPolicy, corporate_origin, normalized_url, origin
from jarvis.tools.base import ToolError, ToolModel

Slug = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")]
ShortText = Annotated[str, StringConstraints(max_length=200)]
Description = Annotated[str, StringConstraints(max_length=2048)]
Category = Literal[
    "productivity", "communication", "finance", "cloud_admin", "social", "custom_erp", "security"
]
WorkspaceBrowserMode = Literal["strict_sandbox", "authenticated_workspace"]
MutationRisk = Literal["SAFE", "CONFIRM"]


def public_url(value: str, *, allow_private: bool = False) -> str:
    """Pure validation using the browser's existing HTTPS/private-network boundary."""
    try:
        normalized = normalized_url(value)
        if "*" in urlsplit(normalized).netloc:
            raise ValueError("Wildcard hosts are not exact origins.")
        # Describing a password manager does not grant access to it. Generated execution
        # policies retain the mandatory blocked domains, irrespective of this metadata.
        grant = (origin(normalized),) if allow_private else ()
        NetworkPolicy(
            allowed_origins=(origin(normalized),), blocked_domains=(), private_origins=grant
        ).validate(normalized)
        return normalized
    except (ToolError, ValueError):
        raise ValueError(
            "Workspace URLs require public HTTPS without credentials or fragments."
        ) from None


class UserIdentity(ToolModel):
    name: ShortText = Field(default="", repr=False)
    organization: ShortText = Field(default="", repr=False)
    role: ShortText = Field(default="", repr=False)
    tasks_description: Description = Field(default="", repr=False)
    language: str = Field(default="ru", min_length=1, max_length=80)
    email_tone: ShortText = ""
    social_comments_tone: ShortText = ""


class AppEntry(ToolModel):
    id: Slug
    name: str = Field(min_length=1, max_length=200)
    category: Category
    kind: Literal["web", "native", "hybrid"]
    launch_url: str | None = Field(default=None, max_length=2048, repr=False)
    allowed_origins: tuple[str, ...] = Field(default=(), max_length=32)
    native_exe_hint: str | None = Field(default=None, min_length=1, max_length=200)
    mcp_server_id: Slug | None = None
    workspace_notes: Description = Field(default="", repr=False)
    default_mutation_risk: MutationRisk = "CONFIRM"
    enabled: bool = True
    allow_private_network: bool = False

    @field_validator("native_exe_hint")
    @classmethod
    def executable_hint(cls, value: str | None) -> str | None:
        # An inert discovery hint, never an executable path or a command line.
        if value is not None and (
            not value.strip() or any(char in value for char in '/\\:;|&<>"\x00\r\n')
        ):
            raise ValueError("Use an executable name, never a path or command line.")
        return value

    @model_validator(mode="after")
    def launch_origin(self) -> "AppEntry":
        if self.allow_private_network and (
            self.category != "custom_erp"
            or not self.allowed_origins
            or any(not corporate_origin(value) for value in self.allowed_origins)
        ):
            raise ValueError("Private access is only available for explicit custom ERP domains.")
        for value in self.allowed_origins:
            if value != origin(public_url(value, allow_private=self.allow_private_network)):
                raise ValueError("Use exact HTTPS origins without paths, queries or wildcards.")
        if len(set(self.allowed_origins)) != len(self.allowed_origins):
            raise ValueError("Duplicate workspace origins.")
        if self.launch_url is not None:
            object.__setattr__(
                self,
                "launch_url",
                public_url(self.launch_url, allow_private=self.allow_private_network),
            )
        if self.launch_url is not None and origin(self.launch_url) not in self.allowed_origins:
            raise ValueError("The launch URL must belong to an explicitly allowed origin.")
        if self.enabled and self.kind == "web" and self.launch_url is None:
            raise ValueError("An enabled web application needs a launch URL.")
        return self


class WorkflowPlaybook(ToolModel):
    id: Slug
    title: str = Field(min_length=1, max_length=200)
    trigger_hint: str = Field(min_length=1, max_length=500, repr=False)
    steps_description: tuple[Description, ...] = Field(min_length=1, max_length=32, repr=False)
    apps_involved: tuple[Slug, ...] = Field(default=(), max_length=32)

    @field_validator("steps_description")
    @classmethod
    def nonempty_steps(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("A playbook step must describe work.")
        return values


class WorkspaceAppContext(ToolModel):
    """No executable hints, MCP configuration or network grants sent to a provider."""

    id: Slug
    name: str = Field(min_length=1, max_length=200)
    launch_url: str | None = Field(max_length=2048, repr=False)
    workspace_notes: Description = Field(repr=False)
    default_mutation_risk: MutationRisk


class WorkspaceContext(ToolModel):
    user: UserIdentity = Field(default_factory=UserIdentity, repr=False)
    apps: tuple[WorkspaceAppContext, ...] = Field(default=(), max_length=128, repr=False)
    playbooks: tuple[WorkflowPlaybook, ...] = Field(default=(), max_length=64, repr=False)


class WorkspaceProfile(ToolModel):
    user: UserIdentity = Field(default_factory=UserIdentity, repr=False)
    apps: tuple[AppEntry, ...] = Field(default=(), max_length=128, repr=False)
    playbooks: tuple[WorkflowPlaybook, ...] = Field(default=(), max_length=64, repr=False)
    browser_mode: WorkspaceBrowserMode = "strict_sandbox"

    @model_validator(mode="after")
    def unique_references(self) -> "WorkspaceProfile":
        app_ids = {app.id for app in self.apps}
        if len(app_ids) != len(self.apps):
            raise ValueError("Workspace application IDs must be unique.")
        if len({book.id for book in self.playbooks}) != len(self.playbooks):
            raise ValueError("Workspace playbook IDs must be unique.")
        if any(not set(book.apps_involved) <= app_ids for book in self.playbooks):
            raise ValueError("A playbook references an unknown workspace application.")
        return self

    def planner_context(self) -> WorkspaceContext:
        """Only enabled apps and playbooks whose required apps are all enabled."""
        enabled = {app.id for app in self.apps if app.enabled}
        return WorkspaceContext(
            user=self.user,
            apps=tuple(
                WorkspaceAppContext(
                    id=app.id,
                    name=app.name,
                    launch_url=app.launch_url,
                    workspace_notes=app.workspace_notes,
                    default_mutation_risk=app.default_mutation_risk,
                )
                for app in self.apps
                if app.enabled
            ),
            playbooks=tuple(book for book in self.playbooks if set(book.apps_involved) <= enabled),
        )
