"""Transactional projects and immutable approvals/runs in SQLite."""

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .domain import MAX_ROWS, canonical_json, fingerprint, now, origin, url, write_new_atomic

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, name TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, generation INTEGER NOT NULL DEFAULT 0, config TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inventory(id INTEGER PRIMARY KEY, project INTEGER NOT NULL REFERENCES projects(id), side TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, canonical TEXT NOT NULL DEFAULT '', priority INTEGER NOT NULL DEFAULT 0, disposition TEXT NOT NULL DEFAULT 'unresolved', target TEXT NOT NULL DEFAULT '', status INTEGER NOT NULL DEFAULT 301, rationale TEXT NOT NULL DEFAULT '', reviewed INTEGER NOT NULL DEFAULT 0, excluded INTEGER NOT NULL DEFAULT 0, exception TEXT NOT NULL DEFAULT '', UNIQUE(project,side,url));
CREATE TABLE IF NOT EXISTS provenance(id INTEGER PRIMARY KEY, item INTEGER NOT NULL REFERENCES inventory(id), source TEXT NOT NULL, row INTEGER NOT NULL, original TEXT NOT NULL, imported TEXT NOT NULL, fingerprint TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS undo(id INTEGER PRIMARY KEY, project INTEGER NOT NULL, rows TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY, project INTEGER NOT NULL, generation INTEGER NOT NULL, created TEXT NOT NULL, hash TEXT NOT NULL, plan TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, revision INTEGER NOT NULL REFERENCES revisions(id), environment TEXT NOT NULL, state TEXT NOT NULL, created TEXT NOT NULL, finished TEXT, config TEXT NOT NULL, error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS observations(run INTEGER NOT NULL REFERENCES runs(id), item INTEGER NOT NULL, evidence TEXT NOT NULL, PRIMARY KEY(run,item));
"""
EDITABLE = {"disposition", "target", "status", "rationale", "reviewed", "excluded", "priority", "exception"}


class Store:
    def __init__(self, path):
        self.path = str(path)
        for part in (Path(path).absolute(), *Path(path).absolute().parents):
            if part.is_symlink():
                raise ValueError("Symlink database paths are unsupported")
        self.lock = threading.RLock()
        with self.connection() as db:
            db.executescript(SCHEMA)
            if "error" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
                db.execute("ALTER TABLE runs ADD COLUMN error TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE runs SET state='interrupted' WHERE state='running'")

    @contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=20)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            try:
                with db:
                    yield db
            finally:
                db.close()

    def query(self, sql, params=()):
        with self.connection() as db:
            return [dict(x) for x in db.execute(sql, params)]

    def project(self, pid):
        rows = self.query("SELECT * FROM projects WHERE id=?", (pid,))
        if not rows:
            raise ValueError("Unknown project")
        p = rows[0]
        p["config"] = json.loads(p["config"])
        return p

    def create(self, name, config):
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Project name is required, up to 200 characters")
        config = validate_config(config)
        with self.connection() as db:
            return db.execute(
                "INSERT INTO projects(name,config,created) VALUES(?,?,?)",
                (name.strip(), canonical_json(config), now()),
            ).lastrowid

    def archive(self, pid, value):
        self.project(pid)
        with self.connection() as db:
            db.execute("UPDATE projects SET archived=? WHERE id=?", (bool(value), pid))

    def configure(self, pid, config):
        self.project(pid)
        config = validate_config(config)
        with self.connection() as db:
            db.execute(
                "UPDATE projects SET config=?,generation=generation+1 WHERE id=?",
                (canonical_json(config), pid),
            )

    def inventory(self, pid, side="old"):
        self.project(pid)
        rows = self.query("SELECT * FROM inventory WHERE project=? AND side=? ORDER BY url", (pid, side))
        provenance = self.query(
            "SELECT provenance.* FROM provenance JOIN inventory ON inventory.id=provenance.item WHERE inventory.project=?",
            (pid,),
        )
        groups = {}
        for p in provenance:
            groups.setdefault(p["item"], []).append(p)
        for r in rows:
            r["provenance"] = groups.get(r["id"], [])
        return rows

    def import_rows(self, pid, side, rows, source, digest, stop=lambda: False):
        self.project(pid)
        if side not in ("old", "new") or not source or len(source) > 500:
            raise ValueError("Select old/new and name the input source")
        if any(r.get("sitemap") for r in rows):
            raise ValueError("Sitemap index: use the bounded fetch action or import child sitemaps")
        if len(rows) > MAX_ROWS:
            raise ValueError("Too many rows")
        with self.connection() as db:
            existing = {
                r[0] for r in db.execute("SELECT url FROM inventory WHERE project=? AND side=?", (pid, side))
            }
            if len(existing | {r["url"] for r in rows}) > MAX_ROWS:
                raise ValueError("Project limit: 10,000 URLs per side")
            for row in rows:
                if stop():
                    raise ValueError("Import cancelled; transaction rolled back")
                normalized = url(row["url"])
                db.execute(
                    "INSERT OR IGNORE INTO inventory(project,side,url,title,canonical,priority) VALUES(?,?,?,?,?,?)",
                    (
                        pid,
                        side,
                        normalized,
                        row.get("title", ""),
                        row.get("canonical", ""),
                        row.get("priority", 0),
                    ),
                )
                item = db.execute(
                    "SELECT id FROM inventory WHERE project=? AND side=? AND url=?", (pid, side, normalized)
                ).fetchone()[0]
                db.execute(
                    "INSERT INTO provenance(item,source,row,original,imported,fingerprint) VALUES(?,?,?,?,?,?)",
                    (
                        item,
                        row.get("_source", source),
                        row["row"],
                        row.get("original", normalized),
                        now(),
                        row.get("_fingerprint", digest),
                    ),
                )
            db.execute("UPDATE projects SET generation=generation+1 WHERE id=?", (pid,))
        return len(rows)

    def edit(self, pid, changes, expected_generation):
        self.project(pid)
        if not isinstance(changes, list) or not changes or len(changes) > MAX_ROWS:
            raise ValueError("Select one or more rows")
        with self.connection() as db:
            generation = db.execute("SELECT generation FROM projects WHERE id=?", (pid,)).fetchone()[0]
            if generation != expected_generation:
                raise ValueError("Project changed; reload before applying edits")
            before = []
            seen = set()
            for change in changes:
                item_id = change["id"]
                if item_id in seen:
                    raise ValueError("Duplicate row in edit")
                seen.add(item_id)
                record = db.execute(
                    "SELECT * FROM inventory WHERE project=? AND side='old' AND id=?", (pid, item_id)
                ).fetchone()
                if not record:
                    raise ValueError("Unknown source row")
                record = dict(record)
                before.append(dict(record))
                patch = change["patch"]
                if not patch or set(patch) - EDITABLE:
                    raise ValueError("Invalid editable fields")
                record.update(patch)
                validate_decision(record)
                # Editing any decision invalidates its review unless explicitly reviewed in this action.
                if "reviewed" not in patch:
                    record["reviewed"] = 0
                db.execute(
                    "UPDATE inventory SET " + ",".join(k + "=?" for k in sorted(EDITABLE)) + " WHERE id=?",
                    tuple(record[k] for k in sorted(EDITABLE)) + (item_id,),
                )
            db.execute("INSERT INTO undo(project,rows) VALUES(?,?)", (pid, canonical_json(before)))
            db.execute("UPDATE projects SET generation=generation+1 WHERE id=?", (pid,))

    def undo(self, pid):
        self.project(pid)
        with self.connection() as db:
            record = db.execute(
                "SELECT id,rows FROM undo WHERE project=? ORDER BY id DESC LIMIT 1", (pid,)
            ).fetchone()
            if not record:
                raise ValueError("Nothing to undo")
            for row in json.loads(record["rows"]):
                db.execute(
                    "UPDATE inventory SET " + ",".join(k + "=?" for k in sorted(EDITABLE)) + " WHERE id=?",
                    tuple(row[k] for k in sorted(EDITABLE)) + (row["id"],),
                )
            db.execute("DELETE FROM undo WHERE id=?", (record["id"],))
            db.execute("UPDATE projects SET generation=generation+1 WHERE id=?", (pid,))

    def issues(self, pid):
        rows = self.inventory(pid)
        by_url = {r["url"]: r for r in rows}
        issues = []
        targets = {}
        for r in rows:
            reason = None
            if r["excluded"]:
                if not r["rationale"]:
                    reason = "Excluded row needs a reason"
            elif r["disposition"] == "unresolved" or not r["reviewed"]:
                reason = "Unresolved or unreviewed"
            elif not r["rationale"]:
                reason = "Decision needs a rationale"
            if reason:
                issues.append({"id": r["id"], "message": reason})
            if r["excluded"] or r["disposition"] not in ("redirect", "merge"):
                continue
            target = r["target"]
            targets.setdefault(target, []).append(r)
            if (
                target in by_url
                and by_url[target]["disposition"] in ("redirect", "merge", "retire")
                and not by_url[target]["excluded"]
            ):
                issues.append(
                    {
                        "id": r["id"],
                        "message": "Target is another redirect or retirement; flatten chain and review",
                    }
                )
            visited = {r["url"]}
            while target in by_url and by_url[target]["disposition"] in ("redirect", "merge"):
                if target in visited:
                    issues.append({"id": r["id"], "message": "Redirect loop"})
                    break
                visited.add(target)
                target = by_url[target]["target"]
        for target, members in targets.items():
            if len(members) > 1 and any(r["disposition"] != "merge" for r in members):
                issues.append(
                    {
                        "id": members[0]["id"],
                        "message": "Many-to-one mapping requires explicit merge dispositions: " + target,
                    }
                )
        return issues

    def freeze(self, pid):
        with self.lock:
            project = self.project(pid)
            issues = self.issues(pid)
            if issues:
                raise ValueError("Approval blocked: " + "; ".join(x["message"] for x in issues[:10]))
            rows = self.inventory(pid)
            if not rows:
                raise ValueError("Import source inventory first")
            plan = {
                "schema": 1,
                "name": project["name"],
                "config": project["config"],
                "generation": project["generation"],
                "rows": rows,
                "targets": self.inventory(pid, "new"),
                "assumptions": "Operator-reviewed supplied inventory only; approval is not a signature",
            }
            digest = fingerprint(plan)
            with self.connection() as db:
                rid = db.execute(
                    "INSERT INTO revisions(project,generation,created,hash,plan) VALUES(?,?,?,?,?)",
                    (pid, project["generation"], now(), digest, canonical_json(plan)),
                ).lastrowid
            return {"id": rid, "hash": digest}

    def revision(self, rid):
        rows = self.query("SELECT * FROM revisions WHERE id=?", (rid,))
        if not rows:
            raise ValueError("Unknown revision")
        r = rows[0]
        r["plan"] = json.loads(r["plan"])
        if fingerprint(r["plan"]) != r["hash"]:
            raise ValueError("Plan hash mismatch")
        return r

    def backup(self):
        import base64
        import hashlib
        import tempfile

        # A real destination lets SQLite convert WAL to a portable rollback-journal file.
        with tempfile.TemporaryDirectory(prefix="smw-backup-") as folder:
            destination = Path(folder) / "backup.sqlite"
            with self.connection() as db:
                copied = sqlite3.connect(destination)
                try:
                    db.backup(copied)
                    copied.execute("PRAGMA journal_mode=DELETE")
                finally:
                    copied.close()
            if destination.stat().st_size > 128 * 1024 * 1024:
                raise ValueError("Workspace backup exceeds 128 MiB limit")
            data = destination.read_bytes()
        return {
            "schema": 1,
            "sha256": hashlib.sha256(data).hexdigest(),
            "database": base64.b64encode(data).decode(),
        }

    @staticmethod
    def restore(envelope, destination):
        for part in (Path(destination).absolute(), *Path(destination).absolute().parents):
            if part.is_symlink():
                raise ValueError("Symlink restore destinations are unsupported")
        import base64
        import hashlib

        if (
            not isinstance(envelope, dict)
            or envelope.get("schema") != 1
            or not isinstance(envelope.get("database"), str)
            or len(envelope["database"]) > 180 * 1024 * 1024
        ):
            raise ValueError("Invalid or oversized backup envelope")
        data = base64.b64decode(envelope["database"], validate=True)
        if len(data) > 128 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != envelope["sha256"]:
            raise ValueError("Backup checksum/size invalid")
        db = sqlite3.connect(":memory:")
        try:
            db.deserialize(data)
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Database integrity failure")
            # Only our fixed tables and SQLite autoindices; no executable views/triggers.
            objects = db.execute(
                "SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            allowed = {"projects", "inventory", "provenance", "undo", "revisions", "runs", "observations"}
            if (
                any(t != "table" or n not in allowed for t, n in objects)
                or {n for t, n in objects} != allowed
            ):
                raise ValueError("Unexpected backup schema")
            for raw, digest in db.execute("SELECT plan,hash FROM revisions"):
                if fingerprint(json.loads(raw)) != digest:
                    raise ValueError("Backup plan hash mismatch")
            path = Path(destination)
            # Exclusive creation; restore never replaces a project store.
            write_new_atomic(path, data)
        except sqlite3.Error as exc:
            raise ValueError("Invalid SQLite backup") from exc
        finally:
            db.close()
        return str(destination)


def validate_decision(r):
    if r["disposition"] not in ("unresolved", "keep", "redirect", "merge", "retire"):
        raise ValueError("Unknown disposition")
    for field in ("reviewed", "excluded"):
        if type(r[field]) not in (bool, int) or r[field] not in (0, 1):
            raise ValueError(field + " must be boolean")
    if type(r["priority"]) is not int or not 0 <= r["priority"] <= 100:
        raise ValueError("Priority must be 0–100")
    for field in ("rationale", "exception", "target"):
        if not isinstance(r[field], str) or len(r[field]) > 8192:
            raise ValueError("Text exceeds limit")
    if r["disposition"] == "keep":
        r["status"] = 200
        r["target"] = ""
    if r["disposition"] == "retire":
        r["target"] = ""
    if r["disposition"] in ("redirect", "merge"):
        r["target"] = url(r["target"])
        if r["target"] == r["url"]:
            raise ValueError("Self redirect")
        if r["status"] not in (301, 308):
            raise ValueError("Redirects require 301 or 308")
    if r["disposition"] == "retire" and r["status"] not in (404, 410):
        raise ValueError("Retirements require 404 or 410")


def validate_config(config):
    if set(config) - {"production", "staging", "private_allowlist", "staging_noindex", "additional_origins"}:
        raise ValueError("Unknown project setting")
    from urllib.parse import urlsplit

    for key in ("production", "staging"):
        if key in config:
            parsed = urlsplit(url(config[key]))
            if parsed.path not in ("", "/") or parsed.query:
                raise ValueError("Environment must be an origin without path/query")
    if "staging_noindex" in config and type(config["staging_noindex"]) is not bool:
        raise ValueError("staging_noindex must be boolean")
    additional = config.get("additional_origins", [])
    if not isinstance(additional, list) or len(additional) > 16:
        raise ValueError("Additional approved origins must be a list of at most 16 sites")
    for value in additional:
        parsed = urlsplit(url(value))
        if parsed.path not in ("", "/") or parsed.query:
            raise ValueError("Additional site origins cannot contain a path/query")
    production = origin(config["production"])
    staging = origin(config.get("staging", production))
    allowlist = config.get("private_allowlist", [])
    import ipaddress

    if not isinstance(allowlist, list) or len(allowlist) > 16:
        raise ValueError("Allowlist must be at most 16 exact IP addresses")
    for value in allowlist:
        ipaddress.ip_address(value)  # Narrow exact address, never a CIDR.
    return {
        "production": production,
        "staging": staging,
        "additional_origins": sorted({origin(value) for value in additional}),
        "private_allowlist": allowlist,
        "staging_noindex": bool(config.get("staging_noindex", True)),
    }
