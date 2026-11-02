"""Rebuild the original offline sample; timestamps reflect this local generation."""

import hashlib
import tempfile
from pathlib import Path

from smw.domain import canonical_json, parse_inventory
from smw.exporters import deployment, export_plan
from smw.store import Store

HERE = Path(__file__).parent
with tempfile.TemporaryDirectory() as folder:
    store = Store(Path(folder) / "example.sqlite")
    pid = store.create(
        "Completed original synthetic migration",
        {"production": "https://example.com", "staging": "https://staging.example.com"},
    )
    raw = b"url,title,priority\nhttps://example.com/about,About,20\nhttps://example.com/old-service,Service,80\nhttps://example.com/merge-alpha,Alpha,40\nhttps://example.com/merge-beta,Beta,40\nhttps://example.com/obsolete,Expired campaign,0\n"
    store.import_rows(
        pid,
        "old",
        parse_inventory(raw, "csv"),
        "Original synthetic migration fixture",
        hashlib.sha256(raw).hexdigest(),
    )
    changes = []
    for row in store.inventory(pid):
        tail = row["url"].rsplit("/", 1)[1]
        disposition = (
            "keep"
            if tail == "about"
            else "retire"
            if tail == "obsolete"
            else "merge"
            if tail.startswith("merge")
            else "redirect"
        )
        target = (
            "https://example.com/bundle"
            if disposition == "merge"
            else "https://example.com/service"
            if disposition == "redirect"
            else ""
        )
        changes.append(
            {
                "id": row["id"],
                "patch": {
                    "disposition": disposition,
                    "target": target,
                    "status": 410 if disposition == "retire" else 301,
                    "rationale": "Original synthetic planning decision; no remote observation claimed",
                    "reviewed": True,
                },
            }
        )
    store.edit(pid, changes, 1)
    revision = store.revision(store.freeze(pid)["id"])
    (HERE / "completed-plan.json").write_text(export_plan(revision, "json")[0])
    (HERE / "completed-migration-backup.json").write_text(canonical_json(store.backup()))
    (HERE / "completed-nginx.conf").write_text(deployment(revision, "nginx")[0])
    (HERE / "completed-wordpress.json").write_text(deployment(revision, "wordpress")[0])
