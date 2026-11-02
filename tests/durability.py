"""Kill real processes mid-transaction/check/output and verify restart invariants."""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from smw.domain import parse_inventory
from smw.jobs import Runner
from smw.store import Store

results = []
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    database = root / "workspace.sqlite"
    store = Store(database)
    pid = store.create("Crash fixture", {"production": "https://example.com"})
    rows = parse_inventory(("\n".join("https://example.com/p" + str(n) for n in range(500))).encode())
    store.import_rows(pid, "old", rows, "Original synthetic crash corpus", "synthetic")
    store.edit(
        pid,
        [
            {
                "id": r["id"],
                "patch": {"disposition": "keep", "reviewed": True, "rationale": "Original decision"},
            }
            for r in store.inventory(pid)
        ],
        1,
    )
    approved = store.freeze(pid)
    baseline = store.inventory(pid)
    for phase in ("import", "edit", "verification", "export"):
        ready = root / (phase + ".ready")
        output = root / "output.json"
        code = r"""
import os,sys,time
from pathlib import Path
from smw.store import Store
from smw.domain import parse_inventory,write_new_atomic
import smw.store as module
import smw.jobs as jobs
database,ready,phase,output,pid,rid=sys.argv[1:]
pid=int(pid);rid=int(rid)
store=Store(database)
def blocked():
 Path(ready).write_text("ready")
 while True:time.sleep(.05)
if phase=="import":
 count=0
 def stop():
  global count
  count+=1
  if count==250:blocked()
  return False
 store.import_rows(pid,"old",parse_inventory(("\n".join("https://example.com/new"+str(n) for n in range(500))).encode()),"Crash import","synthetic",stop=stop)
elif phase=="edit":
 original=module.validate_decision
 count=0
 def validate(row):
  global count
  count+=1
  if count==250:blocked()
  return original(row)
 module.validate_decision=validate
 store.edit(pid,[{"id":r["id"],"patch":{"rationale":"Incomplete edit"}} for r in store.inventory(pid)],store.project(pid)["generation"])
elif phase=="verification":
 calls=0
 def capture(target,config,stop,**kwargs):
  global calls
  calls+=1
  if calls==2:blocked()
  return {"requested_url":target,"observed_at":"2026-11-02T00:00:00+00:00","source":"synthetic injected capture","final_url":target,"status":200,"chain":[{"url":target,"status":200}],"canonicals":[target],"robots":[],"truncated":False}
 jobs.capture=capture
 runner=jobs.Runner(store)
 runner.interval=0
 run=runner.create(rid,"production")
 while True:time.sleep(.05)
else:
 original=os.fsync
 def sync(fd):
  original(fd)
  blocked()
 os.fsync=sync
 write_new_atomic(output,'{"complete":true}')
"""
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                code,
                str(database),
                str(ready),
                phase,
                str(output),
                str(pid),
                str(approved["id"]),
            ]
        )
        limit = time.monotonic() + 15
        while not ready.exists():
            if process.poll() is not None:
                raise AssertionError("Child exited before crash point: " + phase)
            if time.monotonic() > limit:
                process.kill()
                raise AssertionError("Crash fixture deadline: " + phase)
            time.sleep(0.02)
        process.kill()
        process.wait(timeout=5)
        recovered = Store(database)
        assert recovered.inventory(pid) == baseline, phase
        assert recovered.revision(approved["id"])["hash"] == approved["hash"], phase
        if phase == "verification":
            run = recovered.query("SELECT * FROM runs ORDER BY id DESC")[0]
            assert run["state"] == "interrupted"
            assert len(recovered.query("SELECT * FROM observations WHERE run=?", (run["id"],))) == 1
            import smw.jobs as jobs

            original = jobs.capture
            jobs.capture = lambda target, config, stop, **kwargs: {
                "requested_url": target,
                "observed_at": "2026-11-02T00:00:00+00:00",
                "source": "synthetic injected capture",
                "final_url": target,
                "status": 200,
                "chain": [{"url": target, "status": 200}],
                "canonicals": [target],
                "robots": [],
                "truncated": False,
            }
            runner = Runner(recovered)
            runner.interval = 0
            runner.resume(run["id"])
            limit = time.monotonic() + 20
            while run["id"] in runner.active:
                assert time.monotonic() < limit
                time.sleep(0.02)
            assert runner.report(run["id"])["counts"]["pass"] == 500
            jobs.capture = original
        if phase == "export":
            assert not output.exists()
        results.append(
            {
                "phase": phase,
                "termination": "SIGKILL",
                "approved_hash_preserved": True,
                "inventory_preserved": True,
                "recovered": True,
            }
        )
print(json.dumps(results, indent=2))
Path("evidence/durability.json").write_text(json.dumps(results, indent=2))
