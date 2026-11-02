"""Bounded, direct, connection-pinned HTTP evidence transport. Never uses proxies."""

import hashlib
import http.client
import ipaddress
import queue
import re
import socket
import ssl
import threading
import time
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

from .domain import now, origin, url

MAX_RESPONSE = 1024 * 1024
DNS_SLOTS = threading.BoundedSemaphore(2)


class Metadata(HTMLParser):
    # Derived from LinkWitness _MetadataParser (MIT); see THIRD_PARTY.md.
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title = ""
        self.canonicals = []
        self.robots = []
        self.login_form = False
        self.metadata_truncated = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and (values.get("type") or "").lower() == "password":
            self.login_form = True
        if tag == "title":
            self.in_title = True
        if tag == "link" and "canonical" in (values.get("rel") or "").lower().split():
            value = values.get("href") or ""
            if len(self.canonicals) >= 32 or len(value) > 8192:
                self.metadata_truncated = True
            else:
                self.canonicals.append(value)
        if tag == "meta" and (values.get("name") or "").lower() in ("robots", "googlebot"):
            value = values.get("content") or ""
            if len(self.robots) >= 32 or len(value) > 8192:
                self.metadata_truncated = True
            else:
                self.robots.append(value)

    def handle_data(self, data):
        if self.in_title:
            remaining = max(0, 8192 - len(self.title))
            self.title += data[:remaining]
            if len(data) > remaining:
                self.metadata_truncated = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False


