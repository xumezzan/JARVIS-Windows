"""The Asana surface, expressed as a fixed route table.

Verified against the official reference on 2026-09-18:

- https://developers.asana.com/reference/getuser - GET /users/{user_gid}, "me" is allowed
- https://developers.asana.com/reference/getprojects - GET /projects
- https://developers.asana.com/reference/gettasks - GET /tasks; a project is required
  unless both assignee and workspace are given
- https://developers.asana.com/reference/gettask - GET /tasks/{task_gid}
- https://developers.asana.com/reference/createtask - POST /tasks, body {"data": {...}},
  answers 201

Five routes and no more. Asana can delete a task, move it between projects and rewrite a
whole workspace; none of that is guarded here, it is simply absent. A connector cannot
misuse what it has no way to express - the same choice Fireflies made about deletion.
"""

from typing import Literal

from jarvis.connectors.http import Route, ServiceTransport, TransportError

ORIGIN = "https://app.asana.com/api/1.0"
ROUTES = (
    Route("GET", r"/users/me"),
    Route("GET", r"/workspaces"),
    Route("GET", r"/projects"),
    Route("GET", r"/tasks"),
    Route("GET", r"/tasks/[0-9]{1,32}"),
    Route("POST", r"/tasks"),
)

Failure = Literal[
    "asana_credentials",
    "asana_forbidden",
    "asana_missing",
    "asana_rejected",
    "asana_rate_limited",
    "asana_network",
    "asana_response",
]

FAILURES: dict[str, Failure] = {
    "credentials": "asana_credentials",
    "forbidden": "asana_forbidden",
    "not_found": "asana_missing",
    "conflict": "asana_rejected",
    "rejected": "asana_rejected",
    "rate_limited": "asana_rate_limited",
    "route_denied": "asana_rejected",
    "redirect": "asana_network",
    "network": "asana_network",
    "server": "asana_network",
    "response_limit": "asana_response",
    "response_invalid": "asana_response",
    "response_unparsable": "asana_response",
}


class AsanaFailure(Exception):
    """Finite categories only; Asana's own wording never crosses this boundary."""

    def __init__(self, code: Failure) -> None:
        self.code = code
        super().__init__(code)


def transport() -> ServiceTransport:
    return ServiceTransport(
        ORIGIN,
        ROUTES,
        headers={"Accept": "application/json"},
        timeout_seconds=15,
        max_response_bytes=262144,
        # A creation answers 201; nothing here accepts 204, because every call of ours
        # asks for something back and an empty success would have nothing to verify.
        success_statuses=(200, 201),
    )


def translate(error: TransportError) -> AsanaFailure:
    return AsanaFailure(FAILURES.get(error.code, "asana_network"))
