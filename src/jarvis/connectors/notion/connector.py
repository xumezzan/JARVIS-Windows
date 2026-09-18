"""Notion as a connector: three reads and two guarded writes.

The reads answer "where is the page about this client" and "what does it say". The writes
create a page under a page, and add paragraphs to one. Both ask first: a Notion page is
shared with people, and a paragraph appended to the wrong page is read by them.

Nothing here archives, deletes, rewrites properties or queries a data source. A connector
cannot misuse what it has no way to express.
"""

import json
from typing import Any

from jarvis.connectors.base import AuthState, Capability, Health
from jarvis.connectors.http import TransportError
from jarvis.connectors.notion.api import NotionFailure, translate, transport
from jarvis.connectors.notion.models import (
    MAX_DATA,
    AppendInput,
    CreatePageInput,
    NotionResult,
    Page,
    PageInput,
    ReadInput,
    SearchInput,
)
from jarvis.core.planner.contracts import ProviderError
from jarvis.permissions.policies import Risk
from jarvis.security.credentials import load_api_key
from jarvis.tools.base import ExecutionContext

# The block types whose words are worth reading back. Anything else is named, not quoted:
# an image or an embed has no text, and pretending otherwise would invent content.
TEXTUAL = (
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "quote",
    "callout",
    "toggle",
    "code",
)

CAPABILITIES = (
    Capability("search", "Найти страницы по названию.", Risk.SAFE, True, reads=("document",)),
    Capability(
        "page", "Прочитать заголовок и ссылку страницы.", Risk.SAFE, True, reads=("document",)
    ),
    Capability("read", "Прочитать текст страницы.", Risk.SAFE, True, reads=("document",)),
    Capability(
        "create_page", "Создать страницу внутри другой.", Risk.CONFIRM, False, writes=("document",)
    ),
    Capability("append", "Дописать абзацы в страницу.", Risk.CONFIRM, False, writes=("document",)),
)


def text(value: object, limit: int = 300) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, str) else ""


def rich(value: object, limit: int = 1800) -> str:
    """Notion writes text as a list of runs; the words are the plain_text of each."""
    if not isinstance(value, list):
        return ""
    parts = [
        run.get("plain_text")
        for run in value
        if isinstance(run, dict) and isinstance(run.get("plain_text"), str)
    ]
    return " ".join("".join(str(part) for part in parts).split())[:limit]


def title_of(item: dict[str, Any]) -> str:
    """A page's title lives in whichever property declares itself the title."""
    properties = item.get("properties")
    if not isinstance(properties, dict):
        return ""
    for value in properties.values():
        if isinstance(value, dict) and value.get("type") == "title":
            return rich(value.get("title"), 300)
    return ""


def page_of(item: dict[str, Any]) -> Page:
    return Page(
        id=text(item.get("id"), 36) or "0" * 32,
        title=title_of(item),
        url=text(item.get("url"), 400),
        archived=bool(item.get("in_trash") or item.get("is_archived") or item.get("archived")),
    )


def line_of(block: dict[str, Any]) -> str:
    kind = block.get("type")
    if not isinstance(kind, str) or kind not in TEXTUAL:
        return ""
    body = block.get(kind)
    return rich(body.get("rich_text")) if isinstance(body, dict) else ""


def packed(payload: object) -> tuple[str, bool]:
    encoded = json.dumps(payload, ensure_ascii=False)
    return (encoded, False) if len(encoded) <= MAX_DATA else (encoded[:MAX_DATA], True)


def paragraph(content: str) -> dict[str, object]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]},
    }


class NotionConnector:
    service = "notion"

    def __init__(self) -> None:
        self.transport = transport()

    def capabilities(self) -> tuple[Capability, ...]:
        return CAPABILITIES

    async def authenticate(self) -> AuthState:
        try:
            await load_api_key(self.service)
        except ProviderError:
            return AuthState("disconnected")
        return AuthState("connected")

    async def health_check(self) -> Health:
        state = await self.authenticate()
        return (
            Health("ready")
            if state.state == "connected"
            else Health("unauthenticated", "credentials")
        )

    async def _call(
        self,
        method: str,
        path: str,
        context: ExecutionContext,
        *,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, object]:
        await context.checkpoint()
        try:
            key = await load_api_key(self.service)
        except ProviderError:
            raise NotionFailure("notion_credentials") from None
        try:
            _, body = await self.transport.request(key, method, path, payload, params)
        except TransportError as error:
            raise translate(error) from None
        finally:
            key = ""
        return body

    def _results(self, body: dict[str, object]) -> list[dict[str, Any]]:
        found = body.get("results")
        return [item for item in found if isinstance(item, dict)] if isinstance(found, list) else []

    async def search(self, args: SearchInput, context: ExecutionContext) -> NotionResult:
        body = await self._call(
            "POST",
            "/v1/search",
            context,
            payload={
                "query": args.query,
                # Pages only: a data source is a schema, not something anyone asked to read.
                "filter": {"property": "object", "value": "page"},
                "page_size": args.limit,
            },
        )
        pages = [page_of(item) for item in self._results(body) if item.get("object") == "page"]
        alive = [page for page in pages if not page.archived][: args.limit]
        data, cut = packed([page.model_dump(mode="json") for page in alive])
        return NotionResult(state="found", data=data, truncated=cut)

    async def page(self, args: PageInput, context: ExecutionContext) -> NotionResult:
        body = await self._call("GET", f"/v1/pages/{args.page}", context)
        found = page_of(body)
        data, cut = packed(found.model_dump(mode="json"))
        return NotionResult(state="page", page_id=found.id, url=found.url, data=data, truncated=cut)

    async def read(self, args: ReadInput, context: ExecutionContext) -> NotionResult:
        body = await self._call(
            "GET",
            f"/v1/blocks/{args.page}/children",
            context,
            params={"page_size": str(min(args.limit, 100))},
        )
        lines = [line for block in self._results(body) if (line := line_of(block))]
        data, cut = packed(lines[: args.limit])
        return NotionResult(state="read", page_id=args.page, data=data, truncated=cut)

    async def create_page(self, args: CreatePageInput, context: ExecutionContext) -> NotionResult:
        wanted = args.page
        payload: dict[str, object] = {
            # Under a page, not a data source: the title property of somebody's database is
            # theirs to name, and guessing it writes into the wrong column.
            "parent": {"page_id": wanted.parent},
            "properties": {
                "title": {"title": [{"type": "text", "text": {"content": wanted.title}}]}
            },
        }
        if wanted.paragraphs:
            payload["children"] = [paragraph(line) for line in wanted.paragraphs]
        body = await self._call("POST", "/v1/pages", context, payload=payload)
        created = page_of(body)
        data, cut = packed(created.model_dump(mode="json"))
        return NotionResult(
            state="created", page_id=created.id, url=created.url, data=data, truncated=cut
        )

    async def append(self, args: AppendInput, context: ExecutionContext) -> NotionResult:
        body = await self._call(
            "PATCH",
            f"/v1/blocks/{args.page}/children",
            context,
            payload={"children": [paragraph(line) for line in args.paragraphs]},
        )
        # What Notion says it created, in its own words, so the caller can compare it with
        # what was asked. This is the service's own report rather than an independent read:
        # a freshly created block cannot be addressed again without widening the route
        # table, and reading the page back finds only its first hundred blocks, so a long
        # page would fail verification for no reason.
        written = [line_of(block) for block in self._results(body)]
        data, cut = packed(written)
        return NotionResult(state="appended", page_id=args.page, data=data, truncated=cut)
