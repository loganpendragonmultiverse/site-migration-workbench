import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fixture_server import Fixture

from smw.domain import parse_inventory, write_new_atomic
from smw.jobs import Runner
from smw.network import capture, compare
from smw.store import Store


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "jobs.sqlite")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.origin = "http://127.0.0.1:" + str(self.server.server_port)
        self.config = {
            "production": self.origin,
            "staging": self.origin,
            "private_allowlist": ["127.0.0.1"],
            "staging_noindex": True,
        }
        self.pid = self.store.create("Original network fixture", self.config)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def plan(self):
        paths = [
            "/old-service",
            "/about",
            "/obsolete",
            "/loop",
            "/auth",
            "/rate",
            "/cut",
            "/temporary",
            "/chain",
            "/missing",
        ]
        self.store.import_rows(
            self.pid,
            "old",
            parse_inventory(("\n".join(self.origin + p for p in paths)).encode()),
            "Original network fixture",
            "synthetic",
        )
        changes = []
        for row in self.store.inventory(self.pid):
            path = row["url"].removeprefix(self.origin)
            p = {
                "disposition": "keep" if path == "/about" else "retire" if path == "/obsolete" else "merge",
                "target": self.origin + "/service",
                "status": 410 if path == "/obsolete" else 301,
                "reviewed": True,
                "rationale": "Known truth",
            }
            changes.append({"id": row["id"], "patch": p})
        self.store.edit(self.pid, changes, 1)
        return self.store.freeze(self.pid)["id"]

    def wait(self, runner, run):
        limit = time.monotonic() + 20
        while self.store.query("SELECT state FROM runs WHERE id=?", (run,))[0]["state"] == "running":
            self.assertLess(time.monotonic(), limit)
            time.sleep(0.02)
        while run in runner.active:
            time.sleep(0.01)
        return runner.report(run)

    def test_real_local_correctness_corpus(self):
        revision = self.plan()
        runner = Runner(self.store)
        run = runner.create(revision, "staging")
        report = self.wait(runner, run)
        actual = {r["url"].removeprefix(self.origin): r["result"] for r in report["rows"]}
        self.assertEqual(
            actual,
            {
                "/about": "pass",
                "/auth": "inconclusive",
                "/chain": "fail",
                "/cut": "inconclusive",
                "/loop": "fail",
                "/missing": "fail",
                "/obsolete": "pass",
                "/old-service": "pass",
                "/rate": "inconclusive",
                "/temporary": "fail",
            },
        )
        self.assertEqual(report["denominator"], 10)
        self.assertEqual(sum(report["counts"].values()), 10)
        with self.assertRaises(ValueError):
            runner.resume(run)
        evidence = capture(self.origin + "/about", self.config)
        row = next(r for r in self.store.inventory(self.pid) if r["url"].endswith("/about"))
        self.assertEqual(compare(row, evidence, self.config, "production")["result"], "fail")

    def test_pause_cancel_resume_and_retry_denominator(self):
        revision = self.plan()
        runner = Runner(self.store)
        run = runner.create(revision, "staging")
        runner.stop(run, "paused")
        self.wait(runner, run)
        partial = runner.report(run)
        self.assertGreater(partial["counts"]["not checked"], 0)
        runner.resume(run)
        completed = self.wait(runner, run)
        self.assertEqual(completed["counts"]["not checked"], 0)
        uncertain = [r["id"] for r in completed["rows"] if r["result"] == "inconclusive"]
        retry = runner.create(revision, "staging", uncertain[:1])
        retried = self.wait(runner, retry)
        self.assertEqual(retried["denominator"], 10)
        self.assertEqual(retried["counts"]["not checked"], 9)
        cancelled = runner.create(revision, "staging")
        runner.stop(cancelled, "cancelled")
        self.wait(runner, cancelled)
        self.assertEqual(runner.report(cancelled)["run"]["state"], "cancelled")

    def test_single_worker_durable_queue(self):
        revision = self.plan()
        runner = Runner(self.store)
        runner.interval = 0
        entered = threading.Event()
        release = threading.Event()
        original = __import__("smw.jobs", fromlist=["capture"]).capture
        calls = []

        def gated(target, config, stop, **kwargs):
            calls.append(target)
            if len(calls) == 1:
                entered.set()
                release.wait(5)
            return original(target, config, stop=stop, **kwargs)

        with patch("smw.jobs.capture", side_effect=gated):
            first = runner.create(revision, "staging")
            self.assertTrue(entered.wait(5))
            second = runner.create(revision, "staging")
            self.assertEqual(
                self.store.query("SELECT state FROM runs WHERE id=?", (second,))[0]["state"], "queued"
            )
            self.assertEqual(len(runner.active), 1)
            release.set()
            self.wait(runner, first)
            limit = time.monotonic() + 15
            while self.store.query("SELECT state FROM runs WHERE id=?", (second,))[0]["state"] != "completed":
                self.assertLess(time.monotonic(), limit)
                time.sleep(0.02)
            self.assertEqual(runner.report(second)["counts"]["not checked"], 0)

    def test_transactional_import_cancel(self):
        rows = parse_inventory(("\n".join(self.origin + "/p" + str(n) for n in range(100))).encode())
        count = 0

        def stop():
            nonlocal count
            count += 1
            return count > 50

        with self.assertRaises(ValueError):
            self.store.import_rows(self.pid, "old", rows, "cancel", "synthetic", stop=stop)
        self.assertEqual(self.store.inventory(self.pid), [])
        self.assertEqual(self.store.project(self.pid)["generation"], 0)

    def test_atomic_output_no_overwrite_or_symlink(self):
        target = Path(self.tmp.name) / "evidence.json"
        write_new_atomic(target, '{"complete":true}')
        with self.assertRaises(FileExistsError):
            write_new_atomic(target, "overwrite")
        self.assertEqual(target.read_text(), '{"complete":true}')
        link = Path(self.tmp.name) / "link"
        link.symlink_to(target)
        with self.assertRaises(ValueError):
            write_new_atomic(link, "bad")
