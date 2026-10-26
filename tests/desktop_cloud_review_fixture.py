"""UI snapshots only: no network, credential access or model execution."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from jarvis.config import AppConfig
from jarvis.core.planner.contracts import PlannerInput, Proposal
from jarvis.desktop.service import DesktopService
from jarvis.desktop.settings import UiSettings, UiSettingsStore
from jarvis.permissions.policies import Mode


class NoCloud:
    async def propose(self, data: PlannerInput) -> Proposal:
        raise AssertionError("UI fixture must not call any provider")


async def main() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory)
        UiSettingsStore(path).save(
            UiSettings(
                provider="openai",
                planner_model="fixture",
                routine_cloud_consent=True,
            )
        )
        service = DesktopService(AppConfig(path), lambda event: None, provider=NoCloud())
        try:
            ordinary = service.snapshot()
            assert not service.start("Оплати счёт и отправь подтверждение", Mode.EXECUTE)
            print(
                json.dumps({"ordinary": ordinary, "review": service.snapshot()}, ensure_ascii=False)
            )
        finally:
            await service.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
