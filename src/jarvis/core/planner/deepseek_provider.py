"""DeepSeek chat/completions planning. Same rules and schemas as the Responses provider.

DeepSeek speaks the OpenAI chat format rather than the Responses API, so the request shape
differs, but the instructions and the tool schemas are imported from the other provider on
purpose: two planners that follow different rules would make behaviour depend on which
model happened to be cheap that minute.

One deliberate difference. DeepSeek's `strict` schema mode is beta-only and is documented
to return malformed JSON in function arguments, so it is not requested here. The schemas
are still sent in full — they guide the model — and correctness is enforced where it always
was: the proposal is parsed strictly, and the registry revalidates every argument before
anything runs. A model that answers off-schema therefore produces a finite
`provider_output` error and never a half-understood action.
"""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import aiohttp

from jarvis.browser.network import tls_context
from jarvis.core.planner.contracts import PlannerInput, Proposal, ProviderError
from jarvis.core.planner.openai_provider import INSTRUCTIONS, strict_schema, user_content
from jarvis.permissions.policies import proposable
from jarvis.security.credentials import load_api_key

ENDPOINT = "https://api.deepseek.com/chat/completions"
MAX_BYTES = 262144

# A control answer the model wrapped in an explanation. This vendor is not asked for
# structured output - its strict mode is documented to return malformed arguments - so the
# answer arrives as ordinary text, and it often arrives as a written summary with the JSON
# below it in a Markdown fence. Measured on deepseek-flash, 2026-09-18: a task that had
# created and read back every file it was asked for was then reported as a provider error,
# because the sentence in front of the JSON made the whole answer unreadable.
FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def control_answer(content: str) -> str:
    """The JSON of a control answer, whether it arrives bare or inside an explanation.

    Only `clarify` and `finish` can come this way - a call in the text is rejected right
    after - so being lenient here can ask the owner a question or end a task, and can never
    carry out an action. The value is still validated strictly afterwards.
    """
    text = content.strip()
    if text.startswith("{"):
        return text
    fences = FENCED.findall(text)
    # The answer is what the model settled on, so the last block wins over any it showed
    # along the way.
    return fences[-1] if fences else text


async def request(payload: dict[str, Any]) -> bytes:
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > MAX_BYTES:
        raise ProviderError("context_limit")
    key = await load_api_key("deepseek")
    try:
        async with (
            aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=tls_context(), force_close=True),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
                timeout=aiohttp.ClientTimeout(total=30),
                auto_decompress=False,
            ) as session,
            session.post(
                ENDPOINT,
                data=encoded,
                headers={
                    "Authorization": "Bearer " + key,
                    "Content-Type": "application/json",
                    "Accept-Encoding": "identity",
                },
                allow_redirects=False,
            ) as response,
        ):
            if response.status != 200 or response.headers.get("Content-Encoding"):
                raise ProviderError("provider_failed")
            content = bytearray()
            async for chunk in response.content.iter_chunked(16384):
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise ProviderError("provider_output")
            return bytes(content)
    except (asyncio.CancelledError, TimeoutError, ProviderError):
        raise
    except Exception:
        raise ProviderError("provider_failed") from None
    finally:
        key = ""


class DeepSeekProvider:
    tier: Literal["fast", "strong"] = "fast"

    def __init__(
        self, model: str, *, transport: Callable[[dict[str, Any]], Awaitable[bytes]] = request
    ) -> None:
        if not model or len(model) > 100 or any(ord(char) < 33 for char in model):
            raise ValueError("Specify a DeepSeek model identifier.")
        self.model = model
        self.transport = transport

    async def propose(self, data: PlannerInput) -> Proposal:
        catalog = json.loads(data.catalog_json)
        names: dict[str, str] = {}
        tools = []
        for tool in catalog:
            if not proposable(tool["risk"]):
                continue
            name = tool["name"].replace(".", "__")
            if name in names:
                raise ProviderError("provider_output")
            names[name] = tool["name"]
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool["description"] + " Risk: " + tool["risk"],
                        "parameters": strict_schema(tool["parameters"]),
                    },
                }
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": INSTRUCTIONS},
                {"role": "user", "content": user_content(data)},
            ],
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": 4096,
            "stream": False,
            # One action at a time, exactly as the Responses provider requires.
            "parallel_tool_calls": False,
        }
        if len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_BYTES:
            raise ProviderError("context_limit")
        raw = await self.transport(payload)
        if len(raw) > MAX_BYTES:
            raise ProviderError("provider_output")
        try:
            response = json.loads(raw)
            choices = response["choices"]
            if len(choices) != 1:
                raise ValueError
            choice = choices[0]
            if choice.get("finish_reason") == "length":
                raise ValueError
            message = choice["message"]
            calls = message.get("tool_calls") or []
            if calls:
                # `parallel_tool_calls: false` is sent and not honoured: asked for three
                # files, this vendor answers with three calls at once. Measured on
                # deepseek-flash, 2026-09-18. Refusing the whole answer cost the task its
                # first action and, with no retries, the task itself.
                #
                # The rule the assistant keeps is that exactly one action is taken per step,
                # and that still holds: the first call is proposed, the rest are dropped
                # unexecuted. They were written before the model could see any outcome, so
                # they are guesses about a world it has not observed yet; the next step is
                # asked for again with the real result in hand.
                if calls[0].get("type") != "function":
                    raise ValueError
                function = calls[0]["function"]
                return Proposal(
                    kind="call", tool=names[function["name"]], arguments=function["arguments"]
                )
            proposal = Proposal.model_validate_json(control_answer(message["content"]))
            if proposal.kind == "call":
                raise ValueError
            return proposal
        except Exception:
            raise ProviderError("provider_output") from None
