"""Strict deployment adapters: explicit blockers instead of lossy output."""

import json
import re
from urllib.parse import urlsplit

from .domain import canonical_json, csv_text, origin


def export_plan(revision, fmt):
    rows = revision["plan"]["rows"]
    if fmt == "json":
        return canonical_json(revision) + "\n", "application/json"
    if fmt == "csv":
        return csv_text(
            rows,
            [
                "id",
                "url",
                "disposition",
                "target",
                "status",
                "reviewed",
                "excluded",
                "rationale",
                "exception",
                "priority",
            ],
        ), "text/csv"
    raise ValueError("Choose json or csv")


def deployment(revision, adapter):
    plan = revision["plan"]
    blockers = []
    rules = []
    for r in plan["rows"]:
        if r["excluded"] or r["disposition"] == "keep":
            continue
        allowed_statuses = (301, 308) if r["disposition"] in ("redirect", "merge") else (404, 410)
        if type(r["status"]) is not int or r["status"] not in allowed_statuses:
            blockers.append(str(r["id"]) + ": unsupported deployment status; implement this row manually")
        p = urlsplit(r["url"])
        if origin(r["url"]) != plan["config"]["production"]:
            blockers.append(
                str(r["id"])
                + ": source host differs from configured production host; implement on that host separately"
            )
        if r["disposition"] == "unresolved":
            blockers.append(str(r["id"]) + ": unresolved")
        if (
            p.query
            or "%" in p.path
            or not re.fullmatch(r"/[A-Za-z0-9/_.,~-]*", p.path)
            or "//" in p.path
            or any(part in (".", "..") for part in p.path.split("/"))
        ):
            blockers.append(
                str(r["id"])
                + ": query/encoded/Unicode/unsafe path requires manual exact request-URI implementation"
            )
        if adapter == "wordpress" and r["disposition"] == "retire" and r["status"] == 404:
            blockers.append(
                str(r["id"])
                + ": WordPress adapter supports explicit 410 retirement only; configure real 404 manually"
            )
        if r["disposition"] in ("redirect", "merge"):
            target = r["target"]
            if not re.fullmatch(r"https?://[A-Za-z0-9:/?&=._~%+,-]+", target):
                blockers.append(str(r["id"]) + ": target cannot be represented safely; implement manually")
        rules.append(r)
    if (
        adapter == "wordpress"
        and rules
        and any(
            not r["excluded"] and ("%" in urlsplit(r["url"]).path or not urlsplit(r["url"]).path.isascii())
            for r in plan["rows"]
        )
    ):
        blockers.append(
            "WordPress cannot promise raw encoding distinctions declared in this plan; implement these rules manually"
        )
    if blockers:
        raise ValueError("Export blocked; no rows emitted: " + "; ".join(blockers))
    if adapter == "nginx":
        lines = [
            "# Plan " + revision["hash"],
            "# Include inside the production server block.",
            "# Query-bearing requests deliberately return 400; this adapter covers query-free sources only.",
        ]
        for r in rules:
            lines += [
                'location = "' + urlsplit(r["url"]).path + '" {',
                '    if ($args != "") { return 400; }',
                '    if ($request_uri != "' + urlsplit(r["url"]).path + '") { return 404; }',
                "    return "
                + str(r["status"])
                + (" " + r["target"] if r["disposition"] != "retire" else "")
                + ";",
                "}",
            ]
        return "\n".join(lines) + "\n", "text/plain"
    if adapter == "wordpress":
        redirects = []
        for n, r in enumerate(rules):
            redirects.append(
                {
                    "url": urlsplit(r["url"]).path,
                    "match_url": urlsplit(r["url"]).path,
                    "match_type": "url",
                    "action_type": "error" if r["disposition"] == "retire" else "url",
                    "action_code": r["status"],
                    "action_data": "" if r["disposition"] == "retire" else r["target"],
                    "regex": False,
                    "group_id": 1,
                    "position": n,
                    "enabled": True,
                    "match_data": {
                        "source": {
                            "flag_query": "exact",
                            "flag_case": False,
                            "flag_trailing": False,
                            "flag_regex": False,
                        }
                    },
                    "title": "Migration plan " + revision["hash"][:12],
                }
            )
        return json.dumps(
            {
                "plugin": {"version": "5.5.2", "export_date": revision["created"]},
                "groups": [{"id": 1, "name": "Site Migration Workbench", "module_id": 1, "enabled": True}],
                "redirects": redirects,
            },
            indent=2,
        ) + "\n", "application/json"
    raise ValueError("Unknown adapter")
