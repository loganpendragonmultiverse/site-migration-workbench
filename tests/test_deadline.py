import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from smw.network import request


class Deadline(unittest.TestCase):
    def test_partial_close_delimited_body_is_not_success_after_deadline(self):
        class Slow(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<title>Partial</title>")
                self.wfile.flush()
                time.sleep(0.35)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        target = "http://127.0.0.1:" + str(server.server_port)
        timer = threading.Timer
        try:
            with patch(
                "smw.network.threading.Timer", side_effect=lambda interval, callback: timer(0.1, callback)
            ):
                with self.assertRaisesRegex(OSError, "deadline"):
                    request(target, {target}, ["127.0.0.1"])
        finally:
            server.shutdown()
            server.server_close()
