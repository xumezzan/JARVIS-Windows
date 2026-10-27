# Open a website in the owner's Chrome

The Electron command bar accepts exact local navigation recipes, including
`открой сайт apple.com` and `открой браузер и перейди на сайт apple.com`.
A bare domain becomes a canonical HTTPS URL. This single, literal command does not
need an OpenAI request or cloud consent. Other commands retain their existing
planner and consent rules; historical context cannot select a URL for this recipe.

Owner decision, 2026-10-26: opening any website in the owner's Chrome is automatic,
without a confirmation or Jarvis sandbox restrictions. `windows.open_chrome_url`
is SAFE. HTTP and HTTPS, localhost, private IP addresses and intranet hosts are
supported. No allowlist, site tier, public-address requirement or DNS preflight
is applied to this URL handoff. Chrome handles networking and its browser policies.

`открой сайт instagram` (also `инстаграм`) resolves to
`https://www.instagram.com/`. Other hostnames are used literally, including
single-label intranet names; the recipe never guesses a `.com` suffix. Bare hosts
default to HTTPS; an explicit HTTP URL stays HTTP. Unicode domain names, paths,
queries, fragments and non-default ports are normalized before the audited action
snapshot. Only HTTP/HTTPS web URLs are passed to Chrome, never file/script URLs,
executables, profile paths or additional command switches. Embedded credentials
are not accepted into the action/history.

The killable Windows helper hands the canonical URL to the installed Chrome
executable with the fixed `--new-tab` switch and `shell=False`. Existing windows
are not closed or restarted, and cookies are not read or copied. Chrome selects
its existing profile and owns navigation after this handoff. PermissionEngine
still validates the snapshot, prevents replay, observes cancellation and writes
a durable metadata-only audit. Simulation never invokes a native adapter, and a
SAFE navigation cannot be given an approval token.

This is an external-browser launch receipt, not a verified page-load or DOM
observation. The factual report says so. Redirects and later
requests in the owner's browser are managed by Chrome, without Jarvis request
interception. Strict Sandbox and its allowlist remain in force for `browser.*`;
this launch does not provide those tools access to the owner's tabs/accounts.
No remote-debugging
port or separate Chrome profile is required for this limited operation.

Network policy rejections during tool preparation now retain `network_denied`
and DENIED, with the registered tool name in the payload-free audit, rather than
becoming `invalid_request`. Malformed input still yields INVALID.

Validation commands:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_chrome_command.py tests/unit/test_browser.py tests/unit/test_windows.py tests/integration/test_desktop_service.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
npm --prefix desktop run build
node --test desktop/electron/*.test.cjs
```

Live installation and Chrome checks are recorded after running these gates.
