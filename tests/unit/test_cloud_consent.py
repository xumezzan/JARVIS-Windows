"""Consent survives a restart, and nothing but a clear yes is read as one."""

import json
from pathlib import Path

from jarvis.security.cloud_consent import CloudConsent


def test_answer_holds_until_it_is_taken_back(tmp_path: Path) -> None:
    path = tmp_path / "cloud-consent.json"
    store = CloudConsent(path)
    assert not store.granted and not store.model

    store.remember("gpt-5.4-mini", True)
    assert CloudConsent(path).granted and CloudConsent(path).model == "gpt-5.4-mini"

    # Unticking in the planner is the way back; the identifier is kept for the next question.
    store.remember("gpt-5.4-mini", False)
    restored = CloudConsent(path)
    assert not restored.granted and restored.model == "gpt-5.4-mini"


def test_identifier_must_be_one_the_provider_would_accept(tmp_path: Path) -> None:
    path = tmp_path / "cloud-consent.json"
    CloudConsent(path).remember("gpt 5.4 mini", True)
    stored = CloudConsent(path)
    # A name with spaces is refused here rather than failing later inside the provider.
    assert not stored.granted and not stored.model


def test_consent_without_a_recipient_is_not_consent(tmp_path: Path) -> None:
    path = tmp_path / "cloud-consent.json"
    CloudConsent(path).remember("", True)
    assert not CloudConsent(path).granted


def test_a_damaged_file_asks_again_instead_of_guessing(tmp_path: Path) -> None:
    path = tmp_path / "cloud-consent.json"
    for content in ("", "{", "[]", json.dumps({"granted": "yes", "model": "gpt-5.4-mini"})):
        path.write_text(content, encoding="utf-8")
        assert not CloudConsent(path).granted
    path.write_text(json.dumps({"granted": True, "model": "x" * 200}), encoding="utf-8")
    assert not CloudConsent(path).granted
    path.write_text(json.dumps({"granted": True, "model": "gpt-5.4-mini"}), encoding="utf-8")
    assert CloudConsent(path).granted


def test_an_unwritable_path_costs_the_question_and_nothing_else(tmp_path: Path) -> None:
    store = CloudConsent(tmp_path / "missing-dir" / "sub" / "cloud-consent.json")
    (tmp_path / "missing-dir").write_text("not a directory", encoding="utf-8")
    store.remember("gpt-5.4-mini", True)
    # The session it was given in still honours it; the next launch simply asks again.
    assert store.granted and store.model == "gpt-5.4-mini"
