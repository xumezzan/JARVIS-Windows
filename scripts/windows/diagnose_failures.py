"""Read-only audit/JSONL/memory diagnostics. Stdlib only; no credentials or payload export."""

import argparse
import json
import os
import platform
import sqlite3
from collections import Counter, deque
from contextlib import closing
from pathlib import Path
from typing import Any

FAILURES = {"ERROR", "DENIED", "CANCELLED", "TIMEOUT", "INVALID"}
AUDIT_KEYS = (
    "timestamp",
    "session_id",
    "request_id",
    "tool",
    "event",
    "status",
    "error",
    "stage",
    "reason",
    "may_have_effects",
    "permission_decision",
)


def data_roots(environ: dict[str, str] | None = None) -> list[Path]:
    env = os.environ if environ is None else environ
    roots = []
    if env.get("JARVIS_DATA_DIR"):
        roots.append(Path(env["JARVIS_DATA_DIR"]))
    local = env.get("LOCALAPPDATA")
    if local:
        base = Path(local)
        roots.append(base / "Jarvis")
        # Packaged Codex/terminal processes may have a redirected LOCALAPPDATA already.
        if base.parts[-2:] == ("LocalCache", "Local"):
            roots.append(Path.home() / "AppData" / "Local" / "Jarvis")
        packages = Path.home() / "AppData" / "Local" / "Packages"
        if (base / "Packages").is_dir():
            packages = base / "Packages"
        if packages.is_dir():
            roots.extend(sorted(packages.glob("*/LocalCache/Local/Jarvis")))
    return list(dict.fromkeys(path.resolve() for path in roots))


def read_database(path: Path, query: str, limit: int) -> tuple[list[Any], str]:
    if not path.is_file():
        return [], "missing"
    try:
        # mode=ro neither creates databases nor runs MemoryStore's retention/deletion hooks.
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        ) as db:
            db.execute("PRAGMA query_only=ON")
            db.set_progress_handler(lambda: 1, 1_000_000)
            return db.execute(query, (limit,)).fetchall(), "read"
    except sqlite3.Error as error:
        return [], type(error).__name__  # No raw SQLite text or data in diagnostics.


def inspect_root(root: Path, limit: int = 50) -> dict[str, Any]:
    rows, audit_state = read_database(
        root / "audit.sqlite3", "SELECT id, record FROM events ORDER BY id DESC LIMIT ?", limit
    )
    events = []
    malformed = 0
    for identifier, raw in rows:
        try:
            if not isinstance(raw, str) or len(raw) > 65536:
                raise ValueError
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError
            # Original records remain redacted. Never copy arguments/results into the report.
            event = {key: value.get(key) for key in AUDIT_KEYS}
            event["id"] = identifier
            event["stage_source"] = "recorded" if value.get("stage") else "not_recorded"
            events.append(event)
        except (ValueError, TypeError):
            malformed += 1
    failures = [event for event in events if event.get("status") in FAILURES]
    shell_path = root / "shell.jsonl"
    shell = []
    shell_state = "missing"
    if shell_path.is_file():
        try:
            # ShellLog rotates at 1 MB; bound reads even for unrelated/legacy log files.
            with shell_path.open("rb") as stream:
                size = stream.seek(0, 2)
                stream.seek(max(0, size - 2_000_000))
                if size > 2_000_000:
                    stream.readline(65537)
                tail = deque(stream, maxlen=limit)
            for line in tail:
                if len(line) > 65536:
                    continue
                try:
                    value = json.loads(line)
                    shell.append(
                        {
                            key: value.get(key)
                            for key in ("timestamp", "session_id", "request_id", "event")
                        }
                    )
                except (ValueError, TypeError, AttributeError):
                    continue
            shell_state = "read"
        except OSError as error:
            shell_state = type(error).__name__
    tasks, memory_state = read_database(
        root / "memory.sqlite3",
        "SELECT id, created, payload FROM recent_tasks ORDER BY rowid DESC LIMIT ?",
        limit,
    )
    recent = []
    for identifier, created, raw in tasks:
        try:
            if len(raw) > 262144:
                raise ValueError
            value = json.loads(raw)
            recent.append({"id": identifier, "created": created, "tools": value.get("tools", [])})
        except (ValueError, TypeError, AttributeError):
            continue
    return {
        "root": str(root),
        "sources": {
            "audit.sqlite3": audit_state,
            "shell.jsonl": shell_state,
            "memory.sqlite3/recent_tasks": memory_state,
        },
        "audit_records_read": len(rows),
        "malformed_records": malformed,
        "statuses": dict(Counter(event["status"] or "lifecycle" for event in events)),
        "failure_codes": dict(Counter(str(event["error"]) for event in failures)),
        "failure_stages": dict(Counter(event["stage"] or "not_recorded" for event in failures)),
        "failures": failures,
        "shell_events": shell,
        "recent_tasks": recent,
    }


