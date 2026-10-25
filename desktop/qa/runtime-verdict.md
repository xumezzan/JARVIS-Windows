## verdict

1. Documentation synchronization — resolved. `desktop/README.md`, `desktop/DESIGN.md` and `.impeccable/design.json` now describe the connected runtime and native authority. `desktop/PRODUCT.md` points to the PRD, roadmap and user request; Windows and live-service limitations remain explicit. The metadata's stale demo narrative and control labels are removed.
2. Compact session-note containment — resolved. The refreshed `compact-runtime.png` shows the complete two-line note within the session card; `min-height:300px` is present. Desktop and mobile captures remain valid and show no regression from this fix.
3. Long-command scroll reachability — resolved. `.command-context` now uses `justify-content:safe center`; `runtime-verification.json` records a real 4,000-character command with both first and last lines reachable at widths 1536, 1280 and 390. The same four screenshot paths were reopened and validated.

## remaining

Clear for these three scored fixes. This verdict covers the correction list, not Windows acceptance, live accounts, live model calls or microphone hardware.

disposition: ship
