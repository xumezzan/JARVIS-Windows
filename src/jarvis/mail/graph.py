"""Single-shot Graph transport: fixed origin, no cookies/proxies/redirects or retries."""

import json
import re
import ssl
from typing import Protocol

import aiohttp
import certifi

from jarvis.mail.credentials import MailFailure


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
    async def request(
        self,
        token: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        if not (
            (
                method == "GET"
                and re.fullmatch(
                    r"/me(?:/messages/[A-Za-z0-9%_.=+-]+(?:/attachments)?|/mailFolders/(?:inbox|sentitems|drafts)/messages)?",
                    path,
                )
            )
            or (method == "POST" and path in ("/me/messages", "/me/sendMail"))
        ):
            raise MailFailure("mail_request")
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=12),
                    trust_env=False,
                    cookie_jar=aiohttp.DummyCookieJar(),
                    connector=aiohttp.TCPConnector(
                        ssl=ssl.create_default_context(cafile=certifi.where())
                    ),
                ) as session,
                session.request(
                    method,
                    "https://graph.microsoft.com/v1.0" + path,
                    headers={
                        "Authorization": "Bearer " + token,
                        "Prefer": 'outlook.body-content-type="text", IdType="ImmutableId"',
                    },
                    json=payload,
                    params=params,
                    allow_redirects=False,
                ) as response,
            ):
                raw = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    raw.extend(chunk)
                    if len(raw) > 262144:
                        raise MailFailure("mail_response_limit")
                if response.status not in (200, 201, 202):
                    raise MailFailure(
                        "mail_credentials" if response.status == 401 else "mail_network"
                    )
                data = json.loads(raw) if raw else {}
                if not isinstance(data, dict):
                    raise MailFailure("mail_response")
                return response.status, data
        except MailFailure:
            raise
        except Exception:
            raise MailFailure("mail_network") from None
