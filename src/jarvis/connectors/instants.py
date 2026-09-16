"""One shape for every instant that crosses a connector boundary.

Shared rather than repeated per service, because a calendar hour and a meeting hour have
to mean the same thing before anything can be linked across the two. Only UTC in one exact
written form is accepted: a connector that took "tomorrow afternoon" would have to guess a
timezone and a working day, and turning what the user said into a concrete range belongs to
the planner, not to a schema.
"""

import re
from datetime import UTC, datetime
from typing import Annotated

from pydantic import Field

SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
FORMAT = "%Y-%m-%dT%H:%M:%SZ"
MAX_RANGE_DAYS = 62

Instant = Annotated[str, Field(min_length=20, max_length=20)]


def validate_instant(value: str) -> str:
    if not SHAPE.fullmatch(value):
        raise ValueError("Укажите время в UTC в виде 2026-09-16T14:00:00Z.")
    try:
        datetime.strptime(value, FORMAT).replace(tzinfo=UTC)
    except ValueError:
        raise ValueError("Несуществующая дата или время.") from None
    return value


def parse(value: str) -> datetime:
    return datetime.strptime(value, FORMAT).replace(tzinfo=UTC)


def render(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(FORMAT)


def span(start: str, end: str, days: int = MAX_RANGE_DAYS) -> None:
    first, last = parse(start), parse(end)
    if last <= first:
        raise ValueError("Конец интервала должен быть позже начала.")
    if (last - first).days > days:
        raise ValueError("Интервал слишком широкий.")
