"""Synthetic 1,200-row comparison: existing LinkWitness captures + CSV join vs Workbench."""

import csv
import hashlib
import json
import re
import tempfile
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from linkwitness import __version__ as baseline_version
from linkwitness.core import analyze

from smw.domain import canonical_json, parse_inventory
from smw.exporters import deployment
from smw.jobs import Runner
from smw.store import Store

assert baseline_version == "1.2.1", baseline_version

faults = {10, 30, 40, 50, 70, 80, 410, 420, 430, 440, 810, 1010, 1100}


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        kind, value = path.strip("/").split("/", 1)
        n = int(value)
        status = 200
        headers = {"Content-Type": "text/html"}
        canonical = path
        body = None
        if kind in ("old", "merge"):
            status = 301
            headers["Location"] = "/new/" + value if kind == "old" else "/bundle/" + str(n % 20)
            body = b""
            if n in faults:
                if n in (410, 415):
                    status = 302
                elif n == 420:
                    status, body = 200, None
                    headers.pop("Location")
                elif n == 430:
                    headers["Location"] = "/wrong/" + value
                elif n == 440:
                    headers["Location"] = "/bridge/" + value
                elif n == 810:
                    headers["Location"] = "/dead/" + value
        elif kind == "bridge":
            status, headers["Location"], body = 301, "/new/" + value, b""
        elif kind == "gone":
            status, body = 410, b"retired"
            if n == 1010:
                status, body = 200, b"not retired"
            elif n == 1100:
                status = 404
        elif kind == "dead":
            status, body = 500, b"dead target"
        if kind == "keep" and n in faults:
            if n == 10:
                status = 404
            elif n == 30:
                status = 401
            elif n == 50:
                status, headers["Location"], body = 302, path, b""
            elif n == 70:
                canonical = "/wrong/70"
            elif n == 80:
                canonical = ""
        if body is None:
            body = (
                "<title>Original fixture</title>"
                + ('<link rel="canonical" href="' + canonical + '">' if canonical else "")
            ).encode()
            if kind == "keep" and n == 40:
                body += b"x" * (1024 * 1024 + 20)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def baseline_result(row, observed):
    error = observed.get("error", "")
    if error:
        if "redirect" in error.lower() and any(x in error.lower() for x in ("limit", "loop", "infinite")):
            return "fail"
        code = re.search(r"HTTP Error (\d{3})", error)
        status = int(code.group(1)) if code else None
        if status in (401, 403, 429, 503) or status is None:
            return "inconclusive"
        return "pass" if row["disposition"] == "retire" and status == row["status"] else "fail"
    if observed.get("truncated"):
        return "inconclusive"
    status = observed["status"]
    if row["disposition"] == "retire":
        return "pass" if status == row["status"] and not observed["redirects"] else "fail"
    expected = row["target"] or row["url"]
    if status != 200 or observed["final_url"] != expected:
        return "fail"
    chain = observed["redirects"]
    if row["disposition"] == "keep" and chain:
        return "fail"
    if row["disposition"] in ("redirect", "merge") and (
        len(chain) != 1 or chain[0]["status"] != row["status"]
    ):
        return "fail"
    if observed.get("canonical_url") and observed["canonical_url"] != expected:
        return "fail"
    return "pass" if observed.get("canonical_url") else "warning"


