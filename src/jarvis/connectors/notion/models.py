"""What a Notion answer is allowed to be, and what may be asked of it.

An identifier is a UUID, with or without its dashes, because that is what Notion returns
and nothing else. A page title, a paragraph and a heading are text people wrote: they are
read as data and never as instruction, exactly like a web page or the body of a letter.
"""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from jarvis.tools.base import ToolModel

MAX_ITEMS = 25
MAX_BLOCKS = 200
MAX_DATA = 32000

Uuid = Annotated[
    str,
    Field(
        min_length=32,
        max_length=36,
        pattern=(
            r"^(?:[0-9a-fA-F]{32}|"
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
        ),
    ),
]
Title = Annotated[str, Field(max_length=300, pattern=r"^[^\r\n\x00]*$")]


class Page(ToolModel):
    id: Uuid
    title: Title = ""
    url: str = Field(default="", max_length=400, repr=False)
    archived: bool = False


class SearchInput(ToolModel):
    query: Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^\r\n\x00]+$")]
    limit: int = Field(default=10, ge=1, le=MAX_ITEMS)


class PageInput(ToolModel):
    page: Uuid


class ReadInput(ToolModel):
    page: Uuid
    limit: int = Field(default=100, ge=1, le=MAX_BLOCKS)


class NewPage(ToolModel):
    """A page under another page. Exactly what the owner sees before it is created."""

    parent: Uuid
    title: Annotated[str, Field(min_length=1, max_length=300, pattern=r"^[^\r\n\x00]+$")]
    # Paragraphs, and only paragraphs. A connector that accepted arbitrary block JSON would
    # be letting a model write Notion's own structures, which is a different surface.
    paragraphs: list[Annotated[str, Field(max_length=1800)]] = Field(
        default_factory=list, max_length=20
    )

    @field_validator("paragraphs")
    @classmethod
    def written(cls, values: list[str]) -> list[str]:
        return [value.replace("\x00", "") for value in values]


class CreatePageInput(ToolModel):
    service: Literal["notion"] = "notion"
    action_type: Literal["create_page"] = "create_page"
    page: NewPage


class AppendInput(ToolModel):
    service: Literal["notion"] = "notion"
    action_type: Literal["append"] = "append"
    page: Uuid
    paragraphs: list[Annotated[str, Field(min_length=1, max_length=1800)]] = Field(
        min_length=1, max_length=20
    )

    @field_validator("paragraphs")
    @classmethod
    def written(cls, values: list[str]) -> list[str]:
        return [value.replace("\x00", "") for value in values]


class NotionResult(ToolModel):
    state: Literal["found", "page", "read", "created", "appended"]
    page_id: str = Field(default="", max_length=36)
    url: str = Field(default="", max_length=400, repr=False)
    data: str = Field(default="", max_length=MAX_DATA, repr=False)
    truncated: bool = False
