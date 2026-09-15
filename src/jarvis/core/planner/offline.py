"""Explicit teaching recipes, not an LLM. Only user text selects a recipe."""

import asyncio
import json
import re

from jarvis.core.planner.contracts import PlannerInput, Proposal
from jarvis.permissions.policies import Mode


def call(tool: str, arguments: object) -> Proposal:
    return Proposal(kind="call", tool=tool, arguments=json.dumps(arguments, ensure_ascii=False))


class OfflineProvider:
    def __init__(self) -> None:
        self._answer_count = 0
        self._start_step = 0

    async def propose(self, data: PlannerInput) -> Proposal:
        await asyncio.sleep(0)
        if not data.steps and not data.answers:
            self._answer_count, self._start_step = 0, 0
        if len(data.answers) != self._answer_count:
            self._answer_count, self._start_step = len(data.answers), len(data.steps)
        command = (data.answers[-1] if data.answers else data.command).strip()
        count = len(data.steps) - self._start_step
        if command.casefold() in ("проверь систему", "проверь систему дважды"):
            total = 2 if command.casefold().endswith("дважды") else 1
            return call("local.check", {}) if count < total else Proposal(kind="finish")
        typed = re.fullmatch(r"открой блокнот и напиши «([\s\S]+)»", command, re.IGNORECASE)
        if typed or command.casefold() == "открой блокнот":
            if count == 0:
                return call("windows.open_app", {"app": "notepad"})
            if typed and count == 1 and data.mode is Mode.EXECUTE:
                result = json.loads(data.steps[-1].outcome.result_json or "{}")
                target = result.get("target")
                if not target or not target.get("empty") or not target.get("editor"):
                    return Proposal(
                        kind="clarify",
                        question="Подготовьте пустой Блокнот и укажите новую команду.",
                    )
                return call("windows.type_text", {"target": target, "text": typed.group(1)})
            return Proposal(kind="finish")
        opened = re.fullmatch(r"открой (https://\S+)", command, re.IGNORECASE)
        search = re.fullmatch(r"найди ([\s\S]+)", command, re.IGNORECASE)
        if opened or search:
            if count == 0:
                if opened:
                    return call("browser.open", {"url": opened.group(1)})
                assert search is not None
                return call("browser.search", {"query": search.group(1)})
            if count == 1 and data.mode is Mode.EXECUTE:
                result = json.loads(data.steps[-1].outcome.result_json or "{}")
                if result.get("page"):
                    return call("browser.read", {"target": result["page"]["target"]})
            return Proposal(kind="finish")
        return Proposal(
            kind="clarify",
            question=(
                "Офлайн-режим понимает учебные команды: «проверь систему», «открой блокнот», "
                "«открой https://example.com/», «найди OpenAI». Укажите одну из них "
                "или используйте OpenAI."
            ),
        )
