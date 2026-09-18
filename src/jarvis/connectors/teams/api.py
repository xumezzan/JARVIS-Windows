"""The Microsoft Teams surface, expressed as a fixed route table.

Verified against the official reference on 2026-09-18:

- https://learn.microsoft.com/graph/api/chat-list - GET /me/chats, delegated
  Chat.ReadBasic / Chat.Read / Chat.ReadWrite, `$top` at most 50
- https://learn.microsoft.com/graph/api/chat-list-messages - GET /chats/{id}/messages,
  delegated Chat.Read, `$top` at most 50, `$orderby` descending only
- https://learn.microsoft.com/graph/api/chatmessage-get - GET /chats/{id}/messages/{id},
  delegated Chat.Read
- https://learn.microsoft.com/graph/api/chat-post-messages - POST /chats/{id}/messages,
  delegated ChatMessage.Send, answers 201 with the created message

Four routes, and chats only. Channels are absent on purpose: reading them needs
`ChannelMessage.Read.All`, which is tenant admin consent — an external gate that
`AGENTS.md` says is raised before a phase rather than inside it. Chats need no `.All`
scope at all, so this connector works on an ordinary work account today.

Teams can also delete a message, edit one, add members and create chats. None of that is
guarded here; it is simply absent, the same choice Asana and Notion made.
"""

from typing import Literal

from jarvis.connectors.http import Route, ServiceTransport, TransportError

ORIGIN = "https://graph.microsoft.com/v1.0"
# A chat identifier as Teams writes it: a thread, a meeting thread, or the synthetic
# identifier of a one-to-one conversation. Nothing else reaches the network.
CHAT = r"19:[A-Za-z0-9._-]{1,180}@(?:thread\.v2|thread\.tacv2|unq\.gbl\.spaces)"
MESSAGE = r"[0-9]{1,32}"
ROUTES = (
    Route("GET", r"/me/chats"),
    Route("GET", rf"/chats/(?:{CHAT})/messages"),
    Route("GET", rf"/chats/(?:{CHAT})/messages/(?:{MESSAGE})"),
    Route("POST", rf"/chats/(?:{CHAT})/messages"),
)

Failure = Literal[
    "teams_credentials",
    "teams_forbidden",
    "teams_missing",
    "teams_rejected",
    "teams_rate_limited",
    "teams_network",
    "teams_response",
    "teams_account_changed",
]

FAILURES: dict[str, Failure] = {
    # A missing consent and an expired token look the same from here, and the message says
    # both: connect Teams on the Outlook tab.
    "credentials": "teams_credentials",
    "forbidden": "teams_forbidden",
    "not_found": "teams_missing",
    "conflict": "teams_rejected",
    "rejected": "teams_rejected",
    "rate_limited": "teams_rate_limited",
    "route_denied": "teams_rejected",
    "redirect": "teams_network",
    "network": "teams_network",
    "server": "teams_network",
    "response_limit": "teams_response",
    "response_invalid": "teams_response",
    "response_unparsable": "teams_response",
}


class TeamsFailure(Exception):
    """Finite categories only; Microsoft's own wording never crosses this boundary."""

    def __init__(self, code: Failure) -> None:
        self.code = code
        super().__init__(code)


def transport() -> ServiceTransport:
    return ServiceTransport(
        ORIGIN,
        ROUTES,
        headers={"Accept": "application/json"},
        timeout_seconds=15,
        # Chat messages arrive as HTML with quoted history, so a page of them is larger
        # than a page of calendar events.
        max_response_bytes=524288,
        success_statuses=(200, 201),
    )


def translate(error: TransportError) -> TeamsFailure:
    return TeamsFailure(FAILURES.get(error.code, "teams_network"))