def addresses(host, port, allowlist):
    if not DNS_SLOTS.acquire(blocking=False):
        raise ValueError("DNS capacity unavailable; retry later")
    results = queue.Queue(maxsize=1)

    def resolve():
        try:
            results.put((True, socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
        except OSError as exc:
            results.put((False, exc))
        finally:
            DNS_SLOTS.release()

    threading.Thread(target=resolve, daemon=True).start()
    try:
        success, answers = results.get(timeout=5)
    except queue.Empty as exc:
        raise ValueError("DNS time budget exceeded") from exc
    if not success:
        raise ValueError("DNS lookup failed") from answers
    if not answers:
        raise ValueError("DNS returned no addresses")
    if len(answers) > 16:
        raise ValueError("DNS answer budget exceeded")
    result = []
    for family, typ, proto, cname, address in answers:
        ip = ipaddress.ip_address(address[0])
        if (not ip.is_global or ip.is_multicast) and str(ip) not in allowlist:
            raise ValueError("Private/reserved target blocked; add only an approved exact staging IP")
        result.append((family, typ, proto, address))
    return result


def request(target, allowed_origins, allowlist=(), timeout=10, limit=MAX_RESPONSE):
    normalized = url(target)
    p = urlsplit(normalized)
    if origin(normalized) not in allowed_origins:
        raise ValueError("Target origin is outside project scope")
    port = p.port or (443 if p.scheme == "https" else 80)
    checked = addresses(p.hostname, port, allowlist)
    sock = None
    last_error = None
    connect_started = time.monotonic()
    for family, typ, proto, address in checked:
        try:
            remaining = 15 - (time.monotonic() - connect_started)
            if remaining <= 0:
                raise OSError("Connection time budget exceeded")
            sock = socket.socket(family, typ, proto)
            sock.settimeout(min(timeout, remaining))
            sock.connect(address)  # No second name resolution.
            if p.scheme == "https":
                context = ssl.create_default_context()
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                sock = context.wrap_socket(sock, server_hostname=p.hostname)
            break
        except OSError as exc:
            last_error = exc
            if sock:
                sock.close()
            sock = None
    if sock is None:
        raise OSError("Connection failed") from last_error
    connection = http.client.HTTPConnection(p.hostname, port, timeout=timeout)
    connection.sock = sock

    timed_out = threading.Event()

    def interrupt():
        timed_out.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    deadline = threading.Timer(15, interrupt)
    deadline.daemon = True
    deadline.start()
    try:
        path = quote(p.path or "/", safe="/%:@!$&'()*+,;=-._~")
        if p.query:
            path += "?" + quote(p.query, safe="%/:?@!$&'()*+,;=-._~")
        connection.request(
            "GET",
            path,
            headers={
                "Host": p.netloc,
                "User-Agent": "SiteMigrationWorkbench/0.1.0",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        headers = {}
        for key, value in response.getheaders():
            key = key.lower()
            headers[key] = headers.get(key, "") + (", " if key in headers else "") + value
        body = response.read(limit + 1)
        if timed_out.is_set():
            raise OSError("Response deadline exceeded")
        truncated = len(body) > limit
        length = response.getheader("Content-Length")
        if length and not truncated and len(body) != int(length):
            raise ValueError("Interrupted or inconsistent response length")
        return response.status, headers, body[:limit], truncated
    finally:
        deadline.cancel()
        connection.close()


def capture(target, config, stop=lambda: False, transport=request):
    evidence = {"requested_url": target, "observed_at": now(), "source": "network", "chain": []}
    try:
        seen = set()
        current = url(target)
        scope = {config["production"], config["staging"]} | set(config.get("additional_origins", []))
        started = time.monotonic()
        for hop in range(11):
            if stop():
                raise ValueError("Check interrupted by operator")
            if time.monotonic() - started > 60:
                raise ValueError("Total URL check budget exceeded")
            if current in seen:
                raise ValueError("Redirect loop")
            seen.add(current)
            for attempt in range(3):
                status, headers, body, truncated = transport(current, scope, config["private_allowlist"])
                if status not in (429, 503) or attempt == 2:
                    break
                retry = headers.get("retry-after", "1")
                delay = min(int(retry) if retry.isdigit() else 2**attempt, 5)
                for _ in range(delay * 10):
                    if stop():
                        raise ValueError("Check interrupted during backoff")
                    time.sleep(0.1)
            evidence["chain"].append(
                {
                    "url": current,
                    "status": status,
                    "headers": {
                        k: headers[k]
                        for k in (
                            "location",
                            "content-type",
                            "x-robots-tag",
                            "etag",
                            "last-modified",
                            "retry-after",
                        )
                        if k in headers
                    },
                    "truncated": truncated,
                }
            )
            if status in (301, 302, 303, 307, 308):
                if hop == 10:
                    raise ValueError("Redirect hop limit exceeded")
                if not headers.get("location"):
                    raise ValueError("Redirect has no destination")
                next_url = url(urljoin(current, headers["location"]))
                if urlsplit(current).scheme == "https" and urlsplit(next_url).scheme == "http":
                    raise ValueError("HTTPS downgrade")
                current = next_url
                if origin(current) not in scope:
                    raise ValueError("Redirect leaves approved site scope")
                continue
            parser = Metadata()
            if "html" in headers.get("content-type", "").lower():
                from email.message import Message

                message = Message()
                message["content-type"] = headers.get("content-type", "")
                charset = message.get_content_charset() or "utf-8"
                try:
                    parser.feed(body.decode(charset, "replace"))
                except LookupError:
                    parser.feed(body.decode("utf-8", "replace"))
            evidence.update(
                final_url=current,
                status=status,
                truncated=truncated
                or parser.metadata_truncated
                or any(hop["truncated"] for hop in evidence["chain"]),
                bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
                title=parser.title.strip(),
                canonicals=[urljoin(current, c) for c in parser.canonicals],
                robots=parser.robots + [headers.get("x-robots-tag", "")],
                login_form=parser.login_form,
                robots_txt="not checked; only page/header directives observed",
            )
            break
    except (OSError, ValueError, http.client.HTTPException) as exc:
        evidence["error"] = str(exc)
    return evidence


def substitute(target, config, environment):
    if environment == "production":
        return target
    p = urlsplit(target)
    if origin(target) == config["production"]:
        return config["staging"] + p.path + ("?" + p.query if p.query else "")
    return target


def compare(row, evidence, config, environment):
    issues = []
    level = "pass"
    if evidence.get("error") in ("Redirect loop", "Redirect hop limit exceeded", "HTTPS downgrade"):
        level = "fail"
        issues.append(evidence["error"])
    elif (
        evidence.get("error")
        or evidence.get("truncated")
        or evidence.get("login_form")
        or evidence.get("status") in (401, 403, 429, 503)
    ):
        level = "inconclusive"
        issues.append(evidence.get("error") or "Response incomplete, authenticated, blocked or unavailable")
    else:
        chain = evidence.get("chain", [])
        expected = substitute(
            row["target"] if row["disposition"] in ("redirect", "merge") else row["url"], config, environment
        )
        if row["disposition"] == "retire":
            if evidence["status"] != row["status"] or len(chain) != 1:
                issues.append("Retirement must return its declared status directly")
        else:
            if evidence["status"] != 200:
                issues.append("Destination did not return 200")
            if url(evidence["final_url"]) != url(expected):
                issues.append("Wrong destination")
            if row["disposition"] == "keep" and len(chain) != 1:
                issues.append("Keep unexpectedly redirects")
            if row["disposition"] in ("redirect", "merge"):
                if len(chain) < 2 or chain[0]["status"] != row["status"]:
                    issues.append("Missing or incorrect permanent redirect")
                if len(chain) > 2:
                    issues.append("Redirect chain must be flattened")
            if any(x["status"] in (302, 303, 307) for x in chain):
                issues.append("Unexpected temporary redirect")
            if any(
                urlsplit(a["url"]).scheme == "https" and urlsplit(b["url"]).scheme == "http"
                for a, b in zip(chain, chain[1:])
            ):
                issues.append("HTTPS downgrade")
            canonical_values = evidence.get("canonicals", [])
            try:
                canonical_conflict = len(canonical_values) > 1 or any(
                    url(c) != url(expected) for c in canonical_values
                )
            except ValueError:
                canonical_conflict = True
            if canonical_conflict:
                issues.append("Canonical conflict")
            blocking = any(
                any(token in re.split(r"[\s,;]+", directive.lower()) for token in ("noindex", "none"))
                for directive in evidence.get("robots", [])
            )
            if blocking and not (environment == "staging" and config["staging_noindex"]):
                issues.append("Unexpected noindex directive")
        if issues:
            level = "fail"
        elif row["disposition"] != "retire" and not evidence.get("canonicals"):
            level = "warning"
            issues.append("No canonical declared; review")
    return {
        **evidence,
        "result": level,
        "issues": issues,
        "exception": row["exception"],
        "exception_is_pass": False,
    }
