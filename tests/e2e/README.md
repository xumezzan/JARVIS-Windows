# End-to-end tests

`test_windows_acceptance.py` contains opt-in Milestone 3 acceptance on real Windows:
Notepad open/focus/confirmed literal text/read-back/nonempty protection, plus Chrome
and VS Code open/focus. Tests require `--run-windows` and `QT_QPA_PLATFORM=windows`.
They never close or discard user documents. Save/close existing Notepad windows first;
restored nonempty documents are rejected. Opened apps and test text remain after tests.

These tests are skipped on macOS/Linux and have not been run on Windows yet.
They do not implement the full MVP scenario. See `docs/WINDOWS_ACCEPTANCE.md` for the
clean-machine installer, failure matrix and full guided scenario. `test_offline_scenario.py`
checks the combined Chrome/search command through the real policy in simulation; it
does not launch Windows or demonstrate live search results.
