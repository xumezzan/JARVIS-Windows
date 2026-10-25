"""Teams as a connector: two reads, a draft that touches nothing, and one guarded send.

The reads answer the questions the owner's own scenarios ask - which conversations are
live, and what was actually said in one. The draft assembles the message and stops there,
so the exact words can be shown, spoken and corrected before anything leaves the machine.
The send is the only thing here that reaches other people, and it asks first.

Sending is CONFIRM, and deliberately. A chat message is not reversible work on the owner's
own computer: it arrives on somebody's phone, it is read within seconds, and this connector
has no way to delete it afterwards - that route does not exist here. The owner may lower it
in their matrix; the default is the one that asks.

Auth rides on the Microsoft account the Outlook tab signed in, but on its own consent: the
`teams` surface asks for `Chat.Read` and `ChatMessage.Send` separately, so a tenant that
refuses them costs Jarvis the chats and leaves the mailbox working.
"""

import json
import re
from html import unescape
from threading import Event
from typing import Any

from jarvis.connectors.base import AuthState, Capability, Health
from jarvis.connectors.http import ServiceTransport, TransportError
from jarvis.connectors.teams.api import TeamsFailure, translate, transport
from jarvis.connectors.teams.models import (
    MAX_DATA,
    MAX_TEXT,
    Chat,
    ChatsInput,
    DraftInput,
    Kind,
    Message,
    MessagesInput,
    SendInput,
    TeamsResult,
    plain,
)
from jarvis.mail.credentials import MailFailure
from jarvis.mail.models import Account
from jarvis.mail.session import MailSession
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext

SURFACE = "teams"
TAG = re.compile(r"<[^>]*>")

CAPABILITIES = (
    Capability("chats", "Показать недавние чаты Teams.", Risk.SAFE, True, reads=("conversation",)),
    Capability("messages", "Прочитать сообщения чата.", Risk.SAFE, True, reads=("conversation",)),
    Capability("draft", "Собрать сообщение, не отправляя его.", Risk.SAFE, True),
    Capability(
        "send", "Отправить сообщение в чат Teams.", Risk.CONFIRM, False, writes=("conversation",)
    ),
)


def text(value: object, limit: int = 200) -> str:
    return " ".join(plain(str(value)).split())[:limit] if isinstance(value, str) else ""


def words(body: object) -> str:
    """What a message says, as text. Teams writes most of them as HTML.

    Tags are dropped rather than rendered, and nothing referenced inside them is fetched:
    a hosted image in a message is somebody else's URL, and following it would turn reading
    a chat into making a request on their behalf.
    """
    if not isinstance(body, dict) or not isinstance(body.get("content"), str):
        return ""
    content = str(body["content"])
    if body.get("contentType") == "html":
        content = unescape(TAG.sub(" ", content))
    return " ".join(plain(content).split())[:MAX_TEXT]


def kind_of(value: object) -> Kind:
    """An unrecognised chat type is unknown, never guessed into one of the known ones."""
    return value if value in ("oneOnOne", "group", "meeting") else "unknown"


def chat_of(item: dict[str, Any]) -> Chat:
    return Chat(
        id=text(item.get("id"), 200),
        topic=text(item.get("topic"), 200),
        kind=kind_of(item.get("chatType")),
        updated=text(item.get("lastUpdatedDateTime"), 32),
    )


def author_of(item: dict[str, Any]) -> str:
    sender = item.get("from")
    user = sender.get("user") if isinstance(sender, dict) else None
    return text(user.get("displayName"), 200) if isinstance(user, dict) else ""


def message_of(item: dict[str, Any]) -> Message:
    return Message(
        id=text(item.get("id"), 32) or "0",
        author=author_of(item),
        created=text(item.get("createdDateTime"), 32),
        text=words(item.get("body")),
    )


def conversation(item: dict[str, Any]) -> bool:
    """A message somebody wrote. Joins, renames and deletions are events, not words."""
    return item.get("messageType") == "message" and not item.get("deletedDateTime")


def packed(payload: object) -> tuple[str, bool]:
    """One bounded JSON string. A chat is people's writing, so it is cut, not dropped."""
    encoded = json.dumps(payload, ensure_ascii=False)
    return (encoded, False) if len(encoded) <= MAX_DATA else (encoded[:MAX_DATA], True)