server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = "http://127.0.0.1:" + str(server.server_port)
out = Path("evidence/comparison")
out.mkdir(parents=True, exist_ok=True)
try:
    rows = []
    for n in range(1200):
        kind = "keep" if n < 400 else "old" if n < 800 else "merge" if n < 1000 else "gone"
        disposition = (
            "keep"
            if kind == "keep"
            else "redirect"
            if kind == "old"
            else "merge"
            if kind == "merge"
            else "retire"
        )
        target = (
            origin + "/new/" + str(n)
            if kind == "old"
            else origin + "/bundle/" + str(n % 20)
            if kind == "merge"
            else ""
        )
        rows.append(
            {
                "url": origin + "/" + kind + "/" + str(n),
                "disposition": disposition,
                "target": target,
                "status": 410 if kind == "gone" else 200 if kind == "keep" else 301,
            }
        )
    fields = ["url", "disposition", "target", "status"]
    with (out / "expected.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fields)
        writer.writeheader()
        writer.writerows(rows)
    expected_hash = hashlib.sha256((out / "expected.csv").read_bytes()).hexdigest()
    # Read the spreadsheet interchange file back; baseline inputs do not come from Workbench exports.
    with (out / "expected.csv").open() as file:
        baseline_rows = [{**r, "status": int(r["status"])} for r in csv.DictReader(file)]
    with tempfile.TemporaryDirectory() as temporary:
        store = Store(Path(temporary) / "workbench.sqlite")
        pid = store.create(
            "1,200 original synthetic sources", {"production": origin, "private_allowlist": ["127.0.0.1"]}
        )
        raw = "\n".join(r["url"] for r in rows).encode()
        store.import_rows(
            pid,
            "old",
            parse_inventory(raw),
            "Original synthetic 1,200 URL list",
            hashlib.sha256(raw).hexdigest(),
        )
        supplied_targets = sorted({r["target"] or r["url"] for r in rows if r["disposition"] != "retire"})
        target_raw = "\n".join(supplied_targets).encode()
        store.import_rows(
            pid,
            "new",
            parse_inventory(target_raw),
            "Original synthetic replacement list",
            hashlib.sha256(target_raw).hexdigest(),
        )
        by_url = {r["url"]: r for r in rows}
        store.edit(
            pid,
            [
                {
                    "id": r["id"],
                    "patch": {
                        **{k: by_url[r["url"]][k] for k in ("disposition", "target", "status")},
                        "reviewed": True,
                        "rationale": "Known original synthetic test truth",
                    },
                }
                for r in store.inventory(pid)
            ],
            2,
        )
        revision = store.freeze(pid)
        runner = Runner(store)
        runner.interval = 0  # Local fixture acceleration, not a production speed or rate claim.
        observations = []
        timings = []
        for iteration in (1, 2):
            if iteration == 2:
                faults.add(415)
            started = time.perf_counter()
            captures = []
            for batch, offset in enumerate(range(0, len(rows), 1000), 1):
                captured = analyze(
                    {
                        "urls": [r["url"] for r in baseline_rows[offset : offset + 1000]],
                        "allow_private": True,
                        "max_bytes": 1024 * 1024,
                    }
                )
                (out / ("capture-" + str(iteration) + "-" + str(batch) + ".json")).write_text(
                    canonical_json(captured)
                )
                captures.extend(captured["captures"])
            indexed = {r["requested_url"]: r for r in captures}
            assert set(indexed) == {r["url"] for r in rows}
            baseline = {r["url"]: baseline_result(r, indexed[r["url"]]) for r in baseline_rows}
            with (out / ("joined-" + str(iteration) + ".csv")).open("w", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["url", "result", "expected_input_hash"])
                writer.writerows((url, result, expected_hash) for url, result in baseline.items())
            baseline_seconds = time.perf_counter() - started
            started = time.perf_counter()
            run = runner.create(revision["id"], "production")
            deadline = time.monotonic() + 120
            while run in runner.active:
                assert time.monotonic() < deadline
                time.sleep(0.02)
            report = runner.report(run)
            actual = {r["url"]: r["result"] for r in report["rows"]}
            assert actual == baseline, [
                (u, actual[u], baseline[u]) for u in actual if actual[u] != baseline[u]
            ][:20]
            assert report["denominator"] == 1200
            observations.append((run, baseline))
            timings.append(
                {
                    "iteration": iteration,
                    "capture_csv_counts": dict(Counter(baseline.values())),
                    "workbench_counts": report["counts"],
                    "capture_and_join_seconds": baseline_seconds,
                    "workbench_accelerated_seconds": time.perf_counter() - started,
                }
            )
            (out / ("workbench-run-" + str(iteration) + ".json")).write_text(canonical_json(report))
        regression = runner.regression(observations[0][0], observations[1][0])
        newly = [r for r in regression if r["change"] == "newly failing"]
        assert len(newly) == 1 and newly[0]["url"].endswith("/old/415"), newly
        baseline_new = [
            u
            for u, state in observations[1][1].items()
            if state == "fail" and observations[0][1][u] != "fail"
        ]
        assert baseline_new == [origin + "/old/415"]
        (out / "regression.csv").write_text("url,change\n" + baseline_new[0] + ",newly failing\n")
        rules = json.loads(deployment(store.revision(revision["id"]), "wordpress")[0])["redirects"]
        expected_rules = {
            urlsplit(r["url"]).path: (r["status"], r["target"])
            for r in baseline_rows
            if r["disposition"] != "keep"
        }
        exported_rules = {r["url"]: (r["action_code"], r["action_data"]) for r in rules}
        assert expected_rules == exported_rules
        summary = {
            "sources": 1200,
            "first_seeded_cases": sorted(faults - {415}),
            "additional_regression": 415,
            "both_detected_same_results": True,
            "baseline": "Existing LinkWitness v1.2.1 redirect/canonical capture, explicit two-batch capture, independent CSV expected/actual join and regression",
            "baseline_is_general_crawler": False,
            "csv_rule_decisions_equal_wordpress_export": len(rules),
            "frozen_plan_hash": revision["hash"],
            "baseline_expected_csv_hash": expected_hash,
            "newly_failing": len(newly),
            "timings": timings,
            "timing_limit": "Loopback synthetic test only. Workbench delay disabled for repeatability; these are not default production timings or human time savings.",
            "external_humans": 0,
            "outreach": False,
            "fair_conclusion": "Both configured approaches found the seeded faults. Workbench's demonstrated advantage is persisted dispositions, approval/rule validation, frozen plan binding and built-in rerun/remediation/handoff; an equally careful custom spreadsheet workflow can reproduce the checks.",
        }
        Path("evidence/comparison.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
finally:
    server.shutdown()
    server.server_close()
