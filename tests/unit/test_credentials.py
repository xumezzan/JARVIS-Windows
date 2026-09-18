"""The quiet credential-store modes a window uses. No real vault and no real key."""

import asyncio
import io
import sys

import pytest

from jarvis.security import credentials


class FakeVault:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], str] = {}
        self.reads = 0

    def get_password(self, service: str, account: str) -> str | None:
        self.reads += 1
        return self.entries.get((service, account))

    def set_password(self, service: str, account: str, key: str) -> None:
        self.entries[(service, account)] = key

    def delete_password(self, service: str, account: str) -> None:
        del self.entries[(service, account)]


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> FakeVault:
    fake = FakeVault()
    monkeypatch.setattr("jarvis.platforms.credentials.native_store", lambda: fake)
    # Nothing in these tests is a console: the helper is always spoken to through pipes.
    monkeypatch.setattr(credentials, "console", lambda stream, standard_input: False)
    return fake


def run(monkeypatch: pytest.MonkeyPatch, arguments: list[str], typed: str = "") -> tuple[int, str]:
    out = io.StringIO()
    monkeypatch.setattr(sys, "argv", ["credentials", *arguments])
    monkeypatch.setattr(sys, "stdin", io.StringIO(typed))
    monkeypatch.setattr(sys, "stdout", out)
    code = credentials.main()
    return code, out.getvalue()


def test_asking_answers_whether_and_never_what(
    monkeypatch: pytest.MonkeyPatch, vault: FakeVault
) -> None:
    vault.entries[("Jarvis/DeepSeek", "default")] = "synthetic-noncredential"
    code, written = run(monkeypatch, ["--has", "deepseek"])
    assert (code, written) == (0, "1\n")
    code, written = run(monkeypatch, ["--has", "openai"])
    assert (code, written) == (0, "0\n")
    # The whole point: the answer is one character, and it is not the key.
    assert "synthetic" not in written


def test_a_key_arrives_through_the_pipe_and_is_stored(
    monkeypatch: pytest.MonkeyPatch, vault: FakeVault
) -> None:
    code, written = run(monkeypatch, ["--store", "fireflies"], typed="synthetic-noncredential\n")
    assert (code, written) == (0, "ok\n")
    assert vault.entries[("Jarvis/Fireflies", "default")] == "synthetic-noncredential"


def test_a_typed_key_belongs_to_the_hidden_prompt_not_to_the_pipe(
    monkeypatch: pytest.MonkeyPatch, vault: FakeVault
) -> None:
    """A console on stdin means somebody is typing, and `set` hides what they type."""
    monkeypatch.setattr(credentials, "console", lambda stream, standard_input: standard_input)
    code, _ = run(monkeypatch, ["--store", "fireflies"], typed="synthetic-noncredential\n")
    assert code == 1 and vault.entries == {}


def test_an_unusable_key_is_refused_before_it_is_stored(
    monkeypatch: pytest.MonkeyPatch, vault: FakeVault
) -> None:
    for typed in ("\n", " \n", "с пробелом и кириллицей\n"):
        code, _ = run(monkeypatch, ["--store", "openai"], typed=typed)
        assert code == 1
    assert vault.entries == {}


def test_forgetting_removes_the_entry_and_is_quiet_about_a_missing_one(
    monkeypatch: pytest.MonkeyPatch, vault: FakeVault
) -> None:
    vault.entries[("Jarvis/OpenAI", "default")] = "synthetic-noncredential"
    code, written = run(monkeypatch, ["--forget", "openai"])
    assert (code, written) == (0, "ok\n") and vault.entries == {}
    # Forgetting what was never there is the same outcome, not an error to explain.
    code, written = run(monkeypatch, ["--forget", "openai"])
    assert (code, written) == (0, "ok\n")


def test_an_unknown_vendor_reaches_no_store_at_all(
    monkeypatch: pytest.MonkeyPatch, vault: FakeVault
) -> None:
    """The allowlist keeps an argument from naming an arbitrary entry in the vault."""
    code, _ = run(monkeypatch, ["--has", "../../Windows"])
    # An unknown name falls back to the default vendor rather than reaching a new one.
    assert code == 0 and vault.reads == 1
    assert all("Windows" not in service for service, _ in vault.entries)


def test_the_quiet_modes_never_print_setup_advice(
    monkeypatch: pytest.MonkeyPatch, vault: FakeVault
) -> None:
    """A window parses these answers; a sentence of advice would be read as one."""
    for mode in ("--has", "--store", "--forget"):
        _, written = run(monkeypatch, [mode, "openai"])
        assert "Настройка:" not in written


@pytest.mark.asyncio
async def test_a_key_is_never_passed_as_an_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    """A command line is readable by anything that can list processes."""
    seen: dict[str, tuple[str, ...] | bytes | None] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, written: bytes | None = None) -> tuple[bytes, bytes]:
            seen["written"] = written
            return b"ok\n", b""

        async def wait(self) -> int:
            return 0

    async def spawn(*arguments: str, **options: object) -> FakeProcess:
        seen["arguments"] = arguments
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    await credentials.store_api_key("openai", "synthetic-noncredential")
    spawned = seen["arguments"]
    assert isinstance(spawned, tuple)
    assert "synthetic-noncredential" not in " ".join(spawned)
    assert seen["written"] == b"synthetic-noncredential\n"
