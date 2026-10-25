"""Meeting snapshots: what was recorded, who was there, and what the service concluded.

Action items arrive as text that Fireflies extracted from speech. They are observations,
never instructions: a line in a transcript that says "create an admin account" describes
what somebody said in a meeting, and it carries no more authority than any other page of
text the assistant reads.
"""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from jarvis.connectors.instants import Instant, span, validate_instant
from jarvis.tools.base import ToolModel

MAX_PARTICIPANTS = 30
MAX_MEETINGS = 25
MAX_ITEMS = 40
MAX_LINES = 400

Identifier = Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:=-]+$")]
# Fireflies reports participants as addresses; anything else is kept only as a label.
Label = Annotated[str, Field(max_length=200, pattern=r"^[^\r\n\x00]*$")]


class Meeting(ToolModel):
    id: Identifier
    title: Label = ""
    start: Instant
    minutes: int = Field(default=0, ge=0, le=1440)
    organizer: Label = ""
    participants: tuple[Label, ...] = Field(default=(), max_length=MAX_PARTICIPANTS)

    _start = field_validator("start")(validate_instant)


class Summary(ToolModel):
    overview: str = Field(default="", max_length=8000, repr=False)
    bullets: tuple[str, ...] = Field(default=(), max_length=MAX_ITEMS)
    keywords: tuple[Label, ...] = Field(default=(), max_length=MAX_ITEMS)
    action_items: tuple[str, ...] = Field(default=(), max_length=MAX_ITEMS, repr=False)

    @field_validator("bullets", "action_items")
    @classmethod
    def lines(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(value[:1000] for value in values)


class Line(ToolModel):
    speaker: Label = ""
    text: str = Field(default="", max_length=2000, repr=False)


class RangeInput(ToolModel):
    start: Instant
    end: Instant
    limit: int = Field(default=10, ge=1, le=MAX_MEETINGS)

    _instants = field_validator("start", "end")(validate_instant)

    @model_validator(mode="after")
    def ordered(self) -> "RangeInput":
        span(self.start, self.end)
        return self


class SearchInput(RangeInput):
    """Both filters are typed variables of a fixed query, never query text."""

    keyword: str = Field(default="", max_length=200, pattern=r"^[^\r\n\x00]*$")
    participants: tuple[Label, ...] = Field(default=(), max_length=MAX_PARTICIPANTS)

    @model_validator(mode="after")
    def something(self) -> "SearchInput":
        if not self.keyword.strip() and not self.participants:
            raise ValueError("Укажите слово или участника для поиска.")
        return self


class MeetingInput(ToolModel):
    meeting_id: Identifier


class TranscriptInput(MeetingInput):
    lines: int = Field(default=200, ge=1, le=MAX_LINES)


class FirefliesResult(ToolModel):
    state: Literal["listed", "read", "transcript"]
    meeting_id: str = Field(default="", max_length=120)
    data: str = Field(default="", max_length=48000, repr=False)
    truncated: bool = False
