"""Loopback-only UI and companion CLI."""

import argparse
import base64
import csv
import hashlib
import json
import os
import secrets
import threading
import webbrowser
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .domain import canonical_json, origin, parse_inventory, suggest, url, write_new_atomic
from .exporters import deployment, export_plan
from .jobs import Runner
from .network import request
from .reports import render
from .store import Store

ASSETS = Path(__file__).parent


def create_server(store, port=0):
    runner = Runner(store)
    token = secrets.token_urlsafe(32)
    imports = {}
    imports_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not log private URLs or notes.

        def send(self, body, kind="application/json", status=200, download=None):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            )
            if download:
                self.send_header("Content-Disposition", 'attachment; filename="' + download + '"')
            self.end_headers()
            self.wfile.write(data)

        def guard(self, api=False):
            expected = "127.0.0.1:" + str(self.server.server_port)
            if self.headers.get("Host") != expected:
                raise ValueError("Host rejected: open the printed loopback URL")
            request_origin = self.headers.get("Origin")
            if request_origin and request_origin != "http://" + expected:
                raise ValueError("Origin rejected")
            if self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
                raise ValueError("Cross-site request rejected")
            if api and not secrets.compare_digest(self.headers.get("X-Workbench-Token", ""), token):
                raise ValueError("Session token rejected")

        def do_GET(self):
            try:
                self.guard()
                parsed = urlsplit(self.path)
                path = parsed.path
                if path == "/":
                    self.send(
                        (ASSETS / "index.html").read_text().replace("SESSION_TOKEN", token), "text/html"
                    )
                elif path in ("/app.js", "/style.css"):
                    self.send(
                        (ASSETS / path[1:]).read_text(),
                        "text/javascript" if path.endswith(".js") else "text/css",
                    )
                elif path.startswith("/api/"):
                    self.guard(api=True)
                    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                    if path == "/api/projects":
                        data = store.query(
                            "SELECT id,name,archived,generation FROM projects ORDER BY created DESC"
                        )
                    elif path == "/api/project":
                        pid = int(q["id"])
                        data = {
                            "project": store.project(pid),
                            "old": store.inventory(pid),
                            "new": store.inventory(pid, "new"),
                            "issues": store.issues(pid),
                            "revisions": store.query(
                                "SELECT id,generation,created,hash FROM revisions WHERE project=? ORDER BY id DESC",
                                (pid,),
                            ),
                            "runs": store.query(
                                "SELECT runs.* FROM runs JOIN revisions ON revisions.id=runs.revision WHERE revisions.project=? ORDER BY runs.id DESC",
                                (pid,),
                            ),
                        }
                    elif path == "/api/report":
                        data = runner.report(int(q["id"]))
                    elif path == "/api/suggestions":
                        rows = store.inventory(int(q["project"]))
                        row = next(r for r in rows if r["id"] == int(q["id"]))
                        data = suggest(row, store.inventory(int(q["project"]), "new"))
                    elif path == "/api/backup":
                        self.send(canonical_json(store.backup()), download="migration-backup.json")
                        return
                    elif path == "/api/export":
                        revision = store.revision(int(q["revision"]))
                        fmt = q["format"]
                        body, kind = (
                            deployment(revision, fmt)
                            if fmt in ("nginx", "wordpress")
                            else export_plan(revision, fmt)
                        )
                        self.send(
                            body,
                            kind,
                            download="migration-plan-"
                            + str(revision["id"])
                            + (
                                "-nginx.conf"
                                if fmt == "nginx"
                                else "." + ("json" if fmt == "wordpress" else fmt)
                            ),
                        )
                        return
                    elif path == "/api/handoff":
                        body, kind = render(
                            runner.report(int(q["run"])), q["format"], q.get("sharing") == "true"
                        )
                        self.send(body, kind, download="migration-handoff." + q["format"])
                        return
                    elif path == "/api/regression":
                        data = runner.regression(int(q["before"]), int(q["after"]))
                    else:
                        raise ValueError("Unknown API")
                    self.send(canonical_json(data))
                else:
                    self.send("Not found", "text/plain", 404)
            except (ValueError, KeyError, StopIteration, TypeError) as exc:
                self.send(canonical_json({"error": str(exc)}), status=400)

        def do_POST(self):
            try:
                self.guard(api=True)
                if self.headers.get("Content-Type") != "application/json":
                    raise ValueError("JSON request required")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 20 * 1024 * 1024:
                    raise ValueError("Request exceeds 20 MiB or is empty")
                data = json.loads(self.rfile.read(length))
                path = urlsplit(self.path).path
                if path == "/api/cancel-import":
                    with imports_lock:
                        event = imports.get(data["job"])
                        if event:
                            event.set()
                    result = {"cancel_requested": bool(event)}
                elif path == "/api/create":
                    result = {"id": store.create(data["name"], data["config"])}
                elif path == "/api/configure":
                    store.configure(data["project"], data["config"])
                    result = {"ok": True}
                elif path == "/api/archive":
                    store.archive(data["project"], data["archived"])
                    result = {"ok": True}
                elif path in ("/api/preview", "/api/import"):
                    event = threading.Event()
                    with imports_lock:
                        imports[data.get("job", "file")] = event
                    raw = base64.b64decode(data["content"], validate=True)
                    rows = parse_inventory(raw, data["kind"], data.get("columns"))
                    if path.endswith("preview"):
                        result = {"rows": rows[:100], "total": len(rows)}
                    else:
                        result = {
                            "imported": store.import_rows(
                                data["project"],
                                data["side"],
                                rows,
                                data["source"],
                                hashlib.sha256(raw).hexdigest(),
                                stop=event.is_set,
                            )
                        }
                elif path == "/api/fetch":
                    event = threading.Event()
                    with imports_lock:
                        imports[data.get("job", "fetch")] = event
                    config = store.project(data["project"])["config"]
                    pending = [url(data["url"])]
                    visited = set()
                    rows = []
                    sources = []
                    total = 0
                    while pending:
                        if event.is_set():
                            raise ValueError("Sitemap fetch cancelled; nothing imported")
                        target = pending.pop(0)
                        if target in visited:
                            continue
                        visited.add(target)
                        if len(visited) > 25:
                            raise ValueError("Sitemap budget: 25 files")
                        status, headers, raw, truncated = request(
                            target,
                            {config["production"], config["staging"]}
                            | set(config.get("additional_origins", [])),
                            config["private_allowlist"],
                            limit=10 * 1024 * 1024,
                        )
                        total += len(raw)
                        if total > 20 * 1024 * 1024 or truncated or status != 200:
                            raise ValueError("Sitemap failed or byte budget exceeded; nothing imported")
                        parsed_rows = parse_inventory(raw, "gzip" if target.endswith(".gz") else "xml")
                        digest = hashlib.sha256(raw).hexdigest()
                        sources.append((target, digest, parsed_rows))
                        for row in parsed_rows:
                            row["_source"] = target
                            row["_fingerprint"] = digest
                        for row in parsed_rows:
                            if row.get("sitemap"):
                                if origin(row["url"]) != origin(data["url"]):
                                    raise ValueError("Nested sitemap left same-origin scope")
                                pending.append(row["url"])
                            else:
                                rows.append(row)
                        if len(rows) > 10000:
                            raise ValueError("Sitemap URL budget exceeded")
                    digest = hashlib.sha256(
                        canonical_json([(source, value) for source, value, _ in sources]).encode()
                    ).hexdigest()
                    for row in rows:
                        row["sitemap"] = False
                    result = {
                        "imported": store.import_rows(
                            data["project"], data["side"], rows, data["url"], digest, stop=event.is_set
                        )
                    }
                elif path == "/api/edit":
                    store.edit(data["project"], data["changes"], data["generation"])
                    result = {"ok": True}
                elif path == "/api/undo":
                    store.undo(data["project"])
                    result = {"ok": True}
                elif path == "/api/freeze":
                    result = store.freeze(data["project"])
                elif path == "/api/run":
                    result = {"id": runner.create(data["revision"], data["environment"], data.get("retry"))}
                elif path == "/api/stop":
                    runner.stop(data["run"], data["state"])
                    result = {"ok": True}
                elif path == "/api/resume":
                    runner.resume(data["run"])
                    result = {"ok": True}
                elif path == "/api/restore":
                    # New store file only; never replace the running database.
                    destination = str(
                        Path(store.path).with_name("restored-" + secrets.token_hex(6) + ".sqlite")
                    )
                    result = {
                        "path": Store.restore(data, destination),
                        "instruction": "Stop this server; open the restored file with --database",
                    }
                elif path == "/api/example":
                    pid = store.create(
                        "Guided example",
                        {"production": "https://example.com", "staging": "https://staging.example.com"},
                    )
                    raw = b"url,title\nhttps://example.com/old-service,Service\nhttps://example.com/about,About\nhttps://example.com/obsolete,Old campaign\n"
                    rows = parse_inventory(raw, "csv")
                    store.import_rows(
                        pid, "old", rows, "Original synthetic fixture", hashlib.sha256(raw).hexdigest()
                    )
                    new = parse_inventory(
                        b"url,title\nhttps://example.com/service,Service\nhttps://example.com/about,About\n",
                        "csv",
                    )
                    store.import_rows(pid, "new", new, "Replacement fixture", "synthetic")
                    result = {"id": pid}
                else:
                    raise ValueError("Unknown action")
                self.send(canonical_json(result))
            except (
                ValueError,
                KeyError,
                TypeError,
                OSError,
                StopIteration,
                RecursionError,
                csv.Error,
                zlib.error,
            ) as exc:
                self.send(canonical_json({"error": str(exc)}), status=400)
            finally:
                if isinstance(locals().get("data"), dict) and "job" in data and path != "/api/cancel-import":
                    with imports_lock:
                        imports.pop(data["job"], None)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description="Site Migration Workbench local UI and evidence CLI")
    parser.add_argument("--database", default="migration-workbench.sqlite")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--no-browser", action="store_true")
    backup = sub.add_parser("backup")
    backup.add_argument("output")
    restore = sub.add_parser("restore")
    restore.add_argument("input")
    restore.add_argument("output")
    report = sub.add_parser("report")
    report.add_argument("run", type=int)
    report.add_argument("--format", choices=["json", "csv", "html"], default="json")
    report.add_argument("--sharing", action="store_true")
    report.add_argument("--output", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("revision", type=int)
    verify.add_argument("--environment", choices=["staging", "production"], required=True)
    args = parser.parse_args()
    if args.command == "restore":
        print(Store.restore(json.loads(Path(args.input).read_text()), args.output))
        return
    os.umask(0o077)
    # One process owns a workspace. Prevent CLI startup from interrupting an active UI run.
    import fcntl

    lock_path = Path(args.database).absolute().with_suffix(".smw-lock")
    if lock_path.is_symlink():
        raise ValueError("Symlink lock path rejected")
    lock_handle = lock_path.open("a")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("Workspace is open in another process; close it before using the CLI")
    store = Store(args.database)
    if args.command == "backup":
        write_new_atomic(args.output, canonical_json(store.backup()))
    elif args.command == "report":
        text, kind = render(Runner(store).report(args.run), args.format, args.sharing)
        write_new_atomic(args.output, text)
    elif args.command == "verify":
        import time

        runner = Runner(store)
        run = runner.create(args.revision, args.environment)
        try:
            while store.query("SELECT state FROM runs WHERE id=?", (run,))[0]["state"] == "running":
                time.sleep(0.2)
        except KeyboardInterrupt:
            runner.stop(run)
        print(canonical_json(runner.report(run)))
    else:
        server = create_server(store, getattr(args, "port", 0))
        address = "http://127.0.0.1:" + str(server.server_port)
        print(address, flush=True)
        if not getattr(args, "no_browser", False):
            webbrowser.open(address)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.server_close()


if __name__ == "__main__":
    main()
