"""Sitemap provenance and cancellation tests against original scoped endpoints."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import test_workflow


class SitemapAPI(unittest.TestCase):
    setUp = test_workflow.Server.setUp
    tearDown = test_workflow.Server.tearDown
    call = test_workflow.Server.call

    def test_nested_sitemap_same_scope_provenance_and_budget(self):
        class Sitemap(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                prefix = "http://127.0.0.1:" + str(self.server.server_port)
                if self.path == "/index.xml":
                    body = (
                        "<sitemapindex><sitemap><loc>" + prefix + "/child.xml</loc></sitemap></sitemapindex>"
                    )
                elif self.path == "/outside.xml":
                    body = "<sitemapindex><sitemap><loc>https://outside.invalid/child.xml</loc></sitemap></sitemapindex>"
                else:
                    body = (
                        "<urlset><url><loc>"
                        + prefix
                        + "/about</loc></url><url><loc>"
                        + prefix
                        + "/service</loc></url></urlset>"
                    )
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body.encode())

        fixture = ThreadingHTTPServer(("127.0.0.1", 0), Sitemap)
        threading.Thread(target=fixture.serve_forever, daemon=True).start()
        prefix = "http://127.0.0.1:" + str(fixture.server_port)
        try:
            self.call(
                "/api/configure",
                {"project": self.pid, "config": {"production": prefix, "private_allowlist": ["127.0.0.1"]}},
            )
            status, body = self.call(
                "/api/fetch",
                {"project": self.pid, "url": prefix + "/index.xml", "side": "old", "job": "sitemap-test"},
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(body)["imported"], 2)
            for row in self.store.inventory(self.pid):
                self.assertEqual(row["provenance"][0]["source"], prefix + "/child.xml")
                self.assertEqual(len(row["provenance"][0]["fingerprint"]), 64)
            status, body = self.call(
                "/api/fetch",
                {"project": self.pid, "url": prefix + "/outside.xml", "side": "old", "job": "sitemap-test"},
            )
            self.assertEqual(status, 400)
            self.assertIn("same-origin", json.loads(body)["error"])
            self.assertEqual(len(self.store.inventory(self.pid)), 2)
        finally:
            fixture.shutdown()
            fixture.server_close()
