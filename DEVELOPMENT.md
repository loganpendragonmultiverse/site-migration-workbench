# Development contract

The approved target is the bounded Site Migration Workbench workflow in docs/REQUIREMENTS.md. This is a persistent local planning/review/approval/handoff application, not a general crawler or automatic production deployer. One pinned runtime dependency, defusedxml, hardens sitemap parsing; no service accounts, analytics integrations or paid APIs are necessary.

Use Python 3.11+ and SQLite for durable state, a loopback standard-library server, and a small vanilla JavaScript interface. This keeps the offline planning workflow portable and avoids an always-running external service. Native packaging remains unverified.

LinkWitness v1.2.1 was inspected at commit cb370f4a1969fe39d1b6164e70d3f94e66637032. Its HTML metadata parser supplies a small reusable primitive, with attribution in THIRD_PARTY.md. Its urllib transport was not reused because its separate DNS check and transport resolution cannot satisfy this project's pinned-address boundary. The new transport and comparison model preserve explicit evidence provenance and uncertainty.

The public canonical repository is loganpendragonmultiverse/site-migration-workbench. The owner authorized source publication, remote CI, GitHub release and the existing Forge catalog. Preserve protected checks and exact artifact provenance.

## Changes

Run the tests and build in TESTING.md. Add tests for substantive behavior, security boundaries and recovery. Do not infer exporter correctness from string snapshots. Update documentation, capability claims, acceptance evidence and schemas with their implementations. Keep synthetic fixtures original and free of client information.

For release, follow current Forge Context open-source workflows. Verify complete CI/CodeQL, actual artifact identities, tag/release hashes and catalog/information-page/sitemap/registry consistency. Never weaken protected review or claim a human acceptance check passed because a browser script ran.

No essential acceptance item is represented as a promised future roadmap. Missing checks and missing scope remain explicit blockers in docs/ACCEPTANCE.md.
