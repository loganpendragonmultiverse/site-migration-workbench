# Original synthetic migration walkthrough

This is an offline planning sample. It contains no client information and no fabricated network observations.

For the built-in first-run example, run the app, choose **Start guided example**, and follow the notice. Import the source CSVs in this directory if you want to inspect import previews yourself.

The completed source example has five source URLs:

| Source | Decision | Expected |
| --- | --- | --- |
| /about | Keep | Direct 200 |
| /old-service | Redirect | 301 to /service |
| /merge-alpha | Merge | 301 to /bundle |
| /merge-beta | Merge | 301 to /bundle |
| /obsolete | Retire | Direct 410 |

Restore `examples/completed-migration-backup.json` into a new database through the UI or CLI. Open it and inspect the reviewed rows, provenance and frozen plan. Its JSON and both deployment adapters are included beside it. They are synthetic examples for inspection, not production configuration to install blindly.

The example.com/staging.example.com addresses are not deployed test fixtures. Do not expect live verification to pass there. The executable end-to-end fixture under tests/full_ui.mjs starts its own loopback HTTP server, creates/imports a project using the real UI, grants only that fixture's exact private IP, approves and downloads the plan, checks staging, changes the HTTP behavior, checks regression and catches noindex in production.

For your own migration, import actual operator-supplied inventories, review every disposition, declare the actual environment origins and only then verify. The primary production origin substitutes to staging; additional approved origins are contacted as declared and do not receive an implicit host substitution. Use a separately reviewed project/plan when a more complex domain move needs different staging substitutions.

A report's denominator includes every frozen source row. Excluded rows, uncertain rows, and approved exceptions must be reviewed separately. The app does not establish semantic equivalence, traffic recovery or indexing.

Two unfamiliar people have not yet completed this documentation unaided. Automated walkthrough evidence is in evidence/full-ui.json; it must not be presented as human usability acceptance.
