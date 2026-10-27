"""Stdlib diagnostics read real SQLite files without rewriting or exporting content."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from scripts.windows.diagnose_failures import data_roots, inspect_root, render


def test_audit_window_is_exactly_last_fifty_and_legacy_stage_is_unknown(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.sqlite3"
    with closing(sqlite3.connect(audit_path)) as db:
        db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, record TEXT NOT NULL)")
        for index in range(60):
            db.execute(
                "INSERT INTO events(record) VALUES (?)",
                (
                    json.dumps(
                        {
                            "tool": "windows.focus_app",
                            "status": "DENIED" if index % 2 else "ERROR",
                            "error": "unobserved_target",
                            "request_id": str(index),
                            "arguments": "PRIVATE_ARGUMENT",
                            "result_summary": "PRIVATE_RESULT",
                            **(
                                {"stage": "_observed_target", "reason": "target_not_observed"}
                                if index == 59
                                else {}
                            ),
                        }
                    ),
                ),
            )
        db.commit()
    with closing(sqlite3.connect(tmp_path / "memory.sqlite3")) as db:
        db.execute("CREATE TABLE recent_tasks(id TEXT, created INTEGER, payload TEXT)")
        db.execute(
            "INSERT INTO recent_tasks VALUES (?, ?, ?)",
            (
                "recent",
                1,
                json.dumps({"command": "PRIVATE_COMMAND", "tools": ["windows.open_app"]}),
            ),
        )
        db.commit()
    (tmp_path / "shell.jsonl").write_text(
        json.dumps({"event": "command_failed", "command": "PRIVATE_COMMAND"}) + "\n",
        encoding="utf-8",
    )
    before = audit_path.read_bytes()
    result = inspect_root(tmp_path)
    assert audit_path.read_bytes() == before
    assert result["audit_records_read"] == len(result["failures"]) == 50
    assert result["statuses"] == {"DENIED": 25, "ERROR": 25}
    assert result["failure_stages"] == {"_observed_target": 1, "not_recorded": 49}
    assert result["failures"][0]["id"] == 60 and result["failures"][-1]["id"] == 11
    assert result["recent_tasks"][0]["tools"] == ["windows.open_app"]
    assert "PRIVATE" not in json.dumps(result)
    markdown = render({"host": "fixture", "limit": 50, "roots": [result]})
    assert "windows.focus_app" in markdown and "_observed_target" in markdown


def test_discovery_includes_store_virtualization_and_explicit_data_dir(tmp_path: Path) -> None:
    virtual = tmp_path / "Packages" / "Codex_fixture" / "LocalCache" / "Local" / "Jarvis"
    virtual.mkdir(parents=True)
    override = tmp_path / "custom"
    roots = data_roots({"LOCALAPPDATA": str(tmp_path), "JARVIS_DATA_DIR": str(override)})
    assert roots == [override, tmp_path / "Jarvis", virtual]


def test_missing_databases_are_reported_without_creating_them(tmp_path: Path) -> None:
    result = inspect_root(tmp_path)
    assert set(result["sources"].values()) == {"missing"}
    assert result["failures"] == [] and list(tmp_path.iterdir()) == []


def test_malformed_record_does_not_hide_other_failures(tmp_path: Path) -> None:
    with closing(sqlite3.connect(tmp_path / "audit.sqlite3")) as db:
        db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, record TEXT)")
        db.executemany(
            "INSERT INTO events(record) VALUES (?)",
            [("[not-json",), ('{"status":"TIMEOUT","tool":"windows.open_app"}',)],
        )
        db.commit()
    result = inspect_root(tmp_path)
    assert result["malformed_records"] == 1 and len(result["failures"]) == 1
