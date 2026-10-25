"""What a OneDrive answer is allowed to be, and what may be asked of a workbook.

An identifier is the drive's own; a sheet name follows Excel's own rule; an address is
A1 notation and nothing else. A model that invents one of these invents a cell, not a path.

Cells are values people will read as a report: text and numbers. Formulas are refused
here rather than sanitised somewhere later - a connector that accepted `=` would be
letting a planned step compute in the owner's workbook, which is a different surface.
"""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from jarvis.mail.models import Account
from jarvis.tools.base import ToolModel

MAX_ITEMS = 25
MAX_ROWS = 50
MAX_COLUMNS = 20
MAX_CELL = 255
MAX_DATA = 32000

ItemId = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9!._-]+$")]
# Excel's own rule: up to 31 characters, and never : \ / ? * [ ]. The apostrophe is left
# out too, because the name is written inside a quoted OData segment.
Sheet = Annotated[str, Field(min_length=1, max_length=31, pattern=r"^[^\r\n\x00:\\/?*\[\]']+$")]
Address = Annotated[
    str, Field(pattern=r"^[A-Za-z]{1,3}[0-9]{1,7}(?::[A-Za-z]{1,3}[0-9]{1,7})?$", max_length=20)
]
Label = Annotated[str, Field(max_length=200, pattern=r"^[^\r\n\x00]*$")]
# What a cell may hold on the way in. `bool` is deliberately absent: strict validation
# keeps it out of an int field, and TRUE/FALSE is not something a report needs written.
Cell = str | int | float

WORKBOOK = (".xlsx", ".xlsm")


def column(letters: str) -> int:
    """A1 column letters as a number: A is 1, Z is 26, AA is 27."""
    index = 0
    for letter in letters.upper():
        index = index * 26 + (ord(letter) - 64)
    return index


def cell(part: str) -> tuple[int, int]:
    """One A1 reference as (column, row)."""
    letters = part.rstrip("0123456789")
    return column(letters), int(part[len(letters) :])


def corners(address: str) -> tuple[int, int]:
    """How many rows and columns an address covers. A single cell covers one of each."""
    first, _, last = address.partition(":")
    left, top = cell(first)
    right, bottom = cell(last or first)
    return abs(bottom - top) + 1, abs(right - left) + 1


class File(ToolModel):
    """One file as observed. `workbook` says whether Excel can be asked about it at all."""

    id: ItemId
    name: Label = ""
    modified: str = Field(default="", max_length=32)
    size: int = Field(default=0, ge=0)
    workbook: bool = False


class Worksheet(ToolModel):
    id: str = Field(default="", max_length=64)
    name: Label = ""
    position: int = Field(default=0, ge=0)
    visible: bool = True


class DriveInput(ToolModel):
    account: Account


class SearchInput(DriveInput):
    # No apostrophe and no backslash: the text is written inside a quoted OData segment,
    # and a quote in it would end that segment early.
    query: Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[^'\\\r\n\x00]+$")]
    limit: int = Field(default=10, ge=1, le=MAX_ITEMS)


class SheetsInput(DriveInput):
    item: ItemId


class ReadInput(DriveInput):
    item: ItemId
    sheet: Sheet
    # Without an address the sheet's used range is read, which is what "what is in it"
    # means for a report nobody has measured yet.
    address: Address | None = None


class Grid(ToolModel):
    """The cells as the owner sees them in the confirmation, and exactly what is sent."""

    item: ItemId
    sheet: Sheet
    address: Address
    values: list[list[Annotated[Cell, Field(union_mode="left_to_right")]]] = Field(
        min_length=1, max_length=MAX_ROWS
    )

    @field_validator("values")
    @classmethod
    def written(cls, rows: list[list[Cell]]) -> list[list[Cell]]:
        if any(not row or len(row) > MAX_COLUMNS for row in rows):
            raise ValueError("A row needs cells, and no more than twenty of them.")
        if len({len(row) for row in rows}) != 1:
            raise ValueError("Every row needs the same number of cells.")
        for row in rows:
            for value in row:
                if isinstance(value, str) and (len(value) > MAX_CELL or value.startswith("=")):
                    raise ValueError("A cell holds text or a number, never a formula.")
        return rows

    @model_validator(mode="after")
    def fits(self) -> "Grid":
        # Excel copies a single value across a whole range when the shapes disagree, so a
        # mismatch here would quietly fill cells nobody meant to touch.
        if corners(self.address) != (len(self.values), len(self.values[0])):
            raise ValueError("The values do not fill the address exactly.")
        return self


class WriteInput(DriveInput):
    service: Literal["onedrive"] = "onedrive"
    action_type: Literal["write_range"] = "write_range"
    range: Grid


class DriveResult(ToolModel):
    state: Literal["found", "sheets", "read", "written"]
    account: Account
    item: str = Field(default="", max_length=200)
    sheet: str = Field(default="", max_length=31)
    address: str = Field(default="", max_length=64)
    data: str = Field(default="", max_length=MAX_DATA, repr=False)
    truncated: bool = False
