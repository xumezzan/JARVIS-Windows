"""Entities that the same person or project has across services, and how they link.

Unlike the label profile in `jarvis.memory`, this store deliberately holds external
identifiers: linking John in Outlook to John in Asana is the point. That makes one rule
load-bearing. An identifier is kept for trusted code — deduplication, lookup and
verification — but it never reaches the planner's context. `Hint` carries the name,
the aliases and the names of the services where an entity is known, never the identifier
itself, so a model cannot copy an id it has not observed in the current task and hand it
to a writing tool. Knowledge answers where to look; only an observation says where to write.

Nothing here stores a credential, a token or an execution handle.
"""

import re
import unicodedata
from typing import Literal

from pydantic import Field, field_validator

from jarvis.tools.base import ToolModel

EntityType = Literal[
    "person",
    "company",
    "project",
    "task",
    "meeting",
    "invoice",
    "document",
    "conversation",
    "customer",
    "account",
]

SERVICE = re.compile(r"[a-z][a-z0-9_]*")
TOOL = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:=+@|/-]{1,200}")
RUN = re.compile(r"[a-f0-9]{32}")
SECRETS = re.compile(
    r"password|passwd|secret|token|cookie|credential|bearer|api[ -]?key|"
    r"парол|секрет|токен|куки|ключ",
    re.IGNORECASE,
)

MAX_ALIASES = 8
MAX_REFERENCES = 12
MAX_HINTS = 12

Predicate = Literal[
    "works_at",
    "member_of",
    "participates_in",
    "assigned_to",
    "about",
    "owns",
    "mentions",
]


def validate_tool(value: str) -> str:
    """Provenance is a registered tool name: an entity must come from a real observation."""
    if not TOOL.fullmatch(value):
        raise ValueError("Provenance must name the tool that observed it.")
    return value


def validate_run(value: str) -> str:
    if not RUN.fullmatch(value):
        raise ValueError("Invalid run identifier.")
    return value


def validate_name(value: str) -> str:
    """A human label: a person, company or project as it is written by people."""
    value = unicodedata.normalize("NFKC", value).strip()
    if not 1 <= len(value) <= 120:
        raise ValueError("A name must be short and non-empty.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("A name holds no control characters.")
    if SECRETS.search(value):
        raise ValueError("Credentials are not names.")
    return value


class Reference(ToolModel):
    """One service's own identity for an entity, with the observation that produced it."""

    service: str = Field(max_length=40)
    value: str = Field(max_length=200, repr=False)
    observed: str = Field(max_length=100)
    run: str = Field(repr=False)

    @field_validator("service")
    @classmethod
    def service_name(cls, value: str) -> str:
        if not SERVICE.fullmatch(value):
            raise ValueError("Invalid service name.")
        return value

    @field_validator("value")
    @classmethod
    def identifier(cls, value: str) -> str:
        if not IDENTIFIER.fullmatch(value) or SECRETS.search(value):
            raise ValueError("Invalid external identifier.")
        return value

    _tool = field_validator("observed")(validate_tool)
    _run = field_validator("run")(validate_run)

    @property
    def key(self) -> str:
        return f"{self.service}:{self.value}"


class EntityDraft(ToolModel):
    """What a connector observed. The store assigns identity and time."""

    type: EntityType
    name: str = Field(max_length=120)
    aliases: tuple[str, ...] = Field(default=(), max_length=MAX_ALIASES)
    external: tuple[Reference, ...] = Field(default=(), max_length=MAX_REFERENCES)

    _names = field_validator("name")(validate_name)

    @field_validator("aliases")
    @classmethod
    def alias_labels(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        names = tuple(validate_name(value) for value in values)
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("Duplicate alias.")
        return names

    @field_validator("external")
    @classmethod
    def references(cls, values: tuple[Reference, ...]) -> tuple[Reference, ...]:
        if len({reference.key for reference in values}) != len(values):
            raise ValueError("Duplicate external identifier.")
        return values


class Entity(EntityDraft):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    updated: int = Field(ge=0)

    @property
    def services(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(reference.service for reference in self.external))

    def hint(self) -> "Hint":
        return Hint(type=self.type, name=self.name, aliases=self.aliases, services=self.services)


class Relationship(ToolModel):
    subject: str = Field(pattern=r"^[a-f0-9]{32}$")
    predicate: Predicate
    object: str = Field(pattern=r"^[a-f0-9]{32}$")
    observed: str = Field(max_length=100)
    run: str = Field(repr=False)
    updated: int = Field(ge=0)

    _tool = field_validator("observed")(validate_tool)
    _run = field_validator("run")(validate_run)

    @property
    def key(self) -> str:
        return f"{self.subject}:{self.predicate}:{self.object}"


class Hint(ToolModel):
    """The planner's view: where an entity is known, never how it is addressed there."""

    type: EntityType
    name: str = Field(max_length=120)
    aliases: tuple[str, ...] = Field(default=(), max_length=MAX_ALIASES)
    services: tuple[str, ...] = Field(default=(), max_length=MAX_REFERENCES)


class KnowledgeContext(ToolModel):
    entities: tuple[Hint, ...] = Field(default=(), max_length=MAX_HINTS, repr=False)

    @property
    def empty(self) -> bool:
        return not self.entities
