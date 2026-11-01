# Desktop runtime dependency check — 1 November 2026

The clean desktop install exposed two high-severity dependency findings. The lockfile
now resolves Electron **44.10.1** and its downloader's Undici **7.30.1**, within the
existing package ranges. No new dependency or exact package constraint was added.

Official advisories describe the affected and patched releases:

- [Electron certificate mitigation](https://github.com/electron/electron/security/advisories/GHSA-jr5w-w6x2-84p9).
- [Undici TLS verification correction](https://github.com/nodejs/undici/security/advisories/GHSA-2m5v-pch6-74hx).

The previous lock also contained 194 records under the former temporary development
directory `../../private/tmp/jarvis-ui-runtime-20261015/node_modules/`. Those records
were not the registry packages selected by `npm ci`: they included a second, older
Electron, Undici and Vite. The package manifest has no dependencies on that directory.
Removing these stale records makes the audit describe the actual dependency tree.
All other registry records and package constraints are unchanged.

## Verification on Apple Silicon / macOS 27.2

```sh
npm --prefix desktop ci
npm --prefix desktop audit --audit-level=high
npm --prefix desktop run build
node --test desktop/electron/*.test.cjs
git diff --check
```

The clean install, production build and diff check passed; the audit returned
**0 vulnerabilities** and all **21 Node tests passed**. The first Node run exposed
an existing Windows DPI test that used the host platform: on macOS it expected
Windows pixel conversion despite receiving logical points. The test now selects
Windows explicitly and restores the original property after the test, including
on failure. Its original coordinate and invalid-target assertions remain intact;
production overlay behavior is unchanged.

This change is based directly on main `5792446586ced17fa9b0ea19f27745c3fc9da564`.
The separate native macOS implementation remains in PR #71. A local acceptance
checkout of that PR with the updated dependency lock also passed its 23 Node tests.
These results do not establish Windows hardware, live accounts or microphone
acceptance. Python and hosted Windows gates are tracked with the pull request.
