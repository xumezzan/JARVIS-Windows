"""Opt-in real API protocol check. Never invokes tool adapters or reads user's pages."""

import asyncio
import json
import os

import pytest

from jarvis.browser.host import BrowserHost
from jarvis.core.planner.contracts import PlannerInput
from jarvis.core.planner.openai_provider import OpenAIProvider
from jarvis.permissions.policies import Mode
from jarvis.platforms.windows.transport import ProcessBackend
from jarvis.tools.browser import register_browser
from jarvis.tools.local import local_registry
from jarvis.tools.windows import register_windows


@pytest.mark.model
@pytest.mark.asyncio
async def test_real_provider_emits_one_valid_registered_check() -> None:
    model = os.environ.get("JARVIS_PLANNER_MODEL", "")
    assert model, "Set non-secret JARVIS_PLANNER_MODEL and store the API key using the setup CLI."
    registry, _ = local_registry()
    host = BrowserHost()
    register_browser(registry, host, host.policy)
    register_windows(registry, ProcessBackend())
    data = PlannerInput(
        "Вызови local.check один раз с delay_ms=0 и fail=false: это проверка протокола.",
        (),
        (),
        Mode.SIMULATION,
        json.dumps(registry.discover()),
    )
    try:
        async with asyncio.timeout(40):
            proposal = await OpenAIProvider(model).propose(data)
        assert proposal.kind == "call" and proposal.tool == "local.check"
        tool = registry.get(proposal.tool)
        assert tool is not None
        tool.normalize(json.loads(proposal.arguments))
        assert host._thread is None
    finally:
        host.shutdown()
