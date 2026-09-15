"""Mail snapshots contain explicit addresses and immutable attachment identities."""

import re
from typing import Annotated, Literal

from pydantic import Field, field_validator

from jarvis.tools.base import ToolModel

Address = Annotated[str, Field(min_length=3, max_length=254)]
Identifier = Annotated[str, Field(min_length=1, max_length=512, pattern=r"^[a-zA-Z0-9_.=+@-]+$")]


def validate_address(value: str) -> str:
    # Deliberately restricted: no display names, aliases, lists or header syntax.
    if not re.fullmatch(
        r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+",
        value,
    ):
        raise ValueError("Укажите точный email, без имени или псевдонима.")
    return value


class Account(ToolModel):
    service: Literal["outlook"] = "outlook"
    user_id: Identifier
    address: Address
    session: str = Field(pattern=r"^[a-f0-9]{32}$")

    _address = field_validator("address")(validate_address)


class Attachment(ToolModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    name: str = Field(min_length=1, max_length=120, pattern=r"^[^\x00-\x1f/\\]+$")
    size: int = Field(ge=0, le=32768)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Message(ToolModel):
    to: list[Address] = Field(min_length=1, max_length=10)
    cc: list[Address] = Field(default_factory=list, max_length=10)
    bcc: list[Address] = Field(default_factory=list, max_length=10)
    subject: str = Field(max_length=500, pattern=r"^[^\r\n\x00]*$")
    body: str = Field(min_length=1, max_length=16000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=3)

    @field_validator("to", "cc", "bcc")
    @classmethod
    def addresses(cls, values: list[str]) -> list[str]:
        for value in values:
            validate_address(value)
        if len({value.casefold() for value in values}) != len(values):
            raise ValueError("Duplicate recipient.")
        return values


class AccountInput(ToolModel):
    account: Account


class ReadInput(AccountInput):
    message_id: Identifier


class ListInput(AccountInput):
    folder: Literal["inbox", "sentitems", "drafts"] = "inbox"
    limit: int = Field(default=5, ge=1, le=10)


class DraftInput(AccountInput):
    action_type: Literal["local_draft"] = "local_draft"
    message: Message


class SaveInput(AccountInput):
    action_type: Literal["remote_draft"] = "remote_draft"
    message: Message


class SendInput(AccountInput):
    action_type: Literal["send_mail"] = "send_mail"
    message: Message


class MailResult(ToolModel):
    state: Literal["account", "listed", "read", "local_draft", "remote_draft", "accepted"]
    account: Account
    message_id: str = Field(default="", max_length=512)
    data: str = Field(default="", max_length=48000, repr=False)
    delivery_verified: Literal[False] = False


class Empty(ToolModel):
    pass
