"""Explicit installer probe: no microphone stream, speech, account or network access."""

import importlib
import json
import os
from importlib.metadata import distributions

DEPENDENCIES = (
    "PySide6.QtWidgets",
    "pydantic",
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


def check_browser() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(offline=True, java_script_enabled=False)
            page = context.new_page()
            page.set_content("<html><body>Jarvis offline browser check</body></html>")
            assert page.locator("body").inner_text() == "Jarvis offline browser check"
            browser_version = browser.version
        finally:
            browser.close()
    return browser_version


def main() -> None:
    for name in DEPENDENCIES:
        try:
            importlib.import_module(name)
        except Exception:
            print(json.dumps({"failure": name}))
            return
    try:
        browser_version = check_browser()
    except Exception:
        print(json.dumps({"failure": "chromium"}))
        return
    try:
        from vosk import Model  # type: ignore[import-untyped]

        Model(model_path=os.environ["JARVIS_VOSK_MODEL"])
    except Exception:
        print(json.dumps({"failure": "model"}))
        return
    warnings: list[str] = []
    import sounddevice  # type: ignore[import-untyped]

    try:
        devices = sounddevice.query_devices()
        if not any(d["max_input_channels"] for d in devices):
            warnings.append(
                "No microphone found. Connect one; test only with a visible hold gesture."
            )
        if not any(d["max_output_channels"] for d in devices):
            warnings.append("No speaker found. Connect one and perform the explicit voice check.")
    except Exception:
        warnings.append(
            "Audio device discovery failed. Text mode is available; check audio settings."
        )
    import pyttsx3  # type: ignore[import-untyped]

    try:
        engine = pyttsx3.init(driverName="sapi5")
        try:
            if not any(
                "ru" in str(v.languages).lower()
                or "russian" in v.id.lower()
                or "ru-ru" in v.id.lower()
                for v in engine.getProperty("voices")
            ):
                warnings.append(
                    "Russian SAPI voice missing. Windows Settings > Time & language > Speech > "
                    "Manage voices: add Russian, accept any OS prompt, then rerun setup."
                )
        finally:
            engine.stop()
    except Exception:
        warnings.append(
            "SAPI voice discovery failed. Check Windows speech settings; text mode works."
        )
    print(
        json.dumps(
            {
                "dependencies": "passed",
                "offline_chromium": browser_version,
                "local_vosk_load": "passed",
                "warnings": warnings,
                "packages": {d.metadata["Name"]: d.version for d in distributions()},
            }
        )
    )


if __name__ == "__main__":
    main()
