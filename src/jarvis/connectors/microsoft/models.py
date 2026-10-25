"""Calendar snapshots with explicit times, explicit addresses and no free-form dates.

Every instant crossing this boundary is UTC in one exact shape. A tool that accepted
"tomorrow afternoon" would have to guess a timezone and a working day, and a meeting
created an hour off is worse than one not created at all. Resolving what the user said
into a concrete range is the planner's job; this layer only accepts the result.

Addresses are exact, as in mail: a display name or an alias never identifies an invitee.
"""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from jarvis.connectors.instants import Instant, span, validate_instant
from jarvis.mail.models import Account, Address, Identifier, validate_address
from jarvis.tools.base import ToolModel

MAX_ATTENDEES = 20
MAX_EVENTS = 25


Response = Literal["none", "accepted", "declined", "tentative"]


class Attendee(ToolModel):
    address: Address
    name: str = Field(default="", max_length=120, pattern=r"^[^\r\n\x00]*$")
    response: Response = "none"

    _address = field_validator("address")(validate_address)


class Event(ToolModel):
    """What the service actually holds, as observed. Never assembled from a plan."""

    id: Identifier
    subject: str = Field(default="", max_length=500, pattern=r"^[^\r\n\x00]*$")
    start: Instant
    end: Instant
    organizer: Address
    attendees: tuple[Attendee, ...] = Field(default=(), max_length=MAX_ATTENDEES)
    location: str = Field(default="", max_length=300, pattern=r"^[^\r\n\x00]*$")
    cancelled: bool = False

    _instants = field_validator("start", "end")(validate_instant)
    _organizer = field_validator("organizer")(validate_address)


class Slot(ToolModel):
    start: Instant
    end: Instant

    _instants = field_validator("start", "end")(validate_instant)


class AccountInput(ToolModel):
    account: Account


class RangeInput(AccountInput):
    start: Instant
    end: Instant
    limit: int = Field(default=10, ge=1, le=MAX_EVENTS)

    _instants = field_validator("start", "end")(validate_instant)

    @model_validator(mode="after")
    def ordered(self) -> "RangeInput":
        span(self.start, self.end)
        return self


class SearchInput(RangeInput):
    query: str = Field(min_length=1, max_length=200, pattern=r"^[^\r\n\x00]*$")


class EventInput(AccountInput):
    event_id: Identifier


class AvailabilityInput(RangeInput):
    addresses: tuple[Address, ...] = Field(min_length=1, max_length=MAX_ATTENDEES)
    minutes: int = Field(default=30, ge=15, le=480)

    @field_validator("addresses")
    @classmethod
    def exact(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            validate_address(value)
        if len({value.casefold() for value in values}) != len(values):
            raise ValueError("Повторяющийся адрес.")
        return values


class Draft(ToolModel):
    subject: str = Field(min_length=1, max_length=500, pattern=r"^[^\r\n\x00]*$")
    start: Instant
    end: Instant
    body: str = Field(default="", max_length=8000)
    location: str = Field(default="", max_length=300, pattern=r"^[^\r\n\x00]*$")
    attendees: tuple[Address, ...] = Field(default=(), max_length=MAX_ATTENDEES)

    _instants = field_validator("start", "end")(validate_instant)

    @field_validator("attendees")
    @classmethod
    def exact(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            validate_address(value)
        if len({value.casefold() for value in values}) != len(values):
            raise ValueError("Повторяющийся участник.")
        return values

    @model_validator(mode="after")
    def ordered(self) -> "Draft":
        span(self.start, self.end)
        return self


class CreateInput(AccountInput):
    action_type: Literal["create_event"] = "create_event"
    event: Draft


class UpdateInput(AccountInput):
    action_type: Literal["update_event"] = "update_event"
    event_id: Identifier
    event: Draft


class CancelInput(AccountInput):
    action_type: Literal["cancel_event"] = "cancel_event"
    event_id: Identifier
    comment: str = Field(default="", max_length=1000)


class CalendarResult(ToolModel):
    state: Literal["listed", "read", "availability", "created", "updated", "cancelled"]
    account: Account
    event_id: str = Field(default="", max_length=512)
    data: str = Field(default="", max_length=48000, repr=False)
    # An accepted write is not proof that every invitee received it.
    delivery_verified: Literal[False] = False
