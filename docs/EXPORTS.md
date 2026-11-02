# Deployment adapters

All rows remain in the plan JSON and CSV. Adapter errors reject the entire adapter file; no unsupported row is silently discarded. Exclusions and keep rows intentionally produce no redirect rule and remain in the plan.

| Capability | Nginx | WordPress Redirection |
| --- | --- | --- |
| Source site | Configured production origin | Configured production origin |
| Match | Exact conservative ASCII path | Exact conservative ASCII path |
| Redirect status | 301, 308 | 301, 308 |
| Retirement | 404, 410 | 410; explicit 404 is blocked |
| Query-bearing source | Blocked; implement request-URI semantics manually | Blocked; implement query semantics manually |
| Encoded / Unicode path | Blocked | Blocked |
| Path case/trailing slash | Exact | Case-sensitive, slash-sensitive flags |
| Multiple rows per destination | Explicit reviewed merge | Explicit reviewed merge |
| Regex, catch-all rules | Unsupported | Unsupported |
| Automatic deployment | Never | Never |

Nginx configuration belongs inside the production server block. Its query guard returns 400 for query-bearing requests to exported locations; this prevents accidentally treating a new query request as a reviewed query-free mapping. Review its implications for tracking parameters before deployment.

The WordPress adapter uses Redirection's JSON import structure with a dedicated group and explicit source query/case/trailing flags. Actual installation/import/request and native export/delete/reimport tests passed with WordPress 7.1.4 + Redirection 5.10.1 and the separate legacy fixture. See evidence/export-servers.json and docs/TEST-ENVIRONMENTS.md. The embedded schema version alone is not proof of compatibility.

Targets with syntax the conservative exporter cannot safely represent are blocked. Use the complete plan and implement those rules manually, then verify them through the workbench. Verification does not require an adapter export.

Nginx also guards the raw request URI, so encoded aliases, repeated slashes and dot segments cannot silently match a reviewed ASCII source. Redirection and its web server can normalize such aliases; WordPress export therefore blocks plans declaring encoded or Unicode source-path distinctions, including keep rows. Full plan export and verification retain those rows for manual implementation. The exact observed alias behavior is recorded in the real-server evidence.
