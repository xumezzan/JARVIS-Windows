"""The OneDrive and Excel surface, expressed as a fixed route table.

Verified against the official reference on 2026-09-18:

- https://learn.microsoft.com/graph/api/driveitem-search -
  GET /me/drive/root/search(q='{text}'), delegated `Files.Read`
- https://learn.microsoft.com/graph/api/worksheet-list -
  GET /me/drive/items/{id}/workbook/worksheets, delegated `Files.ReadWrite`
- https://learn.microsoft.com/graph/api/worksheet-usedrange -
  GET .../worksheets/{id|name}/usedRange
- https://learn.microsoft.com/graph/api/range-get -
  GET .../worksheets/{id|name}/range(address='<address>')
- https://learn.microsoft.com/graph/api/range-update -
  PATCH the same address, body {"values": [[...]]}, answers 200 with the stored range
- https://learn.microsoft.com/graph/api/resources/excel - sessions, and the rule this
  connector relies on: "If you don't use a session header, changes made during the API
  call *are* persisted to the file."

Two things were read there rather than remembered, and both matter. The Excel REST API
does not serve consumer OneDrive at all - only work and school drives - so a personal
account will answer for its files and refuse their contents. And a request without a
`workbook-session-id` still saves: a session is a performance decision, not the thing
that makes a write stick.

Five routes. Excel can add sheets, delete them, write formulas, build charts, sort and
filter tables; none of that is guarded here, it is absent. Uploading, moving, sharing and
deleting a file are absent for the same reason - this connector reads the drive and writes
cells, and nothing it cannot express can be misused.
"""

from typing import Literal

from jarvis.connectors.http import Route, ServiceTransport, TransportError

ORIGIN = "https://graph.microsoft.com/v1.0"
# A drive item identifier as OneDrive writes it, and nothing that could become a path.
ITEM = r"[A-Za-z0-9!._-]{1,200}"
# A worksheet name and an A1 address, both percent-encoded before they reach a URL.
SHEET = r"'[A-Za-z0-9%._~-]{1,150}'"
ADDRESS = r"'[A-Za-z0-9%]{2,40}'"
QUERY = r"'[A-Za-z0-9%._~-]{1,200}'"
WORKSHEET = rf"/me/drive/items/{ITEM}/workbook/worksheets"
ROUTES = (
    Route("GET", rf"/me/drive/root/search\(q={QUERY}\)"),
    Route("GET", WORKSHEET),
    Route("GET", rf"{WORKSHEET}\({SHEET}\)/usedRange"),
    Route("GET", rf"{WORKSHEET}\({SHEET}\)/range\(address={ADDRESS}\)"),
    Route("PATCH", rf"{WORKSHEET}\({SHEET}\)/range\(address={ADDRESS}\)"),
)

Failure = Literal[
    "drive_credentials",
    "drive_forbidden",
    "drive_missing",
    "drive_rejected",
    "drive_rate_limited",
    "drive_network",
    "drive_response",
    "drive_account_changed",
]

FAILURES: dict[str, Failure] = {
    "credentials": "drive_credentials",
    "forbidden": "drive_forbidden",
    # A workbook on a personal drive answers 404 for its own worksheets, and so does a
    # file that is not a workbook at all. The message says both.
    "not_found": "drive_missing",
    "conflict": "drive_rejected",
    "rejected": "drive_rejected",
    "rate_limited": "drive_rate_limited",
    "route_denied": "drive_rejected",
    "redirect": "drive_network",
    "network": "drive_network",
    "server": "drive_network",
    "response_limit": "drive_response",
    "response_invalid": "drive_response",
    "response_unparsable": "drive_response",
}


class DriveFailure(Exception):
    """Finite categories only; Microsoft's own wording never crosses this boundary."""

    def __init__(self, code: Failure) -> None:
        self.code = code
        super().__init__(code)


def transport() -> ServiceTransport:
    return ServiceTransport(
        ORIGIN,
        ROUTES,
        headers={"Accept": "application/json"},
        timeout_seconds=20,
        # A range carries values, text, formulas and formats for every cell, so one answer
        # is larger than a listing of anything else here.
        max_response_bytes=524288,
        success_statuses=(200,),
    )


def translate(error: TransportError) -> DriveFailure:
    return DriveFailure(FAILURES.get(error.code, "drive_network"))
