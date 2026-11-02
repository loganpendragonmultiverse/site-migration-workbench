"""Reproducible supplied-inventory benchmark; no external network duration claim."""

import hashlib
import json
import platform
import resource
import tempfile
import time
from pathlib import Path

from smw.domain import parse_inventory
from smw.store import Store

with tempfile.TemporaryDirectory() as t:
    store = Store(Path(t) / "scale.sqlite")
    pid = store.create("10,000 synthetic URLs", {"production": "https://example.com"})
    data = "\n".join("https://example.com/page-" + str(i) for i in range(10000)).encode()
    started = time.perf_counter()
    rows = parse_inventory(data)
    store.import_rows(pid, "old", rows, "Synthetic 10k", hashlib.sha256(data).hexdigest())
    imported = time.perf_counter()
    values = store.inventory(pid)
    loaded = time.perf_counter()
    selected = [r for r in values if "page-99" in r["url"]]
    filtered = time.perf_counter()
    print(
        json.dumps(
            {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "cpu": platform.processor(),
                "rows": len(values),
                "import_seconds": imported - started,
                "load_seconds": loaded - imported,
                "filter_seconds": filtered - loaded,
                "matched": len(selected),
                "peak_rss_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "network": "Not measured; offline supplied inventory",
            },
            indent=2,
        )
    )
