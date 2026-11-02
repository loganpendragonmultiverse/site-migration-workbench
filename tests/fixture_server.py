"""Original local-only correctness fixture, never deployed publicly."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

fault = False


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        global fault
        path = urlsplit(self.path).path
        if path == "/set-fault":
            fault = True
            status, headers, body = 200, {}, b"enabled"
        elif path == "/clear-fault":
            fault = False
            status, headers, body = 200, {}, b"disabled"
        elif path in ("/old-service", "/permanent308", "/temporary", "/chain"):
            status = 302 if path == "/temporary" or fault else 308 if path == "/permanent308" else 301
            headers = {"Location": "/old-service" if path == "/chain" else "/service"}
            body = b""
        elif path in ("/about", "/service", "/noindex"):
            status, headers = 200, {"Content-Type": "text/html", "X-Robots-Tag": "noindex"}
            body = ('<title>Original fixture</title><link rel="canonical" href="' + path + '">').encode()
        elif path == "/obsolete":
            status, headers, body = 410, {}, b"retired"
        elif path == "/loop":
            status, headers, body = 301, {"Location": "/loop"}, b""
        elif path == "/auth":
            status, headers, body = 401, {}, b"authentication unsupported"
        elif path == "/rate":
            status, headers, body = 429, {"Retry-After": "0"}, b"rate limited"
        elif path == "/cut":
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"short")
            self.close_connection = True
            return
        else:
            status, headers, body = 404, {}, b"missing"
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    print("http://127.0.0.1:" + str(server.server_port), flush=True)
    server.serve_forever()
