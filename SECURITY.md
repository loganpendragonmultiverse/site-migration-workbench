# Security

Do not put client secrets into issues or shared fixtures. Report a security issue privately to the repository owner through GitHub private vulnerability reporting when the public repository exists; until then contact the owner through the established private channel.

This app binds only to 127.0.0.1. It rejects unexpected Host/Origin and cross-site requests and requires a random session token for data operations. Imported text is rendered literally, and reports escape HTML and neutralize spreadsheet formulas. Backups contain private full-fidelity data and must stay private.

Only declared HTTP(S) targets on configured origins are contacted. Private/reserved addresses are denied unless an exact project allowlist explicitly permits them. Every connection is pinned to a validated DNS address, including redirect hops. No proxy, cookie, URL credential or authorization support is included. This app cannot verify authenticated staging pages.

Limits apply to inputs, gzip expansion, XML, sitemap recursion, URLs, responses, DNS capacity and redirects. Adapter output is conservative and never installed by the app. Production deployments and their rollback remain the operator's job.

Sharing mode removes query values, private notes, settings, headers, titles and freeform error evidence. It retains URL paths; inspect shared artifacts if path names themselves are confidential. No telemetry or upload endpoint exists.

No formal external penetration test or human accessibility/security review is claimed.
