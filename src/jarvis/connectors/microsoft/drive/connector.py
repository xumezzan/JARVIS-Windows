"""OneDrive as a connector: find the file, read the sheet, and write one range that asks.

Three reads and one guarded write, aimed at the one thing the owner asked for by name -
a report. Finding the workbook, seeing which sheets it has and reading what is already in
one are all reversible work on data that is already theirs. Writing cells is not: a
workbook in OneDrive is usually shared, the previous contents of a cell are gone once it
is overwritten, and this connector has no undo - that route does not exist here.

Auth rides on the Microsoft account the Outlook tab signed in, on its own consent: the
`files` surface asks for `Files.ReadWrite` separately, so the mailbox keeps working if
that consent is refused, and the mail token cannot write into a workbook.
"""

import json
from threading import Event
from typing import Any
from urllib.parse import quote

from jarvis.connectors.base import AuthState, Capability, Health
from jarvis.connectors.http import ServiceTransport, TransportError
from jarvis.connectors.microsoft.drive.api import DriveFailure, translate, transport
from jarvis.connectors.microsoft.drive.models import (
    MAX_DATA,
    WORKBOOK,
    DriveResult,
    File,
    Grid,
    ReadInput,
    SearchInput,
    SheetsInput,
    Worksheet,
    WriteInput,
)
from jarvis.mail.credentials import MailFailure
from jarvis.mail.models import Account
from jarvis.mail.session import MailSession
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext

SURFACE = "files"

CAPABILITIES = (
    Capability(
        "files", "Найти файлы в OneDrive по названию.", Risk.SAFE, True, reads=("document",)
    ),
    Capability("sheets", "Показать листы книги Excel.", Risk.SAFE, True, reads=("document",)),
    Capability("read", "Прочитать диапазон листа.", Risk.SAFE, True, reads=("document",)),
    Capability("write", "Записать значения в диапазон.", Risk.CONFIRM, False, writes=("document",)),
)


def text(value: object, limit: int = 200) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, str) else ""


def number(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def file_of(item: dict[str, Any]) -> File:
    name = text(item.get("name"), 200)
    return File(
        id=text(item.get("id"), 200) or "0",
        name=name,
        modified=text(item.get("lastModifiedDateTime"), 32),
        size=number(item.get("size")),
        # Only a workbook can be asked about its sheets, and saying so here saves the
        # planner a request that would come back as "not found".
        workbook=name.lower().endswith(WORKBOOK),
    )


def sheet_of(item: dict[str, Any]) -> Worksheet:
    return Worksheet(
        id=text(item.get("id"), 64),
        name=text(item.get("name"), 200),
        position=number(item.get("position")),
        visible=item.get("visibility") != "Hidden",
    )


def values_of(data: dict[str, object]) -> list[list[object]]:
    """The cells Excel reports, as rows. Anything else in the answer is not values."""
    rows = data.get("values")
    if not isinstance(rows, list):
        return []
    return [list(row) for row in rows if isinstance(row, list)]


def packed(payload: object) -> tuple[str, bool]:
    """One bounded JSON string. A sheet can be far larger than an answer should be."""
    encoded = json.dumps(payload, ensure_ascii=False)
    return (encoded, False) if len(encoded) <= MAX_DATA else (encoded[:MAX_DATA], True)


def segment(value: str) -> str:
    """One quoted OData segment. Everything outside the unreserved set is encoded."""
    return quote(value, safe="")


class DriveConnector:
    service = "onedrive"

    def __init__(self, session: MailSession, graph: ServiceTransport | None = None) -> None:
        self.session = session
        self.graph = graph or transport()

    def capabilities(self) -> tuple[Capability, ...]:
        return CAPABILITIES

    async def authenticate(self) -> AuthState:
        """Who is signed in. The Outlook tab owns that; the drive reports what it inherited."""
        account = self.session.account
        return (
            AuthState("connected", account.address)
            if account is not None
            else AuthState("disconnected")
        )

    async def health_check(self) -> Health:
        """Whether the drive can be reached, which is a different question from who signed in."""
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
            # A missing files consent and a changed account are different problems, and the
            # owner can only act on one of them.
            raise DriveFailure(
                "drive_account_changed"
                if error.code == "mail_account_changed"
                else "drive_credentials"
            ) from None
        await context.checkpoint()
        try:
            _, data = await self.graph.request(token, method, path, payload, params)
        except TransportError as error:
            raise translate(error) from None
        await context.checkpoint()
        return data

    def _sheet_path(self, item: str, sheet: str) -> str:
        return f"/me/drive/items/{item}/workbook/worksheets('{segment(sheet)}')"

    async def files(self, args: SearchInput, context: ExecutionContext) -> DriveResult:
        data = await self._call(
            args.account,
            context,
            "GET",
            f"/me/drive/root/search(q='{segment(args.query)}')",
            params={"$top": str(args.limit), "$select": "id,name,size,lastModifiedDateTime"},
        )
        values = data.get("value")
        rows = values if isinstance(values, list) else []
        found = [file_of(row) for row in rows if isinstance(row, dict) and row.get("id")]
        body, cut = packed([item.model_dump(mode="json") for item in found[: args.limit]])
        return DriveResult(state="found", account=args.account, data=body, truncated=cut)

    async def sheets(self, args: SheetsInput, context: ExecutionContext) -> DriveResult:
        data = await self._call(
            args.account, context, "GET", f"/me/drive/items/{args.item}/workbook/worksheets"
        )
        values = data.get("value")
        rows = values if isinstance(values, list) else []
        found = [sheet_of(row) for row in rows if isinstance(row, dict)]
        body, cut = packed([sheet.model_dump(mode="json") for sheet in found])
        return DriveResult(
            state="sheets", account=args.account, item=args.item, data=body, truncated=cut
        )

    async def read(self, args: ReadInput, context: ExecutionContext) -> DriveResult:
        base = self._sheet_path(args.item, args.sheet)
        path = (
            f"{base}/range(address='{segment(args.address)}')"
            if args.address
            else f"{base}/usedRange"
        )
        data = await self._call(args.account, context, "GET", path)
        body, cut = packed(values_of(data))
        return DriveResult(
            state="read",
            account=args.account,
            item=args.item,
            sheet=args.sheet,
            # The address Excel answers with, which for a used range is the one it measured.
            address=text(data.get("address"), 64) or args.address or "",
            data=body,
            truncated=cut,
        )

    async def write(self, args: WriteInput, context: ExecutionContext) -> DriveResult:
        wanted = args.range
        path = (
            self._sheet_path(wanted.item, wanted.sheet)
            + f"/range(address='{segment(wanted.address)}')"
        )
        data = await self._call(
            args.account,
            context,
            "PATCH",
            path,
            # Values only. Number formats and formulas are other people's decisions about
            # their own workbook, and this connector has no way to express them.
            payload={"values": [list(row) for row in wanted.values]},
        )
        body, cut = packed(values_of(data))
        return DriveResult(
            state="written",
            account=args.account,
            item=wanted.item,
            sheet=wanted.sheet,
            address=text(data.get("address"), 64) or wanted.address,
            data=body,
            truncated=cut,
        )

    async def stored(self, args: Grid, account: Account, context: ExecutionContext) -> DriveResult:
        """The same range, read again. Used to check that a write really landed."""
        return await self.read(
            ReadInput(account=account, item=args.item, sheet=args.sheet, address=args.address),
            context,
        )
