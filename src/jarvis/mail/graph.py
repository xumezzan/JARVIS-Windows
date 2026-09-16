"""Single-shot Graph transport: fixed origin, no cookies/proxies/redirects or retries.

The wire behaviour now comes from the shared connector transport. Only the Graph route
table, its headers and the mapping to mail's own finite error codes live here, so the
Outlook surface stays exactly as narrow as before.
"""

from typing import Protocol

from jarvis.connectors.http import Code, Route, ServiceTransport, TransportError
from jarvis.mail.credentials import MailFailure

ROUTES = (
    Route("GET", r"/me"),
    Route("GET", r"/me/messages/[A-Za-z0-9%_.=+-]+"),
    Route("GET", r"/me/messages/[A-Za-z0-9%_.=+-]+/attachments"),
    Route("GET", r"/me/mailFolders/(?:inbox|sentitems|drafts)/messages"),
    Route("POST", r"/me/messages"),
    Route("POST", r"/me/sendMail"),
)

# Every other transport category becomes mail_network: a mail user cannot act on the
# difference between a refused route upstream, a redirect and an unreachable service.
FAILURES: dict[Code, str] = {
    "route_denied": "mail_request",
    "credentials": "mail_credentials",
    "response_limit": "mail_response_limit",
    "response_invalid": "mail_response",
}


class Graph(Protocol):
    async def request(
        self,
        token: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]: ...


class GraphTransport:
    def __init__(self) -> None:
        self.transport = ServiceTransport(
            "https://graph.microsoft.com/v1.0",
            ROUTES,
            headers={"Prefer": 'outlook.body-content-type="text", IdType="ImmutableId"'},
            timeout_seconds=12,
            max_response_bytes=262144,
            success_statuses=(200, 201, 202),
            allow_compression=True,
        )

    async def request(
        self,
        token: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        try:
            return await self.transport.request(token, method, path, payload, params)
        except TransportError as error:
            raise MailFailure(FAILURES.get(error.code, "mail_network")) from None
