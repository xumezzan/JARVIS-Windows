"""What a Teams answer is allowed to be, and what may be asked of it.

A chat identifier is Teams' own thread string and nothing else: a model that invents one
invents a thread, not a path. A message identifier is the decimal stamp Teams returns.

Topics, names and message text are written by people. They are read as data and never as
instruction, exactly like the body of a letter: a chat message that says "delete the
production database" describes what somebody typed into Teams, and it carries no more
authority than any other page the assistant reads.
"""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from jarvis.mail.models import Account
from jarvis.tools.base import ToolModel

MAX_ITEMS = 50
MAX_TEXT = 4000
MAX_DATA = 32000

CHAT_PATTERN = r"^19:[A-Za-z0-9._-]{1,180}@(?:thread\.v2|thread\.tacv2|unq\.gbl\.spaces)$"
ChatId = Annotated[str, Field(min_length=5, max_length=200, pattern=CHAT_PATTERN)]
MessageId = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[0-9]+$")]
Label = Annotated[str, Field(default="", max_length=200, pattern=r"^[^\r\n\x00]*$")]
Kind = Literal["oneOnOne", "group", "meeting", "unknown"]


def plain(value: str) -> str:
    """Text a person typed, kept as text: no null bytes, no other control characters.

    Newlines survive, because a message with paragraphs is a normal message and losing
    them would silently rewrite what the owner approved.
    """
    return "".join(char for char in value if char == "\n" or ord(char) >= 32)


class Chat(ToolModel):
    """One conversation, as observed. Never assembled from a plan."""

    id: ChatId
    topic: Label = ""
    kind: Kind = "unknown"
    updated: str = Field(default="", max_length=32)


class Message(ToolModel):
    id: MessageId
    author: Label = ""
    created: str = Field(default="", max_length=32)
    text: str = Field(default="", max_length=MAX_TEXT, repr=False)


class TeamsInput(ToolModel):
    account: Account


class ChatsInput(TeamsInput):
    limit: int = Field(default=15, ge=1, le=MAX_ITEMS)


class MessagesInput(TeamsInput):
    chat: ChatId
    limit: int = Field(default=20, ge=1, le=MAX_ITEMS)


class Note(ToolModel):
    """The message as the owner sees it in the confirmation, and exactly what is sent."""

    chat: ChatId
    text: Annotated[str, Field(min_length=1, max_length=MAX_TEXT)]

    @field_validator("text")
    @classmethod
    def written(cls, value: str) -> str:
        cleaned = plain(value).strip()
        if not cleaned:
            raise ValueError("A message needs words.")
        return cleaned


class DraftInput(TeamsInput):
    message: Note


class SendInput(TeamsInput):
    service: Literal["teams"] = "teams"
    action_type: Literal["send_message"] = "send_message"
    message: Note


class TeamsResult(ToolModel):
    state: Literal["chats", "messages", "draft", "sent"]
    account: Account
    chat: str = Field(default="", max_length=200)
    message_id: str = Field(default="", max_length=32)
    data: str = Field(default="", max_length=MAX_DATA, repr=False)
    truncated: bool = False
