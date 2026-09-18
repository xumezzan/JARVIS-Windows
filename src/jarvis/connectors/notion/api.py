"""The Notion surface, expressed as a fixed route table.

Verified against the official reference on 2026-09-18:

- https://developers.notion.com/reference/intro - base https://api.notion.com, bearer token
- https://developers.notion.com/reference/post-search - POST /v1/search, title search over
  what the connection has been shared with
- https://developers.notion.com/reference/retrieve-a-page - GET /v1/pages/{page_id}
- https://developers.notion.com/reference/get-block-children - GET /v1/blocks/{id}/children
- https://developers.notion.com/reference/post-page - POST /v1/pages, parent is a page or a
  data source
- https://developers.notion.com/reference/patch-block-children - PATCH /v1/blocks/{id}/children

Two things were read there rather than remembered, and both would have been wrong from
memory: the current version header is 2026-03-11, and databases now appear as data sources.

Five routes. Notion can delete blocks, archive pages, rewrite properties and query data
sources; none of that is guarded here, it is absent. A page is created only under another
page, never under a data source: that would need the schema of somebody's database, and
guessing which property holds the title is how a connector writes into the wrong column.
"""

from typing import Literal

from jarvis.connectors.http import Route, ServiceTransport, TransportError

ORIGIN = "https://api.notion.com"
# Pinned on purpose. Notion versions its API by date, and an unpinned client changes
# behaviour on their schedule rather than on ours.
VERSION = "2026-03-11"
UUID = (
    r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
ROUTES = (
    Route("POST", r"/v1/search"),
    Route("GET", rf"/v1/pages/(?:{UUID})"),
    Route("GET", rf"/v1/blocks/(?:{UUID})/children"),
    Route("POST", r"/v1/pages"),
    Route("PATCH", rf"/v1/blocks/(?:{UUID})/children"),
)

Failure = Literal[
    "notion_credentials",
    "notion_forbidden",
    "notion_missing",
    "notion_rejected",
    "notion_rate_limited",
    "notion_network",
    "notion_response",
]

FAILURES: dict[str, Failure] = {
    "credentials": "notion_credentials",
    # Notion answers 404 for a page the connection was never shared with, so "missing" and
    # "not shared with me" are the same fact from here. The message says both.
    "not_found": "notion_missing",
    "forbidden": "notion_forbidden",
    "conflict": "notion_rejected",
    "rejected": "notion_rejected",
    "rate_limited": "notion_rate_limited",
    "route_denied": "notion_rejected",
    "redirect": "notion_network",
    "network": "notion_network",
    "server": "notion_network",
    "response_limit": "notion_response",
    "response_invalid": "notion_response",
    "response_unparsable": "notion_response",
}


class NotionFailure(Exception):
    """Finite categories only; Notion's own wording never crosses this boundary."""

    def __init__(self, code: Failure) -> None:
        self.code = code
        super().__init__(code)


def transport() -> ServiceTransport:
    return ServiceTransport(
        ORIGIN,
        ROUTES,
        headers={"Accept": "application/json", "Notion-Version": VERSION},
        timeout_seconds=15,
        max_response_bytes=524288,
        success_statuses=(200,),
    )


def translate(error: TransportError) -> NotionFailure:
    return NotionFailure(FAILURES.get(error.code, "notion_network"))
