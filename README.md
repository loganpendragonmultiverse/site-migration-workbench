# Site Migration Workbench

A local workspace for deciding where old website URLs belong, freezing a reviewed migration plan, exporting supported redirect rules, and checking a declared URL set against that exact revision.

**Unreleased candidate 0.1.0.** The source is implemented and undergoing acceptance. It is not a Gold release. See [acceptance status](docs/ACCEPTANCE.md) for implemented scope, measured evidence, and remaining checks.

## Run locally

Python 3.11 or newer is required. Linux container execution is the current tested environment; Windows and macOS packaging and native behavior are not yet advertised as supported.

```sh
python -m pip install .
migration-workbench --database migration.sqlite serve
```

Open the printed loopback URL if the browser does not open. The service binds only to 127.0.0.1 on an automatically allocated port. Do not expose it through a reverse proxy or port-forward it to other machines. Local users who can read your database can read its contents.

Choose **Start guided example** to learn the workflow with original synthetic data. No account, paid API, telemetry, or upload service is required. Verification sends requests to configured sites; planning and reviewing stored reports are offline.

## Complete a migration record

1. Create a project and declare production and staging origins.
2. Import the old URL inventory and proposed new URLs. Preview CSV columns, URL lists or sitemap XML/gzip before importing. Multiple occurrences retain source names, row numbers, original spelling, timestamps and fingerprints.
3. Review each source URL. Keep it, redirect it, merge it intentionally into another destination, retire it with 404/410, or leave it unresolved. Give excluded rows a reason. Use priority and rationale to make review easier.
4. Select rows for a bulk edit, preview the exact changes and apply. Undo restores the last edit. Similarity suggestions are explained and never approved automatically.
5. Fix unresolved decisions, accidental many-to-one destinations and redirect chains/loops. Freeze the reviewed revision. Its hash includes the inventory evidence and environment settings; later edits require a new approval.
6. Export full JSON/CSV or a supported deployment adapter. Review and deploy the configuration through your hosting workflow. This app never writes production configuration.
7. Verify staging, then production. Inspect failures, warnings and inconclusive or unchecked rows. Exceptions stay visible and never turn into technical passes.
8. Download HTML/CSV/JSON handoff evidence, using redacted sharing mode when appropriate. Rerun against the same revision to find regressions.

A freeze records the operator's decision, not a digital signature or client consent.

## Boundaries that matter

URL identity lowercases the scheme/host and removes default ports. It preserves path case, trailing slash, percent spelling and query order. Fragments and URL credentials are rejected. The tool does not assume that similarly named pages mean the same thing.

The current deployment adapters deliberately handle a conservative subset. Query-dependent, encoded, Unicode and ambiguous paths block adapter export, with an instruction to implement those rows manually. Full plan exports retain them and verification still accepts them. See the [capability matrix](docs/EXPORTS.md).

Network checks pin connections to validated DNS addresses, reject private/reserved addresses by default, and validate every redirect's site scope. Additional old/new site origins can be explicitly approved in project settings; they are contacted as declared. Only the primary production origin substitutes to staging. Explicit private staging access requires a narrow per-project exact-IP allowlist. Requests do not use environment proxies. Authentication, cookies, JavaScript rendering and login journeys are unsupported. A 401/403 or unavailable response is inconclusive.

Responses are bounded to 1 MiB with bounded redirects, DNS capacity and request deadlines. Evidence contains observed chains, status, title, canonical and page/header noindex directives. robots.txt is explicitly not evaluated. A pass describes observed technical behavior for the supplied URL at that moment; it does not guarantee completeness, rankings, traffic, indexing, semantic equivalence or absence of soft 404s.

## Backups and CLI

```sh
migration-workbench --database migration.sqlite backup portable-backup.json
migration-workbench restore portable-backup.json restored.sqlite
migration-workbench --database migration.sqlite verify 1 --environment staging
migration-workbench --database migration.sqlite report 1 --format html --sharing --output handoff.html
```

Backup and report outputs use exclusive creation. Restore creates a new database rather than overwriting the current one, validates the checksum and SQLite integrity, rejects unexpected schema objects and verifies frozen plan hashes. The UI accepts backup envelopes up to 20 MiB; use the CLI for larger backups. Keep backups private: they contain the full workspace.

The [operator walkthrough](docs/OPERATOR-WALKTHROUGH.md) includes a completed original sample backup and plan. See [development](DEVELOPMENT.md), [testing](TESTING.md), [security](SECURITY.md), [schemas](docs/SCHEMAS.md), and [architecture](docs/ARCHITECTURE.md).
