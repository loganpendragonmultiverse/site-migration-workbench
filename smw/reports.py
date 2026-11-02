"""Deterministic evidence rendering with a separate, explicit sharing mode."""

import html
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .domain import canonical_json, csv_text


def redact(value, key=""):
    if key in (
        "rationale",
        "exception",
        "config",
        "title",
        "supplied_title",
        "supplied_canonical",
        "supplied_target_title",
    ):
        return "[redacted]"
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items() if k != "headers"}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            try:
                p = urlsplit(value)
                return urlunsplit(
                    (
                        p.scheme,
                        p.netloc,
                        p.path,
                        urlencode([(k, "[redacted]") for k, v in parse_qsl(p.query, keep_blank_values=True)]),
                        "",
                    )
                )
            except ValueError:
                pass
        if key in ("error", "issues", "robots", "canonicals"):
            return "[redacted; inspect private evidence]"
    return value


def render(report, fmt="json", sharing=False):
    data = redact(report) if sharing else report
    if fmt == "json":
        return canonical_json(data) + "\n", "application/json"
    if fmt == "csv":
        return csv_text(
            data["rows"],
            [
                "id",
                "url",
                "target",
                "expected_status",
                "status",
                "final_url",
                "result",
                "issues",
                "observed_at",
                "exception",
                "priority",
            ],
        ), "text/csv"
    if fmt == "html":

        def e(v):
            return html.escape(str(v))

        rows = "".join(
            "<tr>"
            + "".join(
                "<td>" + e(r.get(k, "")) + "</td>"
                for k in ("url", "target", "expected_status", "status", "result", "issues", "exception")
            )
            + "</tr>"
            for r in data["rows"]
        )
        return (
            "<!doctype html><html lang=en><meta charset=utf-8><title>Migration handoff</title><style>body{font:16px system-ui;margin:2rem;color:#16232b}table{border-collapse:collapse;width:100%}td,th{border:1px solid #aaa;padding:.6rem;text-align:left;overflow-wrap:anywhere}</style><h1>Migration evidence handoff</h1><p>Plan "
            + e(data["plan_hash"])
            + " / revision "
            + e(data["revision"])
            + "</p><p>Environment: "
            + e(data["environment"])
            + ". Total: "
            + e(data["denominator"])
            + "</p><p>"
            + e(data["counts"])
            + "</p><p>Observation window: "
            + e(data["run"]["created"])
            + " – "
            + e(data["run"]["finished"])
            + "</p><p>"
            + e(data["limitations"])
            + "</p><p>Sharing mode: "
            + e(sharing)
            + "</p><table><tr><th>Source</th><th>Target</th><th>Expected HTTP</th><th>Observed HTTP</th><th>Result</th><th>Issues</th><th>Exception</th></tr>"
            + rows
            + "</table></html>",
            "text/html",
        )
    raise ValueError("Unknown report format")
