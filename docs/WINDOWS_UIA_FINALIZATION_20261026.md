# Windows UIA and acceptance finalization — 2026-10-26

Verified on the owner's Windows 11 Pro build 26200, Python 3.13.15.

## Deep native UIA observations

The shared maximum/default depth is now 16 in both public/private request schemas,
the native tree walk, click re-observation and active-window marked screenshots.
Unnamed Pane/Group wrappers are traversed without querying their names or discarding
empty container rectangles: Electron places visible controls inside those wrappers.
The existing 2048 raw-node traversal limit, 96 returned-element limit, 67,000-byte
metadata budget, password/visibility/enabled filters and killable worker remain.

Read-only production-worker checks used separate real application windows and temporary
data, not mocked adapters or the owner's documents:

| Live application | Depth 5 | Depth 16 | Metadata / worker time at depth 16 |
| --- | --- | --- | --- |
| Jarvis Electron with the real Python backend READY | 0 elements | 20: 18 buttons, 1 edit, 1 document | 5735 bytes / 0.837 s |
| Google Chrome, owned local acceptance page | 3 buttons | 15: 11 buttons, 2 edits, 1 tab, 1 document | 4339 bytes / 0.693 s |

The real Electron screenshot returned 20 marks in a 47,667-byte JPEG (902 × 600),
within the unchanged 49,152-byte image budget. Native checks do not establish
Teams acceptance, mixed-DPI behavior or every third-party UIA provider.

Regressions cover actions beneath 13 unnamed containers, invisible/password control
exclusion, request depth validation, the cyclic-provider node cap and the Unicode byte
budget on a deep tree. No screen text or observation is approval authority.

## Missing symlink privilege

Only fixture creation in the three previously failing tests catches OSError with
`getattr(error, "winerror", None) == 1314` and skips with:

`Requires SeCreateSymbolicLinkPrivilege on Windows`

Other fixture errors are re-raised. Production symlink/hardlink rejection is unchanged;
Developer Mode, OS privileges and permissions are unchanged.

## Local verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
node --test desktop/electron/*.test.cjs
```

Set PLAYWRIGHT_BROWSERS_PATH to the existing slot's **physical** browser directory;
the previous update documented why virtualized paths break Chromium assembly loading.

- Full pytest: **1864 passed, 14 skipped, 0 failed**, 296.48 s.
- Focused native/filesystem/mail/SoM regressions: **140 passed, 4 skipped**, 2.66 s.
- Ruff lint and format: passed (424 files).
- Mypy: passed (343 source files).
- Electron/HUD unit tests: **16 passed**.

The first hosted run exposed an unrelated five-second polling deadline in the
conversation-consent test while its real initial system-info task was still running.
Like the adjacent long-task regression, this test now awaits the actual worker within
the production task budget and asserts it finished before checking consent. Consent
assertions and production timeouts are unchanged; this is not a performance test.

Live voice, cloud model, Outlook and native E2E remain separate opt-in checks; skipped
tests are not passed tests. Hosted Windows CI and the matching installation/bootstrap
reports must be checked independently before declaring the published installation ready.
