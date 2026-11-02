# Architecture and failure model

- domain.py: identity, bounded parsing, deterministic data and explainable proposals.
- store.py: serialized transactions, provenance, edit undo, immutable revisions and portable backup.
- network.py: approved-origin scope, address policy, connection pinning, TLS hostname validation, bounded responses, metadata and expected/observed comparison.
- jobs.py: durable run queue and missing-row resume; two requests/second.
- exporters.py: conservative adapters that reject the whole unsupported export.
- reports.py: deterministic evidence rendering and explicit redacted sharing.
- app.py: CLI and loopback server; per-process random session token, Host/Origin checks, same-site rejection, restrictive CSP.
- app.js: accessible native controls and a paginated decision table.

SQLite commits are transactional; incomplete edits/import transactions roll back. Startup marks formerly running jobs interrupted. Existing observations and approved plan hashes are preserved. Edits invalidate working approval without mutating previous plans/runs. Backups use SQLite's backup API and rollback-journal files for portability, avoiding the invalid in-memory WAL serialization reproduced by tests.

No network credentials are accepted or stored. No browser cookies or Authorization header are sent. TLS stays verified. DNS results are checked as a set, then the socket connects directly to one checked address without re-resolving. DNS operations have bounded concurrency and wait; late resolver threads are daemonized. Redirects re-enter the same origin/address policy. Native response reads have an absolute per-request shutdown deadline as well as socket timeouts.

The current user interface loads project records once and renders at most 100 mapping rows per page. Reports present up to 100 remediation details at a time; downloadable reports retain all rows.

An observer can only prove what it received. Missing canonical metadata is a warning. Blocking/auth/rate-limited/truncated/failed responses are inconclusive. Noindex accepted in staging is checked again against production policy. Content equivalence and soft-404 detection are not claimed.
