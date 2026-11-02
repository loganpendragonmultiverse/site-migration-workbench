# Candidate acceptance status

**Implemented, tested local candidate 0.1.0. Not released and not Gold.**

Forge's Gold Star means Logan considers the project complete for its purpose and is happy with its state, with fixes and good new ideas welcome. It is not a promise of zero bugs or every possible feature. The [owner's definition](https://www.loganpendragonforge.com/open-source/gold-star/) is separate from the product-specific acceptance evidence below.

## Measured evidence

| Check | Actual result |
| --- | --- |
| Core workflow | Chromium and Firefox guided review/export/backup/reload/undo pass; complete Chromium import → review → freeze → four export downloads → staging → redacted handoff → HTTP regression → production noindex test passes |
| Unit/integration | Final per-interpreter logs are evidence/tests-python311.txt, tests-python312.txt and tests-python314.txt; Linux containers only |
| Real adapters | Nginx 1.28 and WordPress 7.1.4 + Redirection 5.10.1 import/request tests pass. Legacy WordPress 6.8.3 + Redirection 5.5.2 also passed. WordPress native export/delete/reimport preserves the observed semantics |
| Durability | Real SIGKILL during import, edit, verification and artifact write preserves approved hash and inventory; interrupted verification resumes missing rows |
| Ceiling | 10,000 synthetic source URLs: import 0.93 s, load 0.19 s, Python filter 0.002 s, approximately 45 MiB peak RSS in the first benchmark; later Chromium scale runs load/render in roughly 1.2–1.4 s and filter in 106–151 ms, with 100 visible rows |
| Security and privacy | Host/Origin/token controls, hostile schemes, private/mixed DNS, connection pinning, redirect scope, XML/gzip budgets, literal UI/report rendering, formula protection, redaction, interrupted responses, login detection and metadata limits have executable coverage |
| Packaging | Wheel and source distribution build; isolated wheel installation and the full installed-package UI flow pass. Final digests and source manifest are generated after the last build |
| Keyboard/accessibility | Automated Tab/Enter/Space/arrow-key review and freeze, modal focus, Escape and five-screen axe checks pass; no serious/critical findings. External human/screen-reader use is not claimed |
| Representative comparison | Actual 1,200-source LinkWitness + independent CSV and Workbench runs find identical faults and the same subsequent regression; 800 rule decisions agree. No commercial-crawler/UI/human-time claim |
| Dependency review | The only application runtime dependency is defusedxml 0.7.1 for hardened XML parsing. Installed test environment audit has no known vulnerabilities after updating its pip installer. The unpublished application is not independently indexed by the advisory service |

Tests are original synthetic fixtures. Browser automation and model inspection are not human operator or screen-reader acceptance. The Python coverage report is an honest partial measure; browser/CLI subprocess flows are not all included in that instrumentation.

## Remaining publication gates and validation limits

- External human keyboard-only and screen-reader use has not occurred. Automated keyboard and axe evidence is recorded separately; this is a disclosure, not a fabricated official Gold gate.
- Two unfamiliar external people have not completed the fixture migration. The proposed pilot is optional validation, not an official Gold requirement.
- The 1,200-source synthetic comparison with actual LinkWitness captures and an independently joined CSV has been completed. Both approaches found identical seeded faults and the next-run regression. This is an automated capture/CSV comparison, not a benchmark of a commercial crawler UI, external users, adoption, or human time savings.
- Native Windows/macOS packaging and clean-machine upgrade/rollback have not occurred and are not advertised. This candidate's process lock requires POSIX.
- Remote CI/CodeQL, exact tag-built packages and Forge catalog verification are recorded in the release evidence when completed. Local candidate evidence alone does not prove publication.
