"""One allowlisted installer probe per bounded child; no live accounts, audio or network."""

import argparse
import importlib
import json
import os
import re
from contextlib import redirect_stdout
from importlib.metadata import version

from jarvis.installation.report import DEPENDENCY_CHECKS

DEPENDENCIES = tuple(DEPENDENCY_CHECKS.values())
PROBE_CHECKS = (
    *DEPENDENCY_CHECKS,
    "offline_chromium",
    "local_vosk",
    "permissions",
    "planner",
    "memory",
    "credentials_backend",
    "microphone",
    "speaker",
    "russian_voice",
    "package_versions",
)
PACKAGES = (
    "jarvis-windows",
    "PySide6-Essentials",
    "pydantic",
    "playwright",
    "aiohttp",
    "certifi",
    "keyring",
    "msal",
    "pywinauto",
    "psutil",
    "sounddevice",
    "vosk",
    "pyttsx3",
)
VERSION_PATTERN = re.compile(
    r"[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc|\.post|\.dev)[0-9]+)*(?:\+[a-z0-9]+(?:[._-][a-z0-9]+)*)?"
)


def check_browser() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, timeout=20000)
        try:
            context = browser.new_context(offline=True, java_script_enabled=False)
            page = context.new_page()
            page.set_default_timeout(5000)
            page.set_content("<html><body>Jarvis offline browser check</body></html>")
            if page.locator("body").inner_text() != "Jarvis offline browser check":
                raise RuntimeError("offline_browser_failed")
        finally:
            browser.close()


def audio_device_present(kind: str) -> bool:
    import sounddevice  # type: ignore[import-untyped]

    field = "max_input_channels" if kind == "microphone" else "max_output_channels"
    return any(device[field] > 0 for device in sounddevice.query_devices())


def russian_voice_present() -> bool:
    import pyttsx3  # type: ignore[import-untyped]

    engine = pyttsx3.init(driverName="sapi5")
    try:
        return any(
            "ru" in str(voice.languages).lower()
            or "russian" in voice.id.lower()
            or "ru-ru" in voice.id.lower()
            for voice in engine.getProperty("voices")
        )
    finally:
        engine.stop()


def run_check(name: str) -> dict[str, object]:
    if name not in PROBE_CHECKS:
        raise ValueError("Unknown installer probe.")
    try:
        if name in DEPENDENCY_CHECKS:
            importlib.import_module(DEPENDENCY_CHECKS[name])
        elif name == "offline_chromium":
            check_browser()
        elif name == "local_vosk":
            from vosk import Model  # type: ignore[import-untyped]

            Model(model_path=os.environ["JARVIS_VOSK_MODEL"])
        elif name in {"permissions", "planner", "memory", "credentials_backend"}:
            from jarvis.installation import offline_checks

            checks = {
                "permissions": offline_checks.check_permissions,
                "planner": offline_checks.check_planner,
                "memory": offline_checks.check_memory,
                "credentials_backend": offline_checks.check_credentials_backend,
            }
            checks[name]()
        elif name in {"microphone", "speaker", "russian_voice"}:
            present = (
                russian_voice_present() if name == "russian_voice" else audio_device_present(name)
            )
            if not present:
                return {"status": "warning", "code": "missing"}
        elif name == "package_versions":
            packages = {package: version(package) for package in PACKAGES}
            if any(
                len(value) > 80 or not VERSION_PATTERN.fullmatch(value)
                for value in packages.values()
            ):
                raise ValueError("Invalid package version.")
            return {"status": "passed", "code": "none", "packages": packages}
    except Exception:
        optional = name in {"microphone", "speaker", "russian_voice"}
        return {
            "status": "warning" if optional else "failed",
            "code": "discovery_failed" if optional else "check_failed",
        }
    return {"status": "passed", "code": "none"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", choices=PROBE_CHECKS, required=True)
    args = parser.parse_args()
    # Third-party Python prints are not evidence and must not enter the report protocol.
    with open(os.devnull, "w", encoding="utf-8") as discarded, redirect_stdout(discarded):
        result = run_check(args.check)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
