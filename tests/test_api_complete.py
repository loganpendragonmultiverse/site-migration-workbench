import base64
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import test_workflow
from fixture_server import Fixture


class CompleteAPI(unittest.TestCase):
    setUp = test_workflow.Server.setUp
    tearDown = test_workflow.Server.tearDown
    call = test_workflow.Server.call

    def test_decision_export_run_handoff_recovery(self):
        fixture = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        threading.Thread(target=fixture.serve_forever, daemon=True).start()
        origin = "http://127.0.0.1:" + str(fixture.server_port)
        try:
            status, body = self.call(
                "/api/configure",
                {
                    "project": self.pid,
                    "config": {
                        "production": origin,
                        "staging": origin,
                        "private_allowlist": ["127.0.0.1"],
                        "staging_noindex": True,
                    },
                },
            )
            self.assertEqual(status, 200)
            raw = ("url,title\n" + origin + "/about,About\n" + origin + "/obsolete,Old\n").encode()
            data = {
                "project": self.pid,
                "content": base64.b64encode(raw).decode(),
                "kind": "csv",
                "source": "Original fixture",
                "side": "old",
                "job": "api-test",
            }
            self.assertEqual(self.call("/api/preview", data)[0], 200)
            malformed = dict(
                data,
                content=base64.b64encode(
                    ("url,title\n" + origin + "/about," + "x" * 140000).encode()
                ).decode(),
            )
            self.assertEqual(self.call("/api/preview", malformed)[0], 400)
            self.assertEqual(self.call("/api/import", data)[0], 200)
            generation = self.store.project(self.pid)["generation"]
            changes = []
            for row in self.store.inventory(self.pid):
                patch = {
                    "disposition": "keep" if row["url"].endswith("about") else "retire",
                    "status": 410,
                    "rationale": "Reviewed fixture",
                    "reviewed": True,
                }
                changes.append({"id": row["id"], "patch": patch})
            self.assertEqual(
                self.call("/api/edit", {"project": self.pid, "generation": generation, "changes": changes})[
                    0
                ],
                200,
            )
            status, body = self.call("/api/freeze", {"project": self.pid})
            revision = json.loads(body)["id"]
            for kind in ("json", "csv", "nginx", "wordpress"):
                self.assertEqual(
                    self.call("/api/export?revision=" + str(revision) + "&format=" + kind)[0], 200
                )
            self.assertEqual(
                self.call("/api/suggestions?project=" + str(self.pid) + "&id=" + str(changes[0]["id"]))[0],
                200,
            )
            status, body = self.call("/api/run", {"revision": revision, "environment": "staging"})
            run = json.loads(body)["id"]
            limit = time.monotonic() + 10
            while self.store.query("SELECT state FROM runs WHERE id=?", (run,))[0]["state"] == "running":
                self.assertLess(time.monotonic(), limit)
                time.sleep(0.02)
            report = json.loads(self.call("/api/report?id=" + str(run))[1])
            self.assertEqual(report["counts"]["pass"], 2)
            for fmt in ("json", "csv", "html"):
                self.assertEqual(
                    self.call("/api/handoff?run=" + str(run) + "&format=" + fmt + "&sharing=true")[0], 200
                )
            self.assertEqual(self.call("/api/regression?before=" + str(run) + "&after=" + str(run))[0], 200)
            self.assertEqual(self.call("/api/resume", {"run": run})[0], 400)
            self.assertEqual(self.call("/api/archive", {"project": self.pid, "archived": True})[0], 200)
            self.assertEqual(self.call("/api/undo", {"project": self.pid})[0], 200)
            backup = json.loads(self.call("/api/backup")[1])
            status, body = self.call("/api/restore", backup)
            self.assertEqual(status, 200)
            self.assertTrue(Path(json.loads(body)["path"]).is_file())
            self.assertEqual(self.call("/api/cancel-import", {"job": "not-running"})[0], 200)
        finally:
            fixture.shutdown()
            fixture.server_close()
