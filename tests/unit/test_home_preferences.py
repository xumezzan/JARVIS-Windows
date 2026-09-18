"""The name survives a restart, and only a name is ever kept as one."""

import json
from pathlib import Path

from jarvis.ui.preferences import HomePreferences, valid_name

THEMES = ("Midnight", "Graphite")


def test_the_greeting_is_remembered_between_launches(tmp_path: Path) -> None:
    path = tmp_path / "home.json"
    store = HomePreferences(path, THEMES)
    assert store.name == "" and store.theme == "Midnight"

    store.remember("  Хумоюн  ", "Graphite")
    restored = HomePreferences(path, THEMES)
    assert restored.name == "Хумоюн" and restored.theme == "Graphite"


def test_only_a_name_is_kept_as_a_name() -> None:
    assert valid_name("Хумоюн") and valid_name("Анна-Мария") and valid_name("O'Neill")
    # The same rule the memory labels are held to: no identifiers, no secrets, no notes.
    assert not valid_name("")
    assert not valid_name("token=42")
    assert not valid_name("x" * 41)
    assert not valid_name("пароль 12345678")


def test_something_that_is_not_a_name_leaves_the_greeting_without_one(tmp_path: Path) -> None:
    path = tmp_path / "home.json"
    store = HomePreferences(path, THEMES)
    store.remember("Хумоюн", "Midnight")
    store.remember("api_key=abc", "Midnight")
    assert store.name == "" and HomePreferences(path, THEMES).name == ""


def test_an_unknown_palette_falls_back_to_the_first_one(tmp_path: Path) -> None:
    path = tmp_path / "home.json"
    HomePreferences(path, THEMES).remember("Хумоюн", "Neon")
    assert HomePreferences(path, THEMES).theme == "Midnight"


def test_a_damaged_file_costs_the_preference_and_nothing_else(tmp_path: Path) -> None:
    path = tmp_path / "home.json"
    for content in ("", "{", "[]", json.dumps({"name": 12, "theme": 7})):
        path.write_text(content, encoding="utf-8")
        store = HomePreferences(path, THEMES)
        assert store.name == "" and store.theme == "Midnight"
    path.write_text(json.dumps({"name": "x" * 5000}), encoding="utf-8")
    assert HomePreferences(path, THEMES).name == ""
    path.write_text(json.dumps({"name": "Хумоюн", "theme": "Graphite"}), encoding="utf-8")
    assert HomePreferences(path, THEMES).name == "Хумоюн"


def test_an_unwritable_path_still_greets_this_session(tmp_path: Path) -> None:
    store = HomePreferences(tmp_path / "missing" / "home.json", THEMES)
    (tmp_path / "missing").write_text("not a directory", encoding="utf-8")
    store.remember("Хумоюн", "Graphite")
    # The window greets them now; the next launch simply starts without the name again.
    assert store.name == "Хумоюн" and store.theme == "Graphite"
