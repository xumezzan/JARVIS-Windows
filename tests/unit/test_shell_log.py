import json
from pathlib import Path
from uuid import uuid4

from jarvis.observability.events import ShellEvent
from jarvis.observability.logging import ShellLog


def test_structured_log_and_independent_sessions(tmp_path: Path) -> None:
    log = ShellLog(tmp_path)
    request = uuid4()
    log.record(ShellEvent.SUBMITTED, request)
    log.close()
    log.close()
    second = ShellLog(tmp_path)
    second.record(ShellEvent.CLOSED)
    second.close()
    rows = [json.loads(line) for line in log.path.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["request_id"] == str(request)
    assert rows[0]["session_id"] != rows[1]["session_id"]
    assert rows[0]["mode"] == "demo"
    assert set(rows[0]) == {"timestamp", "session_id", "request_id", "actor", "event", "mode"}
