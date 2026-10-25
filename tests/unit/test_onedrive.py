"""OneDrive and Excel connector. No account, token, network or real workbook is used."""

import json
from collections.abc import Iterator
from threading import Event as Flag
from typing import Any

import pytest
from pydantic import ValidationError

from jarvis.connectors.http import TransportError
from jarvis.connectors.microsoft.drive.api import ROUTES, DriveFailure, transport
from jarvis.connectors.microsoft.drive.connector import CAPABILITIES, DriveConnector
from jarvis.connectors.microsoft.drive.mapping import MAPPERS
from jarvis.connectors.microsoft.drive.models import (
    DriveResult,
    Grid,
    ReadInput,
    SearchInput,
    SheetsInput,
    corners,
)
from jarvis.connectors.microsoft.drive.tools import register_drive
from jarvis.mail.credentials import MailFailure
from jarvis.mail.models import Account
from jarvis.permissions.matrix import PermissionMatrix, Rule
from jarvis.permissions.policies import Risk
from jarvis.tools.base import ExecutionContext
from jarvis.tools.registry import ToolRegistry

ACCOUNT = Account(user_id="fixture-user", address="owner@example.test", session="a" * 32)
OTHER = Account(user_id="other-user", address="someone@example.test", session="b" * 32)
ITEM = "01CYZLFJGUJ7JHBSZDFZFL25KSZGQTVAUN"
SHEET = "Отчёт"
RUN = "c" * 32


def raw_file(name: str = "Отчёт за неделю.xlsx", identifier: str = ITEM) -> dict[str, Any]:
    return {
        "id": identifier,
        "name": name,
        "size": 20480,
        "lastModifiedDateTime": "2026-09-18T07:30:00Z",
    }


def raw_range(values: list[list[Any]], address: str = "Отчёт!A1:B2") -> dict[str, Any]:
    return {
        "address": address,
        "addressLocal": address,
        "cellCount": sum(len(row) for row in values),
        "rowCount": len(values),
        "columnCount": len(values[0]) if values else 0,
        "text": [[str(cell) for cell in row] for row in values],
        "values": values,
        "valueTypes": [["String" for _ in row] for row in values],
    }


