import gzip
import hashlib
import http.client
import json
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from smw.app import create_server
from smw.domain import fingerprint, parse_inventory, safe_cell, suggest, url
from smw.exporters import deployment, export_plan
from smw.jobs import Runner
from smw.network import addresses, capture, compare, request
from smw.reports import render
from smw.store import Store


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbpath = Path(self.tmp.name) / "workspace.sqlite"
        self.store = Store(self.dbpath)
        self.pid = self.store.create(
            "Synthetic migration",
            {"production": "https://example.com", "staging": "https://staging.example.com"},
        )

    def tearDown(self):
        self.tmp.cleanup()

    def inventory(self, values=None):
        raw = (
            "\n".join(
                values or ["https://example.com/old", "https://example.com/keep", "https://example.com/gone"]
            )
        ).encode()
        rows = parse_inventory(raw)
        self.store.import_rows(self.pid, "old", rows, "synthetic", hashlib.sha256(raw).hexdigest())
        return self.store.inventory(self.pid)

    def approve(self):
        rows = self.inventory()
        changes = []
        for row in rows:
            if row["url"].endswith("/old"):
                p = {"disposition": "redirect", "target": "https://example.com/new", "status": 301}
            elif row["url"].endswith("/keep"):
                p = {"disposition": "keep"}
            else:
                p = {"disposition": "retire", "status": 410}
            p.update(reviewed=True, rationale="Fixture decision")
            changes.append({"id": row["id"], "patch": p})
        self.store.edit(self.pid, changes, self.store.project(self.pid)["generation"])
        return self.store.freeze(self.pid)

    def test_url_identity_and_rejection(self):
        self.assertEqual(url("https://EXAMPLE.com:443/A?x=1&y=2"), "https://example.com/A?x=1&y=2")
        for a, b in [("/A", "/a"), ("/A", "/A/"), ("/%41", "/A"), ("/?a=1&b=2", "/?b=2&a=1")]:
            self.assertNotEqual(url("https://example.com" + a), url("https://example.com" + b))
        for bad in [
            "file:///etc/passwd",
            "ftp://example.com",
            "https://u:p@example.com",
            "https://example.com/#secret",
            "https://example.com/%q",
            "https://example.com/\r\n",
            "https://example.com\\evil",
        ]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                url(bad)

    def test_import_formats_and_expansion(self):
        rows = parse_inventory(
            b"Address,Title,Traffic\nhttps://example.com/a,A,50",
            "csv",
            {"url": "Address", "title": "Title", "priority": "Traffic"},
        )
        self.assertEqual(rows[0]["row"], 2)
        self.assertEqual(rows[0]["priority"], 50)
        xml = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/a</loc></url></urlset>'
        self.assertEqual(parse_inventory(xml, "xml")[0]["url"], "https://example.com/a")
        self.assertEqual(parse_inventory(gzip.compress(xml), "gzip")[0]["url"], "https://example.com/a")
        with self.assertRaises(ValueError):
            parse_inventory(b'<!DOCTYPE a [<!ENTITY xx "bad">]><urlset/>', "xml")
        with self.assertRaises(ValueError):
            parse_inventory(gzip.compress(b"x" * (10 * 1024 * 1024 + 1)), "gzip")

    def test_duplicates_provenance_and_limit_transaction(self):
        self.inventory(["https://example.com/a", "https://EXAMPLE.com/a"])
        row = self.store.inventory(self.pid)[0]
        self.assertEqual(len(row["provenance"]), 2)
        self.assertEqual(row["provenance"][1]["original"], "https://EXAMPLE.com/a")
        self.assertEqual(self.store.project(self.pid)["generation"], 1)
        with self.assertRaises(ValueError):
            self.store.import_rows(self.pid, "old", [{"url": "file:///bad", "row": 1}], "bad", "x")
        self.assertEqual(len(self.store.inventory(self.pid)), 1)

    def test_decisions_freeze_undo_stale_generation(self):
        frozen = self.approve()
        revision = self.store.revision(frozen["id"])
        self.assertEqual(
            next(r for r in revision["plan"]["rows"] if r["disposition"] == "keep")["status"], 200
        )
        self.assertEqual(fingerprint(revision["plan"]), frozen["hash"])
        generation = self.store.project(self.pid)["generation"]
        row = self.store.inventory(self.pid)[0]
        self.store.edit(self.pid, [{"id": row["id"], "patch": {"rationale": "Changed"}}], generation)
        self.assertEqual(self.store.inventory(self.pid)[0]["reviewed"], 0)
        with self.assertRaises(ValueError):
            self.store.freeze(self.pid)
        with self.assertRaises(ValueError):
            self.store.edit(self.pid, [{"id": row["id"], "patch": {"rationale": "Stale"}}], generation)
        self.store.undo(self.pid)
        self.assertEqual(self.store.inventory(self.pid)[0]["rationale"], "Fixture decision")
        self.assertEqual(self.store.revision(frozen["id"])["hash"], frozen["hash"])

    def test_conflicts_loop_and_many_to_one(self):
        rows = self.inventory(["https://example.com/a", "https://example.com/b"])
        self.store.edit(
            self.pid,
            [
                {
                    "id": rows[0]["id"],
                    "patch": {
                        "disposition": "redirect",
                        "target": rows[1]["url"],
                        "rationale": "x",
                        "reviewed": True,
                    },
                },
                {
                    "id": rows[1]["id"],
                    "patch": {
                        "disposition": "redirect",
                        "target": rows[0]["url"],
                        "rationale": "x",
                        "reviewed": True,
                    },
                },
            ],
            1,
        )
        self.assertTrue(any("loop" in x["message"].lower() for x in self.store.issues(self.pid)))
        self.store.edit(
            self.pid,
            [{"id": r["id"], "patch": {"target": "https://example.com/new", "reviewed": True}} for r in rows],
            2,
        )
        self.assertTrue(any("Many-to-one" in x["message"] for x in self.store.issues(self.pid)))
        self.store.edit(
            self.pid, [{"id": r["id"], "patch": {"disposition": "merge", "reviewed": True}} for r in rows], 3
        )
        self.assertEqual(self.store.issues(self.pid), [])

    def test_exclusion_remains_in_plan_and_needs_reason(self):
        row = self.inventory(["https://example.com/excluded"])[0]
        self.store.edit(self.pid, [{"id": row["id"], "patch": {"excluded": True}}], 1)
        with self.assertRaises(ValueError):
            self.store.freeze(self.pid)
        self.store.edit(self.pid, [{"id": row["id"], "patch": {"rationale": "Outside supplied scope"}}], 2)
        r = self.store.freeze(self.pid)
        self.assertEqual(len(self.store.revision(r["id"])["plan"]["rows"]), 1)

    def test_backup_restore_and_corruption(self):
        self.approve()
        envelope = self.store.backup()
        target = Path(self.tmp.name) / "restored.sqlite"
        Store.restore(envelope, target)
        restored = Store(target)
        self.assertEqual(restored.inventory(self.pid), self.store.inventory(self.pid))
        with self.assertRaises(FileExistsError):
            Store.restore(envelope, target)
        broken = dict(envelope, sha256="0" * 64)
        with self.assertRaises(ValueError):
            Store.restore(broken, Path(self.tmp.name) / "bad.sqlite")

    def test_backup_rejects_executable_schema(self):
        self.approve()
        with self.store.connection() as db:
            db.execute("CREATE VIEW hostile AS SELECT * FROM projects")
        envelope = self.store.backup()
        with self.assertRaises(ValueError):
            Store.restore(envelope, Path(self.tmp.name) / "hostile.sqlite")

    def test_export_capability_blocks_without_drops(self):
        f = self.approve()
        r = self.store.revision(f["id"])
        nginx, _ = deployment(r, "nginx")
        self.assertIn("return 410;", nginx)
        wp, _ = deployment(r, "wordpress")
        self.assertEqual(len(json.loads(wp)["redirects"]), 2)
        self.assertIn("Fixture decision", export_plan(r, "json")[0])
        invalid = json.loads(json.dumps(r))
        invalid["plan"]["rows"][0]["status"] = "410; include unsafe.conf"
        for adapter in ("nginx", "wordpress"):
            with self.assertRaises(ValueError):
                deployment(invalid, adapter)
        encoded_keep = json.loads(json.dumps(r))
        encoded_keep["plan"]["rows"].append(
            {**r["plan"]["rows"][0], "url": "https://example.com/%6fld", "disposition": "keep", "status": 200}
        )
        with self.assertRaises(ValueError):
            deployment(encoded_keep, "wordpress")
        self.assertIn("$request_uri", deployment(encoded_keep, "nginx")[0])
        for raw in [
            "https://example.com/a?x=1",
            "https://example.com/%61",
            "https://example.com/é",
            "https://example.com/a/../b",
        ]:
            clone = json.loads(json.dumps(r))
            clone["plan"]["rows"][0].update(url=raw, disposition="retire", status=410)
            for adapter in ("nginx", "wordpress"):
                with self.assertRaises(ValueError):
                    deployment(clone, adapter)

    def test_suggestion_is_explainable_and_not_approval(self):
        suggestions = suggest(
            {"url": "https://example.com/a", "title": "A"},
            [
                {"url": "https://example.com/a", "title": "A"},
                {"url": "https://example.com/z", "title": "Other"},
            ],
        )
        self.assertEqual(suggestions[0]["score"], 1)
        self.assertIn("review", suggestions[0]["reason"])

    def test_csv_formula_defense(self):
        self.assertEqual(safe_cell("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(safe_cell(" @evil"), "' @evil")
        self.assertEqual(safe_cell("normal"), "normal")

    def test_report_full_denominator_redaction_and_regression(self):
        frozen = self.approve()
        with self.store.connection() as db:
            run = db.execute(
                "INSERT INTO runs(revision,environment,state,created,config) VALUES(?,'production','completed','2026-01-01',?)",
                (frozen["id"], '{"ids":[]}'),
            ).lastrowid
        runner = Runner(self.store)
        report = runner.report(run)
        self.assertEqual(report["counts"]["not checked"], 3)
        self.assertEqual(report["denominator"], 3)
        report["rows"][0].update(
            url="https://example.com/a?token=SECRET",
            rationale="PRIVATE",
            exception="CONFIDENTIAL",
            title="<script>bad</script>",
            error="SECRET",
        )
        shared, _ = render(report, "json", True)
        for secret in ["SECRET", "PRIVATE", "CONFIDENTIAL", "<script>"]:
            self.assertNotIn(secret, shared)
        private, _ = render(report, "html", False)
        self.assertNotIn("<script>bad</script>", private)
        self.assertEqual(runner.regression(run, run)[0]["change"], "still unverified")

    def test_restart_marks_running_interrupted(self):
        frozen = self.approve()
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO runs(revision,environment,state,created,config) VALUES(?,'production','running','x','{}')",
                (frozen["id"],),
            )
        restarted = Store(self.dbpath)
        self.assertEqual(restarted.query("SELECT state FROM runs")[0]["state"], "interrupted")


class Network(unittest.TestCase):
    config = {
        "production": "https://example.com",
        "staging": "https://staging.example.com",
        "private_allowlist": [],
        "staging_noindex": True,
    }

    def row(self, disposition="redirect"):
        return {
            "url": "https://example.com/old",
            "target": "https://example.com/new",
            "disposition": disposition,
            "status": 301,
            "exception": "",
        }

    def transport(self, table):
        def get(target, *args):
            response = table[target]
            return (
                response[0],
                response[1],
                response[2] if len(response) > 2 else b"<title>Fixture</title>",
                False,
            )

        return get

    def test_correct_wrong_temporary_and_dead(self):
        for status, final, expected in [
            (301, "/new", "warning"),
            (308, "/new", "fail"),
            (302, "/new", "fail"),
            (301, "/wrong", "fail"),
        ]:
            with self.subTest(status=status, final=final):
                evidence = capture(
                    "https://example.com/old",
                    self.config,
                    transport=self.transport(
                        {
                            "https://example.com/old": (status, {"location": final}),
                            "https://example.com" + final: (200, {"content-type": "text/html"}),
                        }
                    ),
                )
                self.assertEqual(compare(self.row(), evidence, self.config, "production")["result"], expected)
        evidence = capture("https://example.com/old", self.config, transport=lambda *a: (404, {}, b"", False))
        self.assertEqual(compare(self.row(), evidence, self.config, "production")["result"], "fail")

    def test_keep_retire_canonical_and_robots(self):
        row = self.row("keep")
        row["target"] = ""
        html = b'<link rel="alternate canonical" href="/old"><meta name="robots" content="noindex">'
        evidence = capture(
            row["url"], self.config, transport=lambda *a: (200, {"content-type": "text/html"}, html, False)
        )
        self.assertEqual(compare(row, evidence, self.config, "production")["result"], "fail")
        evidence["final_url"] = "https://staging.example.com/old"
        evidence["chain"][0]["url"] = evidence["final_url"]
        evidence["canonicals"] = ["https://staging.example.com/old"]
        self.assertEqual(compare(row, evidence, self.config, "staging")["result"], "pass")
        row.update(disposition="retire", status=410)
        gone = capture(row["url"], self.config, transport=lambda *a: (410, {}, b"", False))
        self.assertEqual(compare(row, gone, self.config, "production")["result"], "pass")

    def test_redirect_loop_cross_origin_and_hop_limit(self):
        loop = capture(
            "https://example.com/a", self.config, transport=lambda *a: (301, {"location": "/a"}, b"", False)
        )
        self.assertIn("loop", loop["error"])
        calls = []

        def outside(target, *args):
            calls.append(target)
            return 301, {"location": "https://127.0.0.1/secret"}, b"", False

        blocked = capture("https://example.com/a", self.config, transport=outside)
        self.assertIn("scope", blocked["error"])
        self.assertEqual(len(calls), 1)

        def endless(target, *args):
            return 301, {"location": target + "a"}, b"", False

        bounded = capture("https://example.com/a", self.config, transport=endless)
        self.assertEqual(len(bounded["chain"]), 11)
        self.assertIn("limit", bounded["error"])

    def test_uncertainty_never_passes(self):
        for status in [401, 403, 429, 503]:
            with patch("smw.network.time.sleep"):
                e = capture(
                    "https://example.com/old", self.config, transport=lambda *a: (status, {}, b"", False)
                )
            self.assertEqual(compare(self.row(), e, self.config, "production")["result"], "inconclusive")
        e = capture("https://example.com/old", self.config, transport=lambda *a: (200, {}, b"x", True))
        self.assertEqual(compare(self.row(), e, self.config, "production")["result"], "inconclusive")
        e = capture("https://example.com/old", self.config, stop=lambda: True)
        self.assertIn("interrupted", e["error"])

    def test_login_metadata_budget_and_downgrade(self):
        for body in (b'<input type="password">', b"<title>" + b"x" * 9000 + b"</title>"):
            evidence = capture(
                "https://example.com/old",
                self.config,
                transport=lambda *args: (200, {"content-type": "text/html"}, body, False),
            )
            self.assertEqual(
                compare(self.row(), evidence, self.config, "production")["result"], "inconclusive"
            )
        evidence = capture(
            "https://example.com/old",
            self.config,
            transport=lambda *args: (301, {"location": "http://example.com/new"}, b"", False),
        )
        self.assertEqual(compare(self.row(), evidence, self.config, "production")["result"], "fail")
        self.assertIn("HTTPS downgrade", evidence["error"])

    def test_explicit_additional_site_scope(self):
        table = {
            "https://example.com/old": (301, {"location": "https://new.example.com/new"}),
            "https://new.example.com/new": (
                200,
                {"content-type": "text/html"},
                b'<link rel="canonical" href="/new">',
            ),
        }
        evidence = capture("https://example.com/old", self.config, transport=self.transport(table))
        self.assertIn("scope", evidence["error"])
        config = dict(self.config, additional_origins=["https://new.example.com"])
        evidence = capture("https://example.com/old", config, transport=self.transport(table))
        row = self.row()
        row["target"] = "https://new.example.com/new"
        self.assertEqual(compare(row, evidence, config, "production")["result"], "pass")

    def test_mixed_dns_and_exact_allowlist(self):
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        with patch("smw.network.socket.getaddrinfo", return_value=[public, private]):
            with self.assertRaises(ValueError):
                addresses("example.com", 443, [])
            self.assertEqual(len(addresses("example.com", 443, ["127.0.0.1"])), 2)

    def test_transport_connection_is_pinned_and_no_auth(self):
        received = []

        class Fixture(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(dict(self.headers))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port
        try:
            with patch(
                "smw.network.addresses",
                return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, ("127.0.0.1", port))],
            ) as dns:
                status, headers, body, truncated = request(
                    "http://rebind.example:" + str(port) + "/", {"http://rebind.example:" + str(port)}
                )
                self.assertEqual(status, 200)
                self.assertEqual(body, b"ok")
                self.assertEqual(dns.call_count, 1)
                self.assertNotIn("Authorization", received[0])
        finally:
            server.shutdown()
            server.server_close()


