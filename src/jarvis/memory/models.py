"""Small user-authored labels, never execution identities or transcript archives."""

import re
import unicodedata
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from jarvis.tools.base import ToolModel

Kind = Literal["profession", "application", "project", "contact", "preference"]
KINDS: dict[str, str] = {
    "profession": "Профессия",
    "application": "Приложение",
    "project": "Проект",
    "contact": "Контакт / роль",
    "preference": "Предпочтение",
}
APPS = {"notepad": "Блокнот", "chrome": "Chrome", "vscode": "VS Code"}
PROFILE_TTL = 30 * 86400
SESSION_TTL = 30 * 60
MAX_ENTRIES = 32


def validate_label(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    # A deliberately narrow label editor, not a general note or secret vault.
    if not 1 <= len(value) <= 80 or not all(c.isalnum() or c in " -.,()" for c in value):
        raise ValueError("Use a short ordinary label.")
    if any(len(word) > 24 for word in re.findall(r"\w+", value)) or re.search(r"\d{7}", value):
        raise ValueError("Opaque identifiers are not labels.")
    forbidden = (
        r"password|passwd|secret|token|cookie|credential|bearer|api[ -]?key|"
        r"парол|секрет|токен|куки|ключ|approval|hwnd|\bpid\b|dom[ -]?id"
    )
    if re.search(forbidden, value, re.IGNORECASE):
        raise ValueError("Credentials and execution identities are excluded.")
    return value


class Entry(ToolModel):
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[a-f0-9]{32}$")
    kind: Kind
    label: str = Field(repr=False)
    value: str = Field(repr=False)
    updated: int = Field(ge=0)
    expires: int = Field(ge=0)

    _labels = field_validator("label", "value")(validate_label)

    @model_validator(mode="after")
    def constraints(self) -> "Entry":
        if not 0 < self.expires - self.updated <= PROFILE_TTL:
            raise ValueError("Invalid retention.")
        if self.kind == "application" and self.value not in APPS:
            raise ValueError("Choose a supported application.")
        return self


class Hint(ToolModel):
    kind: Kind
    label: str = Field(repr=False)
    value: str = Field(repr=False)

    _labels = field_validator("label", "value")(validate_label)

    @model_validator(mode="after")
    def app(self) -> "Hint":
        if self.kind == "application" and self.value not in APPS:
            raise ValueError("Choose a supported application.")
        return self


class MemoryContext(ToolModel):
    """Only explicitly selected fields; no store IDs, timestamps, targets or permissions."""

    profile: tuple[Hint, ...] = Field(default=(), max_length=8, repr=False)
    session: tuple[Hint, ...] = Field(default=(), max_length=6, repr=False)

    @property
    def empty(self) -> bool:
        return not self.profile and not self.session
