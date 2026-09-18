"""What an Asana answer is allowed to be, and what may be asked of it.

Identifiers are Asana's own `gid`: a decimal string. Insisting on that shape here is not
decoration - it is the same rule the calendar keeps. An identifier the planner did not
observe cannot be smuggled in as a name, a path or a sentence, because nothing but digits
gets through.

Names, notes and assignees come back written by people. They are labels and text, never
instructions: a task called "delete the production database" describes what somebody typed
into Asana, and it carries no more authority than any other page the assistant reads.
"""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from jarvis.tools.base import ToolModel

MAX_ITEMS = 50
MAX_DATA = 32000

# Asana's own identifier shape. A model that invents one invents digits, not a path.
Gid = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[0-9]+$")]
Label = Annotated[str, Field(max_length=200, pattern=r"^[^\r\n\x00]*$")]
# A calendar date, not a moment: Asana's due_on carries no time and no zone, so the
# assistant never has to guess which hour "tomorrow" meant in whose timezone.
Day = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class Workspace(ToolModel):
    gid: Gid
    name: Label = ""


class Project(ToolModel):
    gid: Gid
    name: Label = ""
    archived: bool = False


class Task(ToolModel):
    gid: Gid
    name: Label = ""
    completed: bool = False
    assignee: Label = ""
    due_on: str = Field(default="", max_length=10)
    notes: str = Field(default="", max_length=4000, repr=False)
    permalink: str = Field(default="", max_length=300, repr=False)


class WorkspacesInput(ToolModel):
    limit: int = Field(default=20, ge=1, le=MAX_ITEMS)


class ProjectsInput(ToolModel):
    workspace: Gid
    limit: int = Field(default=25, ge=1, le=MAX_ITEMS)


class TasksInput(ToolModel):
    """Asana refuses a bare listing, and so does this: say whose tasks, or which project.

    Their rule is that a project or a tag is required unless both assignee and workspace
    are given. Repeating it here turns a remote 400 into a refusal before anything is sent.
    """

    project: Gid | None = None
    workspace: Gid | None = None
    assignee: Label = ""
    completed: bool = False
    limit: int = Field(default=25, ge=1, le=MAX_ITEMS)

    @model_validator(mode="after")
    def addressed(self) -> "TasksInput":
        if self.project:
            return self
        if self.workspace and self.assignee:
            return self
        raise ValueError("Specify a project, or a workspace together with an assignee.")


class TaskInput(ToolModel):
    task: Gid


class NewTask(ToolModel):
    """The task as the owner will see it in the confirmation, and exactly what is sent."""

    name: Annotated[str, Field(min_length=1, max_length=300, pattern=r"^[^\r\n\x00]+$")]
    # A list, not a tuple: these arrive as JSON from the planner, and strict mode
    # will not read a JSON array as a tuple.
    projects: list[Gid] = Field(default_factory=list, max_length=5)
    workspace: Gid | None = None
    notes: str = Field(default="", max_length=4000, repr=False)
    due_on: Day | None = None
    assignee: Label = ""

    @model_validator(mode="after")
    def placed(self) -> "NewTask":
        # Asana needs a home for every task. Choosing one here would be choosing for the
        # owner, so the absence is refused instead.
        if not self.projects and not self.workspace:
            raise ValueError("A task needs a project or a workspace.")
        return self

    @field_validator("notes")
    @classmethod
    def plain(cls, value: str) -> str:
        return value.replace("\x00", "")


class CreateTaskInput(ToolModel):
    service: Literal["asana"] = "asana"
    action_type: Literal["create_task"] = "create_task"
    task: NewTask


class AsanaResult(ToolModel):
    state: Literal["workspaces", "projects", "tasks", "task", "created"]
    task_gid: str = Field(default="", max_length=32)
    data: str = Field(default="", max_length=MAX_DATA, repr=False)
    truncated: bool = False