class TeamsConnector:
    service = "teams"

    def __init__(self, session: MailSession, graph: ServiceTransport | None = None) -> None:
        self.session = session
        self.graph = graph or transport()

    def capabilities(self) -> tuple[Capability, ...]:
        return CAPABILITIES

    async def authenticate(self) -> AuthState:
        """Who is signed in. The Outlook tab owns that; Teams reports what it inherited."""
        account = self.session.account
        return (
            AuthState("connected", account.address)
            if account is not None
            else AuthState("disconnected")
        )

    async def health_check(self) -> Health:
        """Whether Teams itself can be reached, which is a different question.

        The account can be signed in while the Teams consent was never given, so this asks
        for a token on that surface rather than assuming one exists.
        """
        account = self.session.account
        if account is None:
            return Health("unauthenticated", "credentials")
        try:
            await self.session.surface_token(account, SURFACE, ExecutionContext(Event()))
        except MailFailure:
            return Health("unauthenticated", "credentials")
        except Exception:
            return Health("unavailable", "network")
        return Health("ready")

    async def _call(
        self,
        account: Account,
        context: ExecutionContext,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, object]:
        try:
            token = await self.session.surface_token(account, SURFACE, context)
        except MailFailure as error:
            # A missing Teams consent and a changed account are different problems, and the
            # owner can only act on one of them.
            raise TeamsFailure(
                "teams_account_changed"
                if error.code == "mail_account_changed"
                else "teams_credentials"
            ) from None
        await context.checkpoint()
        try:
            _, data = await self.graph.request(token, method, path, payload, params)
        except TransportError as error:
            raise translate(error) from None
        await context.checkpoint()
        return data

    def _values(self, data: dict[str, object], limit: int) -> list[dict[str, Any]]:
        values = data.get("value")
        if not isinstance(values, list) or len(values) > limit:
            raise TeamsFailure("teams_response")
        return [item for item in values if isinstance(item, dict)]

    async def chats(self, args: ChatsInput, context: ExecutionContext) -> TeamsResult:
        data = await self._call(
            args.account,
            context,
            "GET",
            "/me/chats",
            params={
                "$top": str(args.limit),
                # The most recent conversations, which is what "recent chats" means here.
                "$orderby": "lastMessagePreview/createdDateTime desc",
            },
        )
        found = [chat_of(item) for item in self._values(data, args.limit)]
        body, cut = packed([chat.model_dump(mode="json") for chat in found])
        return TeamsResult(state="chats", account=args.account, data=body, truncated=cut)

    async def messages(self, args: MessagesInput, context: ExecutionContext) -> TeamsResult:
        data = await self._call(
            args.account,
            context,
            "GET",
            f"/chats/{args.chat}/messages",
            params={"$top": str(args.limit), "$orderby": "createdDateTime desc"},
        )
        said = [message_of(item) for item in self._values(data, args.limit) if conversation(item)]
        body, cut = packed([message.model_dump(mode="json") for message in said])
        return TeamsResult(
            state="messages", account=args.account, chat=args.chat, data=body, truncated=cut
        )

    async def stored(
        self, account: Account, chat: str, message_id: str, context: ExecutionContext
    ) -> Message:
        """One message as Teams holds it now. Used to check that a send really landed."""
        data = await self._call(account, context, "GET", f"/chats/{chat}/messages/{message_id}")
        return message_of(data)

    async def draft(self, args: DraftInput, context: ExecutionContext) -> TeamsResult:
        """Exactly what would be sent, and nothing sent. No request leaves the machine."""
        await context.checkpoint()
        body, cut = packed(args.message.model_dump(mode="json"))
        return TeamsResult(
            state="draft",
            account=args.account,
            chat=args.message.chat,
            data=body,
            truncated=cut,
        )

    async def send(self, args: SendInput, context: ExecutionContext) -> TeamsResult:
        note = args.message
        data = await self._call(
            args.account,
            context,
            "POST",
            f"/chats/{note.chat}/messages",
            # Plain text on purpose: HTML would let a planned message carry markup, links
            # and mentions that nobody approved.
            payload={"body": {"contentType": "text", "content": note.text}},
        )
        written = message_of(data)
        body, cut = packed(written.model_dump(mode="json"))
        return TeamsResult(
            state="sent",
            account=args.account,
            chat=note.chat,
            message_id=written.id,
            data=body,
            truncated=cut,
        )
