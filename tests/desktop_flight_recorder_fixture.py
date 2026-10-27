"""Renderer fixtures from the actual failure event projection; no model or OS actions."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from jarvis.config import AppConfig
from jarvis.core.planner.contracts import Step
from jarvis.desktop.service import DesktopService
from jarvis.observability.audit import ErrorCode
from jarvis.observability.diagnostics import Reason, Stage
from jarvis.permissions.engine import Outcome
from jarvis.permissions.policies import Status


async def main() -> None:
    with TemporaryDirectory() as directory:
        service = DesktopService(AppConfig(Path(directory)), lambda event: None)
        try:
            service.task.state = "error"
            service.task.command = "Открыть окно и дописать текст"
            for error, stage, reason, tool in (
                (
                    ErrorCode.UNOBSERVED_TARGET,
                    Stage.OBSERVED_TARGET,
                    Reason.TARGET_NOT_OBSERVED,
                    "windows.focus_app",
                ),
                (
                    ErrorCode.INVALID_REQUEST,
                    Stage.PREPARE,
                    Reason.INVALID_ARGUMENTS,
                    "windows.type_text",
                ),
                (ErrorCode.VERIFICATION, Stage.VERIFY, None, "windows.click_element"),
            ):
                service._event(
                    "tool",
                    Step(
                        tool,
                        Outcome(
                            uuid4(),
                            Status.DENIED if error is ErrorCode.UNOBSERVED_TARGET else Status.ERROR,
                            error,
                            error is ErrorCode.VERIFICATION,
                            stage=stage,
                            reason=reason,
                        ),
                        json.dumps({"target": {"app": "notepad"}}),
                    ),
                )
            print(json.dumps(service.snapshot(), ensure_ascii=False))
        finally:
            await service.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
