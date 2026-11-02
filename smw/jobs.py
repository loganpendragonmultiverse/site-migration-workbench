"""Durable declared-set verification; no discovery crawling."""

import json
import threading
import time

from .domain import canonical_json, now
from .network import capture, compare, request, substitute


class Runner:
    def __init__(self, store):
        self.store = store
        self.active = {}
        self.lock = threading.Lock()
        self.interval = 0.5
        self.network_lock = threading.Lock()
        self.last_request = 0

    def create(self, rid, environment, retry=None):
        if environment not in ("staging", "production"):
            raise ValueError("Unknown environment")
        revision = self.store.revision(rid)
        rows = [r for r in revision["plan"]["rows"] if not r["excluded"]]
        if retry is not None:
            wanted = set(retry)
            if not wanted <= {r["id"] for r in rows}:
                raise ValueError("Retry contains unknown row")
            rows = [r for r in rows if r["id"] in wanted]
        config = {"ids": [r["id"] for r in rows], "rate_per_second": 2}
        with self.store.connection() as db:
            run = db.execute(
                "INSERT INTO runs(revision,environment,state,created,config) VALUES(?,?,'queued',?,?)",
                (rid, environment, now(), canonical_json(config)),
            ).lastrowid
        try:
            self.resume(run)
        except ValueError:
            # A concurrent run retains this request in the durable queue.
            if not self.active:
                raise
        return run

    def resume(self, run):
        with self.lock:
            if run in self.active:
                raise ValueError("Run already active")
            if self.active:
                raise ValueError("Another verification is active; pause it or wait for its queue")
            result = self.store.query("SELECT * FROM runs WHERE id=?", (run,))
            if not result:
                raise ValueError("Unknown run")
            if result[0]["state"] == "completed":
                raise ValueError("Completed run is immutable; create a rerun")
            event = threading.Event()
            self.active[run] = event
            with self.store.connection() as db:
                db.execute("UPDATE runs SET state='running',finished=NULL WHERE id=?", (run,))
            thread = threading.Thread(target=self.work, args=(run, event), daemon=True)
            thread.start()

    def stop(self, run, state="paused"):
        if state not in ("paused", "cancelled"):
            raise ValueError("Invalid stop state")
        with self.lock:
            event = self.active.get(run)
            if event:
                event.set()
            with self.store.connection() as db:
                db.execute(
                    "UPDATE runs SET state=?,finished=? WHERE id=? AND state!='completed'",
                    (state, now(), run),
                )

    def work(self, run, event):
        try:
            record = self.store.query("SELECT * FROM runs WHERE id=?", (run,))[0]
            revision = self.store.revision(record["revision"])
            config = revision["plan"]["config"]
            ids = set(json.loads(record["config"])["ids"])
            done = {r["item"] for r in self.store.query("SELECT item FROM observations WHERE run=?", (run,))}
            for row in revision["plan"]["rows"]:
                if row["id"] not in ids or row["id"] in done or event.is_set():
                    continue
                target = substitute(row["url"], config, record["environment"])

                def limited_request(*args, **kwargs):
                    with self.network_lock:
                        delay = max(0, self.interval - (time.monotonic() - self.last_request))
                        if event.wait(delay):
                            raise ValueError("Check interrupted before request")
                        self.last_request = time.monotonic()
                        return request(*args, **kwargs)

                evidence = capture(target, config, stop=event.is_set, transport=limited_request)
                if event.is_set():
                    break  # Interrupted item remains explicitly unverified for resume.
                result = compare(row, evidence, config, record["environment"])
                with self.store.connection() as db:
                    db.execute(
                        "INSERT INTO observations(run,item,evidence) VALUES(?,?,?)",
                        (run, row["id"], canonical_json(result)),
                    )
                event.wait(self.interval)
            if not event.is_set():
                with self.store.connection() as db:
                    db.execute("UPDATE runs SET state='completed',finished=? WHERE id=?", (now(), run))
        except Exception as exc:
            with self.store.connection() as db:
                db.execute(
                    "UPDATE runs SET state='interrupted',finished=?,error=? WHERE id=?",
                    (now(), str(exc), run),
                )
        finally:
            with self.lock:
                self.active.pop(run, None)
            queued = self.store.query("SELECT id FROM runs WHERE state='queued' ORDER BY id LIMIT 1")
            if queued:
                try:
                    self.resume(queued[0]["id"])
                except ValueError:
                    pass  # Another caller acquired the single worker.

    def report(self, run):
        record = self.store.query("SELECT * FROM runs WHERE id=?", (run,))
        if not record:
            raise ValueError("Unknown run")
        record = record[0]
        revision = self.store.revision(record["revision"])
        ids = set(json.loads(record["config"])["ids"])
        observed = {
            r["item"]: json.loads(r["evidence"])
            for r in self.store.query("SELECT * FROM observations WHERE run=?", (run,))
        }
        rows = []
        targets = {r["url"]: r for r in revision["plan"].get("targets", [])}
        counts = {key: 0 for key in ("pass", "fail", "warning", "not checked", "inconclusive", "excluded")}
        for row in revision["plan"]["rows"]:
            evidence = observed.get(
                row["id"],
                {
                    "result": "not checked",
                    "issues": ["Outside this retry scope" if row["id"] not in ids else "Not observed"],
                    "exception": row["exception"],
                },
            )
            if row["excluded"]:
                evidence = {"result": "excluded", "issues": [row["rationale"]], "exception": row["exception"]}
            counts[evidence["result"]] += 1
            rows.append(
                {
                    "id": row["id"],
                    "url": row["url"],
                    "disposition": row["disposition"],
                    "target": row["target"],
                    "expected_status": row["status"],
                    "supplied_title": row["title"],
                    "supplied_canonical": row["canonical"],
                    "supplied_target_title": targets.get(row["target"], {}).get("title", ""),
                    "priority": row["priority"],
                    "rationale": row["rationale"],
                    **evidence,
                }
            )
        return {
            "schema": 1,
            "run": record,
            "revision": revision["id"],
            "plan_hash": revision["hash"],
            "plan_generation": revision["generation"],
            "environment": record["environment"],
            "denominator": len(rows),
            "counts": counts,
            "rows": rows,
            "limitations": "Declared supplied inventory only. No JS, auth journey, robots.txt crawl, ranking/indexing or semantic equivalence claim. Exceptions are not technical passes.",
        }

    def regression(self, before, after):
        left = self.report(before)
        right = self.report(after)
        if left["plan_hash"] != right["plan_hash"] or left["environment"] != right["environment"]:
            raise ValueError("Compare runs of the same frozen plan and environment")
        previous = {r["id"]: r for r in left["rows"]}
        changes = []
        for row in right["rows"]:
            old = previous[row["id"]]["result"]
            current = row["result"]
            state = (
                "still unverified"
                if current in ("not checked", "inconclusive")
                else "newly failing"
                if current == "fail" and old != "fail"
                else "resolved"
                if old in ("fail", "inconclusive", "not checked") and current in ("pass", "warning")
                else "unchanged"
            )
            changes.append(
                {"id": row["id"], "url": row["url"], "before": old, "after": current, "change": state}
            )
        return changes
