# Data contracts

CSV and generic crawler imports have reviewed column names for `url` (required), `title`, `canonical` and `priority` (integer 0–100). Optional columns default to empty/zero. URL lists contain one absolute URL per nonempty line. XML/gzip use sitemap urlset or index namespaces; remote indexes are bounded and same-origin.

Every inventory occurrence keeps original spelling, source name, source row, UTC import time and SHA-256 of source bytes. URL identity preserves path/slash/query/encoding distinctions. Side is old or new. Each side is bounded to 10,000 unique URLs; each import is at most 10 MiB expanded and 10,000 rows.

A decision has disposition unresolved/keep/redirect/merge/retire, optional target, expected HTTP status, rationale, reviewed boolean, exclusion boolean, priority and a visible exception note. Each edit increments the project's generation. Unknown fields and stale generations are rejected.

A schema-1 frozen plan contains project name/configuration, generation, all source rows and provenance, and assumptions. Its hash is SHA-256 over deterministic UTF-8 JSON (sorted keys, compact separators, no NaN). Approval revision ID and hash bind verification and report outputs to the exact plan.

Run observations are immutable once written. A paused/interrupted run resumes only missing rows. Retry creates a new run with a selected ID set and keeps all other rows explicitly not checked; it never merges unrelated runs into a green total. Compare runs only for identical revision hash and environment.

Report states are pass/fail/warning/not checked/inconclusive/excluded. An approved exception is separate from result. Counts include the complete plan denominator and exclusions. Observation timestamps, run window, environment and limitations are included.

Backup schema 1 is JSON with `database` (base64 SQLite) and `sha256`. Restore validates this envelope and creates a new file only. Backups are private full-fidelity workspace copies; shared reports remove query values, notes, titles, headers, settings and potentially sensitive freeform error evidence. Path components are retained; review reports before sending if paths themselves are private.
