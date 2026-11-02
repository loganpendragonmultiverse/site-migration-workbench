"""Conservative URL identity, bounded inventory parsing, and review helpers."""

import csv
import difflib
import gzip
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from defusedxml import ElementTree

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 10000


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def url(value):
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise ValueError("URL must be text, 1–8192 characters")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise ValueError("Whitespace, controls and backslashes are not allowed in URLs")
    p = urlsplit(value)
    if (
        p.scheme not in ("http", "https")
        or not p.hostname
        or p.username is not None
        or p.password is not None
    ):
        raise ValueError("Only absolute HTTP(S) URLs without credentials are supported")
    if p.fragment:
        raise ValueError("Fragment URLs are not HTTP resource identities; remove the fragment explicitly")
    if p.port is not None and not 1 <= p.port <= 65535:
        raise ValueError("Invalid port")
    if re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise ValueError("Malformed percent encoding")
    host = p.hostname.encode("idna").decode("ascii").lower()
    if ":" in host:
        host = "[" + host + "]"
    port = p.port
    authority = host + (":" + str(port) if port and port != (443 if p.scheme == "https" else 80) else "")
    # Paths, slash, percent spelling and query order remain significant.
    return urlunsplit((p.scheme, authority, p.path or "/", p.query, ""))


def origin(value):
    p = urlsplit(url(value))
    return p.scheme + "://" + p.netloc


def parse_inventory(data, kind="list", columns=None):
    if len(data) > MAX_BYTES:
        raise ValueError("Input exceeds 10 MiB")
    if kind == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
            data = f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("Expanded sitemap exceeds 10 MiB")
        kind = "xml"
    text = data.decode("utf-8-sig")
    rows = []
    if kind == "xml":
        if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
            raise ValueError("XML entities and doctypes are disabled")
        try:
            root = ElementTree.fromstring(text, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        except ElementTree.ParseError as exc:
            raise ValueError("Invalid sitemap XML") from exc
        index = root.tag.rsplit("}", 1)[-1] == "sitemapindex"
        if root.tag.rsplit("}", 1)[-1] not in ("urlset", "sitemapindex"):
            raise ValueError("Expected sitemap urlset or sitemapindex")
        for n, node in enumerate(root, 1):
            loc = next((x.text for x in node if x.tag.rsplit("}", 1)[-1] == "loc"), None)
            if loc:
                rows.append({"url": loc.strip(), "row": n, "title": "", "priority": 0, "sitemap": index})
    elif kind == "csv":
        columns = columns or {
            "url": "url",
            "title": "title",
            "priority": "priority",
            "canonical": "canonical",
        }
        reader = csv.DictReader(io.StringIO(text))
        if columns.get("url") not in (reader.fieldnames or []):
            raise ValueError("Select a valid URL column; available: " + ", ".join(reader.fieldnames or []))
        for n, item in enumerate(reader, 2):
            rows.append(
                {
                    "url": item.get(columns["url"], ""),
                    "row": n,
                    **{
                        key: item.get(columns.get(key, key), "") or ""
                        for key in ("title", "priority", "canonical")
                    },
                }
            )
    elif kind == "list":
        rows = [
            {"url": x.strip(), "row": n, "title": "", "priority": 0}
            for n, x in enumerate(text.splitlines(), 1)
            if x.strip()
        ]
    else:
        raise ValueError("Unsupported inventory format")
    if len(rows) > MAX_ROWS:
        raise ValueError("Inventory exceeds 10,000 rows")
    for row in rows:
        row["original"] = row["url"]
        row["url"] = url(row["url"])
        row["priority"] = int(row.get("priority") or 0)
        if not 0 <= row["priority"] <= 100:
            raise ValueError("Priority must be 0–100")
        if row.get("canonical"):
            row["canonical"] = url(row["canonical"])
    return rows


def suggest(old, candidates):
    path = urlsplit(old["url"]).path
    scored = []
    for candidate in candidates:
        target_path = urlsplit(candidate["url"]).path
        exact = path == target_path
        path_score = difflib.SequenceMatcher(None, path, target_path).ratio()
        title_score = (
            difflib.SequenceMatcher(
                None, old.get("title", "").lower(), candidate.get("title", "").lower()
            ).ratio()
            if old.get("title") and candidate.get("title")
            else 0
        )
        score = 1 if exact else round(0.75 * path_score + 0.25 * title_score, 3)
        scored.append(
            {
                "url": candidate["url"],
                "score": score,
                "reason": "Exact path; review content"
                if exact
                else "Path similarity "
                + str(round(path_score, 3))
                + "; title similarity "
                + str(round(title_score, 3)),
            }
        )
    return sorted(scored, key=lambda x: (-x["score"], x["url"]))[:5]


def csv_text(rows, fields):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(fields)
    for row in rows:
        writer.writerow([safe_cell(row.get(k, "")) for k in fields])
    return output.getvalue()


def safe_cell(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value


def write_new_atomic(destination, text):
    """Create a complete artifact without overwriting any existing path."""
    import os
    import tempfile
    from pathlib import Path

    path = Path(destination).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Symlink input/output paths are unsupported")
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".smw-output-", dir=path.parent)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(text.encode("utf-8") if isinstance(text, str) else text)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)  # Atomic exclusive publication; existing destinations are untouched.
    finally:
        if temporary:
            os.unlink(temporary)
