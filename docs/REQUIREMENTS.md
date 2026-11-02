# Approved scope and requirement-to-test checklist

The project targets an intentional disposition for each known source URL, frozen approvals, supported export, expected-vs-observed verification and honest handoff. The detailed acceptance list comes from the approved product specification, not an official Forge certification standard.

| Requirement | Implementation | Evidence / remaining acceptance |
| --- | --- | --- |
| Create/open/archive workspace, guided example | UI + Store | API tests and browser acceptance |
| Search/filter, pagination, previewed bulk edit, undo, autosave | UI + transactional Store | Store tests; browser flow |
| Portable backup/restore | SQLite backup and exclusive new-file restore | Backup, corruption and unexpected schema tests |
| Inventory URL/CSV/crawler/XML/gzip, provenance, duplicates | domain + Store | Parser/provenance/expansion tests |
| Bounded nested sitemap fetch | same-origin index fetch, child fingerprints | Nested same-scope fetch/provenance test and transactional cancellation test pass |
| Conservative URL identity | explicit documented policy | Case/slash/encoding/query tests |
| Keep/redirect/merge/retire/unresolved, reasons and priority | Store/UI | Conflict/approval tests |
| Explainable suggestions, no automatic approval | path/title proposals | Suggestion test + browser |
| Loops/chains and accidental many-to-one | preapproval validation | Conflict tests |
| Versioned frozen scope/hash and invalidation | immutable revision rows | Freeze/stale/undo tests |
| Complete JSON/CSV and strict WordPress/Nginx export | adapter module | Unit blockers and real Nginx/WordPress import/request/roundtrip tests pass |
| Declared-set staging/production capture | pinned HTTP + job runner | Synthetic transport and local socket tests |
| Status/chain/canonical/noindex/blocking/downgrade | evidence comparison | Correctness tests, local live corpus and 1,200-source comparison pass |
| Pass/fail/warning/not checked/inconclusive | separate states | Uncertainty/denominator tests |
| Pause/cancel/resume, backoff, retries, history | Runner | Pause/cancel/resume, durable single-worker queue, selected retry and SIGKILL tests pass |
| Remediation/regression and handoff | UI + reports | Denominator/redaction/regression tests |
| Safe rendering, origin/CSRF, network/file budgets | server/parser/transport | Hostile input and pinned DNS tests |
| 10,000 URLs responsive and bounded | paginated UI + budgets | 10,000-row offline and Chromium filtering/pagination benchmarks pass |
| Install/build/license/CI | package and repository files | Local package install/full UI and audit pass; remote CI is blocked |
| Keyboard/accessibility and optional human validation | native controls, modal focus; automated keyboard/axe smoke | Automated workflow and five-screen checks pass; external screen-reader/users not claimed |
| Representative comparison | actual LinkWitness capture + independent CSV versus Workbench, 1,200 sources and two runs | Identical detected faults and regression; external human/commercial crawler UI not claimed |
| Canonical source/release/catalog/registry | Forge workflow | Blocked before repository creation |

This checklist must remain honest when evidence changes. No essential feature or test is declared complete solely because its function or button exists.
