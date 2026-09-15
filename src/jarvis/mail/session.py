"""One explicit account/session. Local drafts and attachment bytes live only in RAM."""

import base64
import hashlib
import json
from threading import RLock
from urllib.parse import quote
from uuid import uuid4

from jarvis.mail.credentials import Credentials, MailFailure, ProcessCredentials
from jarvis.mail.graph import Graph, GraphTransport
from jarvis.mail.models import Account, Attachment, MailResult, Message
from jarvis.tools.base import ExecutionContext


class MailSession:
    def __init__(self, credentials: Credentials | None = None, graph: Graph | None = None) -> None:
        self.credentials = credentials or ProcessCredentials()
        self.graph = graph or GraphTransport()
        self.account: Account | None = None
        self._client = ""
        self._home = ""
        self._lock = RLock()
        self._attachments: dict[str, tuple[Attachment, bytes]] = {}
        self._drafts: dict[str, Message] = {}

    def detach(self) -> None:
        with self._lock:
            self.account = None
            self._client = self._home = ""
            self._attachments.clear()
            self._drafts.clear()

    async def connect(self, client_id: str, context: ExecutionContext) -> Account:
        self.detach()
        credential = await self.credentials.call("connect", client_id)
        await context.checkpoint()
        _, profile = await self.graph.request(
            credential.token, "GET", "/me", params={"$select": "id,mail,userPrincipalName"}
        )
        result = Account.model_validate(
            {
                "user_id": profile.get("id"),
                "address": profile.get("mail") or profile.get("userPrincipalName"),
                "session": uuid4().hex,
            }
        )
        await context.checkpoint()
        with self._lock:
            self._client, self._home, self.account = client_id, credential.home_id, result
        return result

    async def disconnect(self) -> None:
        self.detach()
        await self.credentials.call("disconnect", "")

    def matches(self, account: Account, message: Message | None = None) -> bool:
        with self._lock:
            return self.account == account and (
                message is None
                or all(
                    self._attachments.get(item.id, (None, b""))[0] == item
                    for item in message.attachments
                )
            )

    def attach(self, name: str, content: bytes) -> Attachment:
        with self._lock:
            if self.account is None or len(self._attachments) >= 3:
                raise MailFailure("mail_attachment_limit")
            item = Attachment(
                id=uuid4().hex,
                name=name,
                size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
            self._attachments[item.id] = (item, bytes(content))
            return item

    def clear_attachments(self) -> None:
        with self._lock:
            self._attachments.clear()

    def local_draft(self, account: Account, message: Message) -> MailResult:
        with self._lock:
            if not self.matches(account, message) or len(self._drafts) >= 10:
                raise MailFailure("mail_draft_limit")
            key = uuid4().hex
            self._drafts[key] = message
            return MailResult(
                state="local_draft", account=account, message_id=key, data=message.model_dump_json()
            )

    def draft_matches(self, key: str, message: Message) -> bool:
        with self._lock:
            return self._drafts.get(key) == message

    def drafts(self) -> tuple[tuple[str, Message], ...]:
        with self._lock:
            return tuple(self._drafts.items())

    async def token(self, account: Account, context: ExecutionContext) -> str:
        with self._lock:
            if not self.matches(account):
                raise MailFailure("mail_account_changed")
            client, home = self._client, self._home
        credential = await self.credentials.call("silent", client, home)
        await context.checkpoint()
        if credential.home_id != home or not self.matches(account):
            raise MailFailure("mail_account_changed")
        _, profile = await self.graph.request(
            credential.token, "GET", "/me", params={"$select": "id,mail,userPrincipalName"}
        )
        if (
            profile.get("id") != account.user_id
            or (profile.get("mail") or profile.get("userPrincipalName")) != account.address
        ):
            raise MailFailure("mail_account_changed")
        await context.checkpoint()
        if not self.matches(account):
            raise MailFailure("mail_account_changed")
        return credential.token

    def payload(self, account: Account, message: Message) -> dict[str, object]:
        with self._lock:
            if not self.matches(account, message):
                raise MailFailure("mail_account_changed")
            return {
                "subject": message.subject,
                "body": {"contentType": "Text", "content": message.body},
                **{
                    field + "Recipients": [
                        {"emailAddress": {"address": value}} for value in getattr(message, field)
                    ]
                    for field in ("to", "cc", "bcc")
                },
                "attachments": [
                    {
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": item.name,
                        "contentType": "application/octet-stream",
                        "contentBytes": base64.b64encode(self._attachments[item.id][1]).decode(),
                    }
                    for item in message.attachments
                ],
            }

    async def read(
        self, account: Account, message_id: str, context: ExecutionContext
    ) -> MailResult:
        token = await self.token(account, context)
        _, data = await self.graph.request(
            token,
            "GET",
            "/me/messages/" + quote(message_id, safe=""),
            params={
                "$select": "id,subject,from,toRecipients,ccRecipients,body,isDraft,hasAttachments"
            },
        )
        # Only explicitly selected fields, inert plain text. Never render HTML or follow links.
        safe = {
            key: data.get(key)
            for key in (
                "id",
                "subject",
                "from",
                "toRecipients",
                "ccRecipients",
                "body",
                "isDraft",
                "hasAttachments",
            )
        }
        return MailResult(
            state="read",
            account=account,
            message_id=message_id,
            data=json.dumps(safe, ensure_ascii=False),
        )

    async def write(
        self, account: Account, message: Message, send: bool, context: ExecutionContext
    ) -> MailResult:
        token = await self.token(account, context)
        payload = self.payload(account, message)
        await context.checkpoint()
        if not self.matches(account, message):
            raise MailFailure("mail_account_changed")
        # Exactly one POST. Never send a mutable remote draft by ID.
        status, data = await self.graph.request(
            token,
            "POST",
            "/me/sendMail" if send else "/me/messages",
            {"message": payload, "saveToSentItems": True} if send else payload,
        )
        if send:
            if status != 202:
                raise MailFailure("mail_response")
            return MailResult(
                state="accepted",
                account=account,
                data="Microsoft принял запрос. Доставка не подтверждена. "
                "Не повторяйте отправку автоматически.",
            )
        key = data.get("id")
        if status != 201 or not isinstance(key, str) or not 1 <= len(key) <= 512:
            raise MailFailure("mail_response")
        # Verify the newly created remote draft by read-back, including attachment bytes.
        await context.checkpoint()
        _, observed = await self.graph.request(
            token,
            "GET",
            "/me/messages/" + quote(key, safe=""),
            params={"$select": "id,subject,body,toRecipients,ccRecipients,bccRecipients,isDraft"},
        )
        body = observed.get("body")
        if (
            observed.get("id") != key
            or observed.get("isDraft") is not True
            or observed.get("subject") != message.subject
            or not isinstance(body, dict)
            or str(body.get("contentType", "")).lower() != "text"
            or body.get("content") != message.body
        ):
            raise MailFailure("mail_verification")
        for field in ("to", "cc", "bcc"):
            recipients = observed.get(field + "Recipients")
            if not isinstance(recipients, list) or [
                value.get("emailAddress", {}).get("address")
                for value in recipients
                if isinstance(value, dict)
            ] != list(getattr(message, field)):
                raise MailFailure("mail_verification")
        _, observed_files = await self.graph.request(
            token, "GET", "/me/messages/" + quote(key, safe="") + "/attachments"
        )
        files = observed_files.get("value")
        if not isinstance(files, list) or len(files) != len(message.attachments):
            raise MailFailure("mail_verification")
        expected = sorted((item.name, item.size, item.sha256) for item in message.attachments)
        actual = []
        for item in files:
            if (
                not isinstance(item, dict)
                or item.get("@odata.type") != "#microsoft.graph.fileAttachment"
            ):
                raise MailFailure("mail_verification")
            raw = base64.b64decode(item.get("contentBytes", ""), validate=True)
            actual.append((item.get("name"), len(raw), hashlib.sha256(raw).hexdigest()))
        if sorted(actual) != expected:
            raise MailFailure("mail_verification")
        return MailResult(
            state="remote_draft",
            account=account,
            message_id=key,
            data="Черновик сохранён в Outlook и проверен чтением. Не отправлен.",
        )
