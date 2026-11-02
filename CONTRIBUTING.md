# Contributing

Read DEVELOPMENT.md, TESTING.md and docs/SCHEMAS.md first. Use original synthetic fixtures, explain the operator problem and behavioral change, and add meaningful tests. Do not include client inventories, tokens, cookies or staging credentials.

Changes must preserve approval hashes, uncertainty states, safe defaults and all known source rows. Unsupported exporter behavior must fail before emitting a misleading configuration. Submit a pull request with exact validation results and known limits. Maintainer review and required CI apply; no automated approval or direct production deployment.
