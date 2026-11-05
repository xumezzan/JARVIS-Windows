# End-to-end tests

`test_windows_acceptance.py` contains opt-in Milestone 3 acceptance on real Windows:
Notepad open/focus/literal text/read-back, plus Chrome and VS Code open/focus.
Names are explicit acceptance-test inputs in an editable field; production has no app list.
Tests require `--run-windows` and `QT_QPA_PLATFORM=windows`.
They never close or discard user documents. Save/close existing Notepad windows first;
restored nonempty documents are rejected. Opened apps and test text remain after tests.

On Windows PowerShell, after preparing this repository's runtime:

```powershell
$env:QT_QPA_PLATFORM = "windows"
.\.venv\Scripts\python.exe -m pytest -q --run-windows tests/e2e/test_windows_acceptance.py
```

These tests are skipped on macOS/Linux; native Windows acceptance remains pending.
They do not implement the full MVP scenario. See `docs/WINDOWS_ACCEPTANCE.md` for the
clean-machine installer, failure matrix and full guided scenario. `test_offline_scenario.py`
checks explicitly scripted browser actions through the real policy in simulation;
it does not interpret user phrases, launch Windows or demonstrate live search results.
