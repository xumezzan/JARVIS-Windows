"""Synthetic Outlook fixtures. Never access native credentials or live mail."""

import asyncio
from typing import Literal

from jarvis.mail.credentials import Credential, MailFailure


class FakeCredentials:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.surfaces: list[str] = []
        self.home = "fixture-home"
        self.fail = False
        # Surfaces the owner never consented to: asking for one fails the way Microsoft
        # fails it, rather than quietly handing out a token for scopes nobody granted.
        self.refused: set[str] = set()
        self.delay = 0.0

    async def call(
        self,
        operation: Literal["connect", "silent", "consent", "disconnect"],
        client_id: str,
        home_id: str = "",
        surface: str = "mail",
    ) -> Credential:
        self.calls.append(operation)
        self.surfaces.append(surface)
        await asyncio.sleep(self.delay)
        if self.fail or surface in self.refused:
            raise MailFailure("mail_credentials")
        return Credential(self.home, "synthetic-noncredential")


class FakeGraph:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.writes: list[dict[str, object]] = []
        self.profile = {"id": "fixture-user", "mail": "owner@example.test"}
        self.fail_write = False
        self.change_draft = False
        self.delay = 0.0
        self.subject = "Fixture subject"
        self.selected = ""
        self.draft: dict[str, object] = {}

    async def request(
        self,
        token: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        self.calls.append((method, path))
        if method == "POST":
            assert payload is not None
            self.writes.append(payload)
            await asyncio.sleep(self.delay)
            if self.fail_write:
                raise MailFailure("mail_network")
            if path == "/me/sendMail":
                return 202, {}
            self.draft = {**payload, "id": "fixture-message", "isDraft": True}
            return 201, self.draft
        if path == "/me":
            return 200, dict(self.profile)
        if path.endswith("/attachments"):
            return 200, {"value": self.draft.get("attachments", [])}
        if path.startswith("/me/mailFolders/"):
            self.selected = (params or {}).get("$select", "")
            return 200, {
                "value": [
                    {
                        "id": "fixture-message",
                        "subject": self.subject,
                        "from": {"emailAddress": {"address": "sender@example.test"}},
                        "receivedDateTime": "2026-09-17T09:00:00Z",
                        "isDraft": False,
                        "isRead": False,
                        # Fields nobody asked for do not cross the boundary, whatever
                        # Graph decides to put in an answer.
                        "bodyPreview": "не должно проходить",
                    }
                ]
            }
        if self.change_draft:
            return 200, {**self.draft, "subject": "tampered"}
        return 200, self.draft or {
            "id": "fixture-message",
            "subject": self.subject,
            "body": {"contentType": "text", "content": "Untrusted fixture text"},
        }