def cell(value: object) -> str:
    return (
        str(value if value is not None else "не записан")
        .replace("|", "\\|")
        .replace("\n", " ")
        .replace("\r", " ")[:250]
    )


def render(report: dict[str, Any]) -> str:
    lines = [
        "# Jarvis Flight Recorder",
        "",
        f"Host: {report['host']}. Последние {report['limit']} записей каждого audit.",
        "",
        "Старые записи без stage не позволяют достоверно восстановить этап. [REDACTED] необратим.",
        (
            "recent_tasks хранит успешные задачи, а shell.jsonl — события оболочки, а "
            "не все отказы инструментов."
        ),
    ]
    if not report["roots"]:
        lines += [
            "",
            (
                "Windows data roots не доступны: LOCALAPPDATA не задан. Статистика Windows "
                "не получена."
            ),
        ]
    for root in report["roots"]:
        lines += [
            "",
            "## " + root["root"],
            "",
            "Источники: " + json.dumps(root["sources"], ensure_ascii=False),
            f"Прочитано записей: {root['audit_records_read']}; "
            f"повреждено: {root['malformed_records']}.",
            "Статусы: " + json.dumps(root["statuses"], ensure_ascii=False),
            "Коды отказов: " + json.dumps(root["failure_codes"], ensure_ascii=False),
            "Этапы отказов: " + json.dumps(root["failure_stages"], ensure_ascii=False),
            "",
            (
                "| ID | Время | Request | Tool | Статус | Этап | Ошибка | Правило | Могли "
                "быть эффекты |"
            ),
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for failure in root["failures"]:
            lines.append(
                "| "
                + " | ".join(
                    cell(failure.get(key))
                    for key in (
                        "id",
                        "timestamp",
                        "request_id",
                        "tool",
                        "status",
                        "stage",
                        "error",
                        "reason",
                        "may_have_effects",
                    )
                )
                + " |"
            )
        if not root["failures"]:
            lines.append(
                "| — | — | — | — | Нет записанных отказов в доступной выборке | — | — | — | — |"
            )
        lines += [
            "",
            f"Shell events: {len(root['shell_events'])}; "
            f"recent_tasks: {len(root['recent_tasks'])}.",
            "```json",
            json.dumps(
                {"shell_events": root["shell_events"], "recent_tasks": root["recent_tasks"]},
                ensure_ascii=False,
                indent=2,
            ),
            "```",
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", action="append", type=Path, default=[])
    parser.add_argument("--limit", type=int, default=50, choices=range(1, 1001), metavar="1..1000")
    parser.add_argument(
        "--output", type=Path, help="New report directory; existing files are never overwritten"
    )
    args = parser.parse_args()
    roots = list(dict.fromkeys([*data_roots(), *(p.resolve() for p in args.data_dir)]))
    report = {
        "host": platform.system(),
        "limit": args.limit,
        "roots": [inspect_root(root, args.limit) for root in roots],
    }
    markdown = render(report)
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "failures.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (args.output / "failures.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
