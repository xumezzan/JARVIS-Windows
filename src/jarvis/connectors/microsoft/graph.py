"""The Microsoft Graph surface this connector is allowed to touch.

The route table is the whole permission boundary at the transport level: a path that is
not listed here cannot be reached even by a bug, because the shared transport refuses it
before a connection is opened. Calendar declares its own routes rather than widening the
mail ones, so neither surface can grow the other's reach.
"""

from typing import Literal

from jarvis.connectors.http import Route, ServiceTransport, TransportError

ORIGIN = "https://graph.microsoft.com/v1.0"
EVENT = r"/me/events/[A-Za-z0-9%_.=+-]+"

ROUTES = (
    Route("GET", r"/me/calendarView"),
    Route("GET", EVENT),
    Route("POST", r"/me/events"),
    Route("PATCH", EVENT),
    Route("POST", EVENT + r"/cancel"),
    Route("POST", r"/me/calendar/getSchedule"),
)

Code = Literal[
    "calendar_request",
    "calendar_credentials",
    "calendar_denied",
    "calendar_missing",
    "calendar_network",
    "calendar_response",
    "calendar_account_changed",
    "calendar_unavailable",
]


class CalendarFailure(Exception):
    """Finite public categories; no server text ever reaches the interface or the audit."""

    def __init__(self, code: Code) -> None:
        self.code = code
        super().__init__(code)


FAILURES: dict[str, Code] = {
    "route_denied": "calendar_request",
    "credentials": "calendar_credentials",
    "forbidden": "calendar_denied",
    "not_found": "calendar_missing",
    "response_invalid": "calendar_response",
    "response_limit": "calendar_response",
}


def transport() -> ServiceTransport:
    return ServiceTransport(
        ORIGIN,
        ROUTES,
        headers={"Prefer": 'outlook.timezone="UTC", IdType="ImmutableId"'},
        timeout_seconds=12,
        max_response_bytes=262144,
        success_statuses=(200, 201, 202, 204),
        allow_compression=True,
    )


def translate(error: TransportError) -> CalendarFailure:
    return CalendarFailure(FAILURES.get(error.code, "calendar_network"))
