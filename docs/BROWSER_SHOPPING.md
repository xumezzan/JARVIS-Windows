# Dynamic browser tasks

Live and Workspace modes use the same observed-element workflow for public shops and
other sites. No shop-specific URLs, selectors or shopping recipes are embedded in the
executor. Strict Sandbox retains its existing isolated, non-JavaScript behavior.

- Live DOM observations include radio buttons, checkboxes, native selects and their
  options. Native options are selected through their observed owning select, with the
  same exact approval and network policy as other JavaScript controls.
- CSS classes and inline styles do not contribute to Live element fingerprints.
  Names, links, form attributes, values, selection, visible text and focus still do.
  Replaced, renamed or retargeted controls still invalidate the action.
- Reading a known Live document can refresh changed contents. It cannot follow a
  different document, URL or origin using the old target. Mutations still require the
  exact latest page snapshot and observed node.
- A stale-page failure triggers audited SAFE `browser.get_tabs` and `browser.read`
  calls for the affected tab before the next model proposal. These calls consume the
  normal step/time budget and cannot bypass a tightened permission matrix. No click
  is automatically repeated and no approval token is reused.
- Live result verification checks document identity rather than requiring JavaScript
  to stop changing after an observation. A returned page is a point-in-time view,
  never proof that an application-level effect completed. The planner must inspect
  the cart/confirmation and match the requested product options before finishing.
- After a JS click, the browser waits up to 1.5 seconds for a DOM/form change, excluding
  focus alone. Later results can be obtained with another read. CDP contexts use the
  same bounded element/navigation timeouts as launched browsers. Live host operations
  have a 30-second cap so their bounded preview, click and navigation stages fit.

Accounts, credentials, bot checks, unavailable variants and unsupported page structures
remain external limits. Public-site shopping has not been established merely by the
local fixtures; a cart addition never authorizes checkout/payment.

Validation on Windows, 2026-10-29: all eight new real-Chromium regression cases passed
in both Live and Workspace modes. They cover colour/capacity selection, checkbox and
native option selection, delayed cart confirmation, CSS animation changes, stale marks,
document changes, and Runner recovery with exactly one actual cart click.

```powershell
python -m pytest -q tests/integration/test_browser_shopping.py
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m jarvis --smoke-test
python -m build
```