class FakeGraph:
    """Records what the connector asked for and answers with fixture shapes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.params: list[dict[str, str]] = []
        self.payloads: list[dict[str, Any]] = []
        self.files: list[dict[str, Any]] = [raw_file()]
        self.sheets: list[dict[str, Any]] = [
            {"id": "{0000}", "name": SHEET, "position": 0, "visibility": "Visible"},
            {"id": "{0001}", "name": "Черновик", "position": 1, "visibility": "Hidden"},
        ]
        self.cells: list[list[Any]] = [["Неделя", "Часы"], ["38-я", 41]]
        self.stored: list[list[Any]] | None = None
        self.deny: str = ""

    async def request(
        self,
        token: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        assert transport().allows(method, path), f"{method} {path} is not an allowed route"
        self.calls.append((method, path))
        if params is not None:
            self.params.append(dict(params))
        if payload is not None:
            self.payloads.append(dict(payload))
        if self.deny:
            raise TransportError(self.deny)  # type: ignore[arg-type]
        if "search" in path:
            return 200, {"value": list(self.files)}
        if path.endswith("/worksheets"):
            return 200, {"value": list(self.sheets)}
        if method == "PATCH":
            written = (payload or {}).get("values")
            assert isinstance(written, list)
            self.cells = [list(row) for row in written]
            return 200, raw_range(self.cells)
        return 200, raw_range(self.stored if self.stored is not None else self.cells)


class FakeSession:
    """The Microsoft session, reduced to what a connector is allowed to ask of it."""

    def __init__(self) -> None:
        self.account: Account | None = ACCOUNT
        self.surfaces: list[str] = []
        self.refused: set[str] = set()

    def matches(self, account: Account, message: object = None) -> bool:
        return self.account == account

    async def surface_token(self, account: Account, surface: str, context: ExecutionContext) -> str:
        await context.checkpoint()
        self.surfaces.append(surface)
        if self.account != account:
            raise MailFailure("mail_account_changed")
        if surface in self.refused:
            raise MailFailure("mail_credentials")
        return "synthetic-noncredential"


@pytest.fixture
def bench() -> Iterator[tuple[DriveConnector, FakeGraph, FakeSession, ExecutionContext]]:
    graph, session = FakeGraph(), FakeSession()
    connector = DriveConnector(session, graph)  # type: ignore[arg-type]
    yield connector, graph, session, ExecutionContext(Flag())


def test_the_transport_reaches_one_drive_and_one_workbook_at_a_time() -> None:
    allowed = {(route.method, route.path) for route in ROUTES}
    assert len(allowed) == 5
    # Nothing uploads, deletes, shares or moves a file, and nobody else's drive is reachable.
    assert not any(method in ("POST", "PUT", "DELETE") for method, _ in allowed)
    assert not any("/users/" in path or "/drives/" in path for _, path in allowed)
    assert not any("createSession" in path or "charts" in path for _, path in allowed)
    guard = transport()
    sheet = f"/me/drive/items/{ITEM}/workbook/worksheets('%D0%9E%D1%82%D1%87%D1%91%D1%82')"
    assert guard.allows("GET", sheet + "/usedRange")
    assert guard.allows("PATCH", sheet + "/range(address='A1%3AB2')")
    # A name that was not encoded would carry its own quote into the segment.
    assert not guard.allows("GET", f"/me/drive/items/{ITEM}/workbook/worksheets('it's')/usedRange")
    assert not guard.allows("DELETE", sheet)
    assert not guard.allows("GET", f"/me/drive/items/{ITEM}/workbook/worksheets/add")


def test_writing_asks_before_it_happens_and_reading_does_not() -> None:
    registry = ToolRegistry()
    register_drive(registry, DriveConnector(FakeSession()))  # type: ignore[arg-type]
    assert registry.get("onedrive.write").risk is Risk.CONFIRM  # type: ignore[union-attr]
    for name in ("onedrive.files", "onedrive.sheets", "onedrive.read"):
        assert registry.get(name).risk is Risk.SAFE  # type: ignore[union-attr]
    writes = {capability.name for capability in CAPABILITIES if capability.writes}
    assert writes == {"write"}


def test_the_owner_may_tighten_a_capability_but_never_loosen_it() -> None:
    registry = ToolRegistry()
    matrix = PermissionMatrix({"onedrive": {"read": Rule(risk=Risk.CONFIRM)}})
    register_drive(registry, DriveConnector(FakeSession()), matrix)  # type: ignore[arg-type]
    assert registry.get("onedrive.read").risk is Risk.CONFIRM  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_search_names_the_workbooks_it_found(
    bench: tuple[DriveConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, graph, session, context = bench
    graph.files = [raw_file(), raw_file("Договор.docx", "01OTHER")]
    result = await connector.files(SearchInput(account=ACCOUNT, query="отчёт"), context)
    method, path = graph.calls[0]
    assert method == "GET"
    # The words the owner said are encoded before they reach the URL, never quoted into it.
    assert path == "/me/drive/root/search(q='%D0%BE%D1%82%D1%87%D1%91%D1%82')"
    rows = json.loads(result.data)
    assert [row["workbook"] for row in rows] == [True, False]
    assert rows[0]["name"] == "Отчёт за неделю.xlsx"
    assert session.surfaces == ["files"]


@pytest.mark.asyncio
async def test_a_hidden_sheet_is_reported_as_hidden(
    bench: tuple[DriveConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, graph, _, context = bench
    result = await connector.sheets(SheetsInput(account=ACCOUNT, item=ITEM), context)
    assert graph.calls[0] == ("GET", f"/me/drive/items/{ITEM}/workbook/worksheets")
    assert [row["visible"] for row in json.loads(result.data)] == [True, False]


@pytest.mark.asyncio
async def test_reading_without_an_address_measures_the_sheet(
    bench: tuple[DriveConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, graph, _, context = bench
    used = await connector.read(ReadInput(account=ACCOUNT, item=ITEM, sheet=SHEET), context)
    assert graph.calls[0][1].endswith("/usedRange")
    assert json.loads(used.data) == [["Неделя", "Часы"], ["38-я", 41]]
    # The address Excel measured is reported back, not the one nobody gave.
    assert used.address == "Отчёт!A1:B2"
    exact = await connector.read(
        ReadInput(account=ACCOUNT, item=ITEM, sheet=SHEET, address="A1:B2"), context
    )
    assert graph.calls[1][1].endswith("/range(address='A1%3AB2')")
    assert exact.state == "read"


@pytest.mark.asyncio
async def test_a_written_range_is_read_back_before_it_is_called_done(
    bench: tuple[DriveConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, graph, _, context = bench
    registry = ToolRegistry()
    register_drive(registry, connector)
    spec = registry.get("onedrive.write")
    assert spec is not None
    args = spec.normalize(
        {
            "account": ACCOUNT.model_dump(),
            "range": {
                "item": ITEM,
                "sheet": SHEET,
                "address": "A1:B2",
                "values": [["Неделя", "Часы"], ["38-я", 41]],
            },
        }
    )
    result = await spec.run(args, context)
    assert isinstance(result, DriveResult) and result.state == "written"
    assert graph.calls[0][0] == "PATCH"
    # Values only: number formats and formulas are the owner's own decisions.
    assert graph.payloads[0] == {"values": [["Неделя", "Часы"], ["38-я", 41]]}
    assert await spec.verify(args, result, context) is True
    # The read-back is a real second look at the same address, not the service's own report.
    assert graph.calls[1] == ("GET", graph.calls[0][1])


@pytest.mark.asyncio
async def test_cells_that_came_back_different_are_not_reported_as_written(
    bench: tuple[DriveConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, graph, _, context = bench
    registry = ToolRegistry()
    register_drive(registry, connector)
    spec = registry.get("onedrive.write")
    assert spec is not None
    args = spec.normalize(
        {
            "account": ACCOUNT.model_dump(),
            "range": {
                "item": ITEM,
                "sheet": SHEET,
                "address": "A1:B1",
                "values": [["Неделя", "01.09.2026"]],
            },
        }
    )
    result = await spec.run(args, context)
    # Excel keeps what it decided the cell meant: a date becomes a serial number, and the
    # check says so instead of passing quietly.
    graph.stored = [["Неделя", 46266]]
    assert await spec.verify(args, result, context) is False
    # A number the owner wrote as text still counts as written: the cell holds the number.
    graph.stored = [["Неделя", "01.09.2026"]]
    assert await spec.verify(args, result, context) is True


@pytest.mark.asyncio
async def test_a_number_stored_as_a_number_still_matches_what_was_asked(
    bench: tuple[DriveConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, graph, _, context = bench
    registry = ToolRegistry()
    register_drive(registry, connector)
    spec = registry.get("onedrive.write")
    assert spec is not None
    args = spec.normalize(
        {
            "account": ACCOUNT.model_dump(),
            "range": {"item": ITEM, "sheet": SHEET, "address": "A1", "values": [["41"]]},
        }
    )
    result = await spec.run(args, context)
    graph.stored = [[41]]
    assert await spec.verify(args, result, context) is True
    graph.stored = [[42]]
    assert await spec.verify(args, result, context) is False


def test_the_values_have_to_fill_the_address_exactly() -> None:
    # Excel copies a single value across a whole range when the shapes disagree; a grid
    # that does not fit its address would quietly fill cells nobody meant to touch.
    with pytest.raises(ValidationError):
        Grid(item=ITEM, sheet=SHEET, address="A1:C3", values=[["один"]])
    with pytest.raises(ValidationError):
        Grid(item=ITEM, sheet=SHEET, address="A1:B2", values=[["a", "b"], ["c"]])
    assert Grid(item=ITEM, sheet=SHEET, address="A1:B2", values=[["a", "b"], ["c", "d"]])
    assert corners("A1") == (1, 1)
    assert corners("A1:B3") == (3, 2)
    assert corners("A1:AA2") == (2, 27)


def test_a_cell_holds_text_or_a_number_and_never_a_formula() -> None:
    with pytest.raises(ValidationError):
        Grid(item=ITEM, sheet=SHEET, address="A1", values=[["=SUM(B1:B9)"]])
    with pytest.raises(ValidationError):
        Grid(item=ITEM, sheet=SHEET, address="A1", values=[["x" * 300]])
    with pytest.raises(ValidationError):
        # A sheet name Excel itself refuses never reaches a URL.
        Grid(item=ITEM, sheet="Отчёт/2026", address="A1", values=[["x"]])
    with pytest.raises(ValidationError):
        Grid(item=ITEM, sheet=SHEET, address="A1:B2", values=[])


def test_a_search_cannot_close_the_quoted_segment_it_lives_in() -> None:
    with pytest.raises(ValidationError):
        SearchInput(account=ACCOUNT, query="отчёт' and 1 eq 1")
    with pytest.raises(ValidationError):
        ReadInput(account=ACCOUNT, item=ITEM, sheet=SHEET, address="Отчёт!A1")


@pytest.mark.asyncio
async def test_a_files_consent_that_was_never_given_is_not_a_changed_account(
    bench: tuple[DriveConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, _, session, context = bench
    session.refused.add("files")
    with pytest.raises(DriveFailure) as failure:
        await connector.files(SearchInput(account=ACCOUNT, query="отчёт"), context)
    assert failure.value.code == "drive_credentials"
    session.refused.clear()
    session.account = OTHER
    with pytest.raises(DriveFailure) as changed:
        await connector.files(SearchInput(account=ACCOUNT, query="отчёт"), context)
    assert changed.value.code == "drive_account_changed"


@pytest.mark.asyncio
async def test_microsoft_wording_never_crosses_the_boundary(
    bench: tuple[DriveConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, graph, _, context = bench
    for code, expected in (
        ("credentials", "drive_credentials"),
        ("forbidden", "drive_forbidden"),
        # A personal OneDrive answers this for a workbook: Excel serves business drives only.
        ("not_found", "drive_missing"),
        ("rate_limited", "drive_rate_limited"),
        ("server", "drive_network"),
        ("response_limit", "drive_response"),
    ):
        graph.deny = code
        with pytest.raises(DriveFailure) as failure:
            await connector.sheets(SheetsInput(account=ACCOUNT, item=ITEM), context)
        assert failure.value.code == expected


@pytest.mark.asyncio
async def test_a_cancelled_task_stops_before_the_request_is_issued(
    bench: tuple[DriveConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    import asyncio

    connector, graph, _, _ = bench
    flag = Flag()
    flag.set()
    with pytest.raises(asyncio.CancelledError):
        await connector.sheets(SheetsInput(account=ACCOUNT, item=ITEM), ExecutionContext(flag))
    assert graph.calls == []


@pytest.mark.asyncio
async def test_a_tool_refuses_an_account_this_session_is_not_signed_in_as(
    bench: tuple[DriveConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, _, _, context = bench
    registry = ToolRegistry()
    register_drive(registry, connector)
    spec = registry.get("onedrive.files")
    assert spec is not None
    args = spec.normalize({"account": OTHER.model_dump(), "query": "x"})
    assert await spec.check(args, context) is False


@pytest.mark.asyncio
async def test_health_answers_for_the_files_consent_and_not_only_for_the_sign_in(
    bench: tuple[DriveConnector, FakeGraph, FakeSession, ExecutionContext],
) -> None:
    connector, _, session, _ = bench
    assert (await connector.health_check()).state == "ready"
    session.refused.add("files")
    health = await connector.health_check()
    assert (health.state, health.reason) == ("unauthenticated", "credentials")
    # The mailbox is still signed in, which is exactly the point of a separate consent.
    assert (await connector.authenticate()).state == "connected"
    session.account = None
    assert (await connector.health_check()).state == "unauthenticated"


def test_a_file_reads_as_a_document_and_its_cells_stay_out_of_the_graph() -> None:
    observation = MAPPERS["onedrive.files"](
        "onedrive.files",
        RUN,
        {"data": json.dumps([raw_file(), {"id": "01OTHER", "name": "Договор.docx"}])},
    )
    names = [entity.name for entity in observation.entities if entity is not None]
    assert names == ["Отчёт за неделю.xlsx", "Договор.docx"]
    assert "onedrive.read" not in MAPPERS and "onedrive.write" not in MAPPERS
