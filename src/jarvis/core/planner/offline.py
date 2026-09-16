"""Explicit teaching recipes, not an LLM. Only user text selects a recipe."""

import asyncio
import json
import re

from jarvis.core.planner.contracts import PlannerInput, Proposal
from jarvis.permissions.policies import Mode

# A spoken application name, never a path, argument or URL; the adapter resolves it.
APP_NAME = re.compile(r"[0-9A-Za-z\u0400-\u04FF][0-9A-Za-z\u0400-\u04FF ._+\-]{0,99}")
LAUNCH = re.compile(r"(?:открой|открыть|запусти|запустить|включи)\s+([\s\S]+)", re.IGNORECASE)


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
        typed = re.fullmatch(
            r"открой блокнот и напиши (?:«([\s\S]+)»|([\s\S]+))", command, re.IGNORECASE
        )
        if command.casefold() in ("открой выбранное приложение", "открой моё приложение"):
            apps = {
                hint.value
                for hint in (*data.memory.profile, *data.memory.session)
                if hint.kind == "application"
            }
            if len(apps) != 1:
                return Proposal(
                    kind="clarify",
                    question=(
                        "Какое приложение открыть? Укажите: открой блокнот, открой chrome "
                        "или открой vs code. Память не выбирает за вас."
                    ),
                )
            return (
                call("windows.open_app", {"app": next(iter(apps))})
                if count == 0
                else Proposal(kind="finish")
            )
        if re.search(r"(?:напиши|отправь|письмо|свяжись)", command, re.IGNORECASE) and not typed:
            return Proposal(
                kind="clarify",
                question=(
                    "Уточните полное имя, адресата и желаемое действие. Сохранённая роль или имя "
                    "не определяют получателя. Для почты используйте вкладку Outlook "
                    "с отдельным подтверждением точного письма."
                ),
            )
        if command.casefold() in ("проверь систему", "проверь систему дважды"):
            total = 2 if command.casefold().endswith("дважды") else 1
            return call("local.check", {}) if count < total else Proposal(kind="finish")
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
                return call(
                    "windows.type_text",
                    {"target": target, "text": typed.group(1) or typed.group(2)},
                )
            return Proposal(kind="finish")
        opened = re.fullmatch(r"открой (https://\S+)", command, re.IGNORECASE)
        search = re.fullmatch(r"найди ([\s\S]+)", command, re.IGNORECASE)
        combined = re.fullmatch(r"открой chrome и найди ([\s\S]+)", command, re.IGNORECASE)
        if combined:
            if count == 0:
                return call("windows.open_app", {"app": "chrome"})
            # Search remains in Jarvis's owned anonymous Chromium, under browser policy.
            search = combined
            count -= 1
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
        launch = LAUNCH.fullmatch(command)
        if launch:
            name = " ".join(launch.group(1).split())
            if APP_NAME.fullmatch(name):
                if count == 0:
                    return call("windows.open_app", {"app": name})
                return Proposal(kind="finish")
        return Proposal(
            kind="clarify",
            question=(
                "Без модели понимаю простые команды: «открой <название приложения>», "
                "«проверь систему», «открой https://example.com/», «найди OpenAI». "
                "Для произвольных формулировок включите провайдер OpenAI."
            ),
        )
