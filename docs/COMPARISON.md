# Representative synthetic workflow comparison

We ran an original 1,200-source migration against an actual loopback HTTP fixture twice. The first run deliberately included correct keeps, redirects, merges and retirements, wrong or missing redirects, a temporary response, a chain, a loop, a dead target, an incorrect retirement, authentication failure, truncation, a canonical conflict and a missing canonical. The second run changed one previously correct redirect to a temporary redirect.

The baseline used the existing LinkWitness v1.2.1 capture implementation from the inspected released source, with two batches to respect its 1,000-URL limit. An independent CSV join read an externally prepared expected map, retained its input hash, required a complete capture denominator and classified actual behavior. This is a real capture-plus-CSV comparison. LinkWitness is not a general crawler, and no commercial crawler or spreadsheet GUI was hands-on benchmarked.

Workbench imported the same original truth, preserved inventory fingerprints, froze a reviewed plan and produced two durable runs and a built-in regression comparison. The fixture used a test-only zero-delay setting; normal application rate limits remain in place.

| Observation | Capture + independent CSV | Workbench |
| --- | --- | --- |
| First run denominator | 1,200 | 1,200 |
| First run result | 1,187 pass, 10 fail, 2 inconclusive, 1 warning | Identical |
| Second run result | 1,186 pass, 11 fail, 2 inconclusive, 1 warning | Identical |
| New regression | One identified | Same one identified |
| Redirect/retirement decisions | 800 independently derived rule decisions | All 800 match the WordPress export's documented fields |
| Bound to supplied map | CSV input hash stored by the custom join | Frozen revision ID and content hash built into run/report records |

Both configured approaches found the seeded faults. There is no evidence here that crawlers cannot do migration QA or that the workbench has superior detection.

The baseline required an expected CSV, capture batches, an explicit join/classification step, a separate regression step and a separate deployment-rule derivation. A careful existing script can automate and preserve all of these. Workbench supplies those relationships inside the saved project: dispositions and reasons, review checks, immutable approvals, supported export blockers, queued verification, remediation, reruns and handoff. It also adds a local UI and database that the operator must learn and back up.

The demonstrated reason for a separate product is this coherent persistent job, rather than a new HTTP checker. The real WordPress/Nginx adapter tests and browser workflow tests are separate evidence for those parts; this comparison does not pretend they were an external human pilot.

No client data, external testers, outreach, usage or willingness-to-pay evidence was involved. No human minutes saved or errors avoided in client work are claimed. The raw original CSV/captures/reports and exact summary are retained under evidence/comparison/ and evidence/comparison.json.
