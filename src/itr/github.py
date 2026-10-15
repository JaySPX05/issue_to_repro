"""GitHub issue fetching (read-only). Uses only the standard library."""
from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from itr.retry import retry_call

ISSUE_RE = re.compile(r"^https?://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)/?(?:[#?].*)?$")
API = "https://api.github.com"


class IssueURLError(ValueError):
    pass


def parse_issue_url(url: str) -> tuple[str, str, int]:
    m = ISSUE_RE.match(url.strip())
    if not m:
        raise IssueURLError(f"not a GitHub issue URL: {url!r}")
    return m.group(1), m.group(2), int(m.group(3))


def _http_get(url: str, token: str | None = None):
    return retry_call(_http_get_once, url, token)      # a dropped connection must not end the run (D62)


def _http_get_once(url: str, token: str | None = None):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "issue-to-repro"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_ts(text: str) -> datetime:
    """Parse an ISO-8601 timestamp (GitHub's 'Z' suffix or a +HH:MM offset) into an aware datetime."""
    dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch_issue(url: str, token: str | None = None,
                http_get: Callable[..., object] = _http_get, as_of: str | None = None) -> dict:
    """Return {url, repo, number, title, body, comments, ref}.

    `ref` is the repo's default branch here. For evaluation on closed issues the eval harness sets
    `ref` (the commit just before the fix) on the issue first, and FETCH keeps it.

    `as_of` (ISO timestamp, normally the fix commit's date) drops comments written after it, so an
    evaluation run cannot read the maintainers' explanation of the fix."""
    token = token or os.environ.get("GITHUB_TOKEN")
    owner, repo, number = parse_issue_url(url)
    issue = http_get(f"{API}/repos/{owner}/{repo}/issues/{number}", token)
    comments = http_get(f"{API}/repos/{owner}/{repo}/issues/{number}/comments?per_page=30", token)
    meta = http_get(f"{API}/repos/{owner}/{repo}", token)
    cutoff = parse_ts(as_of) if as_of else None
    kept = [c for c in comments
            if cutoff is None or not c.get("created_at") or parse_ts(c["created_at"]) <= cutoff]
    return {
        "url": url,
        "repo": f"{owner}/{repo}",
        "number": number,
        "title": issue.get("title", ""),
        "body": issue.get("body") or "",
        "comments": [{"author": (c.get("user") or {}).get("login", ""), "body": c.get("body") or ""}
                     for c in kept],
        "ref": meta.get("default_branch", "main"),
    }
