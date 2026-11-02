# Testing

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
PYTHONPATH=. python tests/benchmark.py
node --check smw/app.js
python -m pip install build
python -m build
```

The unit/integration suite includes conservative URL identity, hostile scheme and XML/gzip inputs, provenance and duplicate imports, approval conflicts, stale-generation rejection, undo, backup corruption and schema rejection, export blockers, canonical/noindex checks, redirect loops/scope/hop bounds, private/mixed DNS policy, pinned local transport, uncertainty, server Host/Origin/token controls, and durable run interruption.

Tests contact only synthetic local HTTP servers or in-memory transports. No client sites or secrets are used.

Browser acceptance uses `tests/ui.mjs` with Playwright 1.64.1 in its matching browser container. It drives real controls, downloads a generated Nginx configuration and backup, edits and reloads state, checks undo and literal hostile text, tests a small viewport and checks browser errors. Automated focus checks are not human keyboard-only or screen-reader testing.

The 10,000-row benchmark uses offline synthetic inventory and reports the actual platform, import/load/filter duration, and peak memory. Network duration is separate and is not extrapolated from it.

Real Nginx/WordPress deployment tests, kill/restart phase coverage, packaged installation and audit results must be recorded in evidence before release. See docs/ACCEPTANCE.md; a missing result is not a pass.

Full repeatable commands, exact fixture versions and observed image hashes are in docs/TEST-ENVIRONMENTS.md. Remote workflow results are available in this repository’s Actions tab; release evidence binds them to exact commits.

`tests/run_history_ui.mjs` uses an original 250-row synthetic SQLite history and real local API/UI. It checks regression direction for three runs, the oldest-run boundary, full remediation paging/search, retained retry selections and the submitted retry IDs.
