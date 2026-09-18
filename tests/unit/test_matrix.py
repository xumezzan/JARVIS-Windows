"""Owner permission policy and the composition root. No network, account or secret."""

import json
from pathlib import Path

import pytest

from jarvis.config import AppConfig
from jarvis.connectors.base import Capability
from jarvis.core.composition import build
from jarvis.permissions.matrix import PermissionMatrix, Rule, load_matrix
from jarvis.permissions.policies import Risk

READ = Capability("search_tasks", "Найти задачи.", Risk.SAFE, True, reads=("task",))
WRITE = Capability("create_task", "Создать задачу.", Risk.CONFIRM, False, writes=("task",))
SEND = Capability("send_mail", "Отправить письмо.", Risk.CRITICAL, False, writes=("conversation",))


def write_matrix(path: Path, data: object) -> Path:
    target = path / "permissions.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    return target


def test_an_unknown_service_keeps_the_risk_its_connector_declared() -> None:
    matrix = PermissionMatrix({"notion": {"create_page": Rule(allowed=False)}})
    assert matrix.effective("asana", READ) is Risk.SAFE
    assert matrix.effective("asana", WRITE) is Risk.CONFIRM
    assert matrix.effective("asana", SEND) is Risk.CRITICAL


def test_policy_may_tighten_a_capability_but_never_loosen_it() -> None:
    matrix = PermissionMatrix(
        {
            "asana": {
                "search_tasks": Rule(risk=Risk.CONFIRM),
                "create_task": Rule(risk=Risk.SAFE),
                "send_mail": Rule(risk=Risk.BLOCKED),
            }
        }
    )
    assert matrix.effective("asana", READ) is Risk.CONFIRM
    # The connector declared CONFIRM; a configuration asking for SAFE is ignored.
    assert matrix.effective("asana", WRITE) is Risk.CONFIRM
    assert matrix.effective("asana", SEND) is Risk.BLOCKED


def test_a_refusal_blocks_and_a_wildcard_covers_every_capability() -> None:
    matrix = PermissionMatrix(
        {"aws": {"*": Rule(allowed=False)}, "asana": {"*": Rule(risk=Risk.CRITICAL)}}
    )
    assert matrix.effective("aws", READ) is Risk.BLOCKED
    assert matrix.effective("asana", READ) is Risk.CRITICAL
    explicit = PermissionMatrix(
        {"asana": {"*": Rule(allowed=False), "search_tasks": Rule(allowed=True)}}
    )
    assert explicit.effective("asana", READ) is Risk.SAFE
    assert explicit.effective("asana", WRITE) is Risk.BLOCKED


def test_matrix_rejects_untrusted_shapes() -> None:
    for rules in (
        {"Asana": {"search_tasks": Rule()}},
        {"asana": {"search.tasks": Rule()}},
        {"asana": {"search_tasks": {"allowed": True}}},
    ):
        with pytest.raises(ValueError):
            PermissionMatrix(rules)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Rule(allowed="yes")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Rule(risk="SAFE")  # type: ignore[arg-type]


def test_a_missing_file_means_no_policy_not_an_empty_allowlist(tmp_path: Path) -> None:
    matrix = load_matrix(tmp_path / "permissions.json")
    assert matrix.describe() == []
    assert matrix.effective("asana", WRITE) is Risk.CONFIRM


def test_a_readable_file_becomes_policy(tmp_path: Path) -> None:
    path = write_matrix(tmp_path, {"asana": {"create_task": {"allowed": True, "risk": "CRITICAL"}}})
    matrix = load_matrix(path)
    assert matrix.effective("asana", WRITE) is Risk.CRITICAL
    assert matrix.describe() == [
        {"service": "asana", "capability": "create_task", "allowed": True, "risk": "CRITICAL"}
    ]


def test_an_unusable_file_fails_closed_instead_of_being_guessed(tmp_path: Path) -> None:
    for data in (
        ["asana"],
        {"asana": "all"},
        {"asana": {"create_task": {"allowed": True, "extra": 1}}},
        {"asana": {"create_task": {"risk": "SOMETIMES"}}},
    ):
        with pytest.raises(ValueError):
            load_matrix(write_matrix(tmp_path, data))
    broken = tmp_path / "permissions.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_matrix(broken)
    broken.write_text(json.dumps({"asana": {"x": {}}}) + " " * 70000, encoding="utf-8")
    with pytest.raises(ValueError):
        load_matrix(broken)


def test_composition_builds_one_surface_with_a_single_approval_issuer(tmp_path: Path) -> None:
    bench = build(AppConfig(tmp_path))
    names = {tool["name"] for tool in bench.registry.discover()}
    expected = {"local.check", "browser.read", "windows.open_app", "files.find", "outlook.list"}
    assert expected <= names
    assert bench.engine is not None and bench.authority is not None
    # Connectors declare themselves through the contract and appear in one inventory.
    assert bench.connectors.services() == (
        "calendar",
        "fireflies",
        "asana",
        "notion",
        "teams",
        "onedrive",
    )
    assert {"calendar.list", "calendar.create", "fireflies.get"} <= names
    assert {"asana.tasks", "asana.task", "asana.projects", "asana.create_task"} <= names
    assert {"notion.search", "notion.read", "notion.create_page", "notion.append"} <= names
    assert {"teams.chats", "teams.messages", "teams.draft", "teams.send"} <= names
    assert {"onedrive.files", "onedrive.sheets", "onedrive.read", "onedrive.write"} <= names
    assert (tmp_path / "audit.sqlite3").exists()
    # The registry is sealed once the engine owns it, so nothing can be added later.
    with pytest.raises(ValueError):
        bench.registry.register(None)  # type: ignore[arg-type]
    bench.shutdown()


def test_composition_reads_owner_policy_from_the_data_directory(tmp_path: Path) -> None:
    write_matrix(tmp_path, {"asana": {"*": {"allowed": False}}})
    bench = build(AppConfig(tmp_path))
    assert bench.matrix.effective("asana", READ) is Risk.BLOCKED
    bench.shutdown()


TYPING = Capability("type_text", "Ввести текст.", Risk.ROUTINE, False, writes=("document",))


def test_the_owner_may_raise_a_routine_capability_but_never_lower_a_confirmed_one() -> None:
    matrix = PermissionMatrix(
        {
            "desktop": {
                "type_text": Rule(risk=Risk.CONFIRM),
                "create_task": Rule(risk=Risk.ROUTINE),
                "search_tasks": Rule(risk=Risk.ROUTINE),
            }
        }
    )
    # Raising the everyday level back to a confirmed one is honoured.
    assert matrix.effective("desktop", TYPING) is Risk.CONFIRM
    # Asking for less caution than the connector declared is ignored, as before.
    assert matrix.effective("desktop", WRITE) is Risk.CONFIRM
    # Raising a read to the everyday level is still a tightening.
    assert matrix.effective("desktop", READ) is Risk.ROUTINE


def test_a_writing_capability_may_be_routine_but_never_safe() -> None:
    Capability("type_text", "Ввести текст.", Risk.ROUTINE, False, writes=("document",))
    with pytest.raises(ValueError):
        Capability("type_text", "Ввести текст.", Risk.SAFE, False, writes=("document",))