class Server(unittest.TestCase):
    def setUp(self):
        Workspace.setUp(self)
        self.server = create_server(self.store)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = "127.0.0.1:" + str(self.server.server_port)
        c = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        c.request("GET", "/")
        html = c.getresponse().read().decode()
        c.close()
        self.token = html.split('name="workbench-token" content="')[1].split('"')[0]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        Workspace.tearDown(self)

    def call(self, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        h = {"X-Workbench-Token": self.token, "Content-Type": "application/json"}
        h.update(headers or {})
        c.request(
            "POST" if body is not None else "GET", path, json.dumps(body) if body is not None else None, h
        )
        r = c.getresponse()
        result = r.status, r.read()
        c.close()
        return result

    def test_origin_host_and_csrf(self):
        for headers in [
            {"Origin": "https://evil.example"},
            {"Host": "evil.example"},
            {"X-Workbench-Token": ""},
            {"Sec-Fetch-Site": "cross-site"},
        ]:
            with self.subTest(headers=headers):
                status, body = self.call(
                    "/api/create", {"name": "bad", "config": {"production": "https://example.com"}}, headers
                )
                self.assertEqual(status, 400)
        self.assertEqual(len(self.store.query("SELECT * FROM projects")), 1)

    def test_ui_workflow_api(self):
        status, body = self.call("/api/example", {})
        self.assertEqual(status, 200)
        pid = json.loads(body)["id"]
        status, body = self.call("/api/project?id=" + str(pid))
        self.assertEqual(json.loads(body)["project"]["name"], "Guided example")
        self.assertEqual(self.call("/api/backup")[0], 200)
        self.assertEqual(self.call("/api/not-real")[0], 400)


if __name__ == "__main__":
    unittest.main()
