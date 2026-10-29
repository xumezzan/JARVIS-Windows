"""Opt-in real Chrome/UIA test on a local synthetic shop; no retailer or payments."""

from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from jarvis.core.planner.contracts import Limits, PlannerInput, Proposal
from jarvis.core.planner.offline import call
from jarvis.core.planner.openai_provider import chrome_handoff_observation
from jarvis.core.planner.runner import Runner
from jarvis.observability.audit import AuditLog
from jarvis.permissions.approvals import ApprovalStore
from jarvis.permissions.engine import PermissionEngine
from jarvis.permissions.policies import Mode, Status
from jarvis.platforms.windows.transport import ProcessBackend
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.windows import WindowsResult, register_windows

pytestmark = [pytest.mark.windows, pytest.mark.e2e]


@pytest.mark.asyncio
async def test_normal_chrome_selects_variant_and_prepares_only_fixture_cart(tmp_path: Path) -> None:
    token = "Jarvis Shop Fixture " + uuid4().hex[:8]
    cart: list[dict[str, list[str]]] = []
    page = (
        (
            """<!doctype html><html lang="en"><meta charset="utf-8">
<title>TOKEN</title><style>body{font:22px sans-serif;margin:24px}label{margin:15px}
button{font-size:24px;margin:15px}fieldset{margin:16px 0}</style>
<h1>Synthetic shop acceptance. No retailer. No payment.</h1>
<a href="#product" onclick="document.getElementById('product').hidden=false">iPhone 18 Pro Max</a>
<section id="product" hidden><h2>Test configuration</h2>
<fieldset><legend>Storage</legend>
<label><input type="radio" name="storage" value="256">256 GB</label>
<label><input type="radio" name="storage" value="512">512 GB</label></fieldset>
<fieldset><legend>Colour</legend>
<label><input type="radio" name="colour" value="burgundy">Burgundy</label>
<label><input type="radio" name="colour" value="black">Black</label></fieldset>
<button id="bag" disabled>Add to Bag</button><p id="cart" role="status"></p>
<button disabled>Place order (unavailable in this fixture)</button></section>
<script>const chosen=n=>document.querySelector('input[name='+n+']:checked');
document.onchange=()=>bag.disabled=!(chosen('storage')&&chosen('colour'));
bag.onclick=async()=>{const q=new URLSearchParams({storage:chosen('storage').value,
colour:chosen('colour').value});await fetch('/cart?'+q);document.getElementById('cart').textContent=
'Fixture cart: '+chosen('storage').value+' GB '+chosen('colour').value;
document.title='TOKEN CART VERIFIED';};</script></html>"""
        )
        .replace("TOKEN", token)
        .encode()
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            url = urlsplit(self.path)
            if url.path == "/cart":
                cart.append(parse_qs(url.query))
                body = b"fixture cart stored"
            else:
                body = page
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"

    class FixturePlanner:
        choices = ["iPhone 18 Pro Max", "256 GB", "Burgundy", "Add to Bag"]

        async def propose(self, data: PlannerInput) -> Proposal:
            if not data.steps:
                return call("windows.open_chrome_url", {"url": url})
            assert data.steps[-1].outcome.status is Status.SUCCESS
            observation = chrome_handoff_observation(data)
            if observation is not None:
                return observation
            last = data.steps[-1]
            result = WindowsResult.model_validate_json(last.outcome.result_json or "null")
            if last.tool == "windows.get_open_windows":
                windows = [window for window in result.windows if window.title.startswith(token)]
                if not windows:
                    return call("windows.get_open_windows", {"app": None})
                assert len(windows) == 1
                if not self.choices:
                    assert "CART VERIFIED" in windows[0].title
                    return Proposal(kind="finish")
                return call("windows.focus_app", {"target": windows[0].model_dump(mode="json")})
            if last.tool == "windows.inspect_active_window":
                assert result.target is not None and result.target.title.startswith(token)
                wanted = self.choices[0]
                elements = [element for element in result.elements if element.name == wanted]
                if not elements:
                    return call("windows.inspect_active_window", {})
                assert len(elements) == 1, (
                    wanted,
                    [(e.control_type, e.name) for e in result.elements],
                )
                self.choices.pop(0)
                return call(
                    "windows.click_element",
                    {
                        "target": result.target.model_dump(mode="json"),
                        "element": elements[0].model_dump(mode="json"),
                    },
                )
            if not self.choices:
                return call("windows.get_open_windows", {"app": None})
            return call("windows.inspect_active_window", {})

    async def no_prompt(*args: object) -> None:
        pytest.fail("Fixture selections must not need a model, privacy grant or effect approval")

    try:
        registry = ToolRegistry()
        register_windows(registry, ProcessBackend(timeout_seconds=10))
        with closing(AuditLog(tmp_path / "audit.sqlite3")) as audit:
            engine = PermissionEngine(registry, ApprovalStore(), audit)
            result = await Runner(
                registry,
                engine,
                FixturePlanner(),
                no_prompt,
                no_prompt,
                limits=Limits(max_steps=25, total_seconds=120),
            ).run("Open the local fixture and prepare the 256 GB burgundy cart", Mode.EXECUTE)
        assert result.status == "finished", (result.status, result.error)
        assert cart == [{"storage": ["256"], "colour": ["burgundy"]}]
        assert sum(step.tool == "windows.click_element" for step in result.steps) == 4
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
