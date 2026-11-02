import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from fixture_server import Fixture

from smw.domain import parse_inventory
from smw.store import Store


class CLI(unittest.TestCase):
    def test_installed_contract_backup_restore_report_lock_and_verify(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "workspace.sqlite"
            store = Store(database)
            fixture = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
            threading.Thread(target=fixture.serve_forever, daemon=True).start()
            origin = "http://127.0.0.1:" + str(fixture.server_port)
            try:
                pid = store.create("CLI fixture", {"production": origin, "private_allowlist": ["127.0.0.1"]})
                store.import_rows(
                    pid,
                    "old",
                    parse_inventory((origin + "/obsolete").encode()),
                    "Original CLI fixture",
                    "synthetic",
                )
                row = store.inventory(pid)[0]
                store.edit(
                    pid,
                    [
                        {
                            "id": row["id"],
                            "patch": {
                                "disposition": "retire",
                                "status": 410,
                                "reviewed": True,
                                "rationale": "Original CLI decision",
                            },
                        }
                    ],
                    1,
                )
                rid = store.freeze(pid)["id"]

                def command(*args):
                    return subprocess.run(
                        [sys.executable, "-m", "smw.app", "--database", str(database), *args],
                        text=True,
                        capture_output=True,
                        timeout=15,
                    )

                verified = command("verify", str(rid), "--environment", "production")
                self.assertEqual(verified.returncode, 0, verified.stderr)
                self.assertEqual(json.loads(verified.stdout)["counts"]["pass"], 1)
                self.assertEqual(command("backup", str(root / "backup.json")).returncode, 0)
                restored = command("restore", str(root / "backup.json"), str(root / "restored.sqlite"))
                self.assertEqual(restored.returncode, 0, restored.stderr)
                self.assertEqual(
                    Store(root / "restored.sqlite").revision(rid)["hash"], store.revision(rid)["hash"]
                )
                run = store.query("SELECT id FROM runs")[0]["id"]
                handoff = command(
                    "report",
                    str(run),
                    "--format",
                    "html",
                    "--sharing",
                    "--output",
                    str(root / "handoff.html"),
                )
                self.assertEqual(handoff.returncode, 0, handoff.stderr)
                self.assertIn("Migration evidence handoff", (root / "handoff.html").read_text())
                service = subprocess.Popen(
                    [sys.executable, "-m", "smw.app", "--database", str(database), "serve", "--no-browser"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    self.assertTrue(service.stdout.readline().startswith("http://127.0.0.1:"))
                    blocked = command("backup", str(root / "blocked.json"))
                    self.assertEqual(blocked.returncode, 2)
                    self.assertIn("another process", blocked.stderr)
                    self.assertFalse((root / "blocked.json").exists())
                finally:
                    service.terminate()
                    service.wait(timeout=5)
                    service.stdout.close()
                    service.stderr.close()
            finally:
                fixture.shutdown()
                fixture.server_close()
