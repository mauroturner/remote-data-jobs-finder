"""Check that a posting is still open before it is listed, so you do not waste time on closed ones.

One read-only GET per posting link. A posting is only dropped when the page clearly says it is gone (404/410, a
"not found" redirect, or a "no longer accepting applications" notice). Anything ambiguous (bot protection, rate
limits, timeouts) is kept as "unknown".
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import requests

from .models import Job

log = logging.getLogger(__name__)

# Redirect targets used by ATSs when a posting no longer exists (Greenhouse: ?error=true, Workable: ?not_found=true).
_GONE_URL_RE = re.compile(r"[?&](error|not_found)=true\b", re.IGNORECASE)
_GONE_TEXT_RE = re.compile(
    r"no longer accepting applications|(position|job|role) has been filled|"
    r"(job|position|posting|offer|role) (is )?no longer available|(this )?(job|posting) has expired|"
    r"ya no est[aá] disponible|oferta (ha )?finalizad[ao]|(oferta|búsqueda) cerrada|vaga encerrada",
    re.IGNORECASE,
)
_MAX_BODY = 300_000  # characters: enough for the notice, avoids downloading huge pages

Fetch = Callable[[str], tuple[int | None, str, str]]


def classify(status: int | None, final_url: str, body: str) -> str:
    """Return "open", "closed" or "unknown" for one fetched posting page."""
    if status in (404, 410):
        return "closed"
    if status is None or status >= 400:
        return "unknown"  # 403/429/5xx: bot protection or rate limits say nothing about the posting
    if _GONE_URL_RE.search(final_url or "") or _GONE_TEXT_RE.search(body or ""):
        return "closed"
    return "open"


def fetch_page(url: str, timeout: float = 10, user_agent: str = "job-finder/0.1") -> tuple[int | None, str, str]:
    """GET a posting page and return (status, final URL after redirects, start of the body)."""
    headers = {"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"}
    with requests.get(url, headers=headers, timeout=timeout, allow_redirects=True, stream=True) as resp:
        body = ""
        if resp.status_code < 400:
            resp.encoding = resp.encoding or "utf-8"
            for chunk in resp.iter_content(chunk_size=32_768, decode_unicode=True):
                body += chunk if isinstance(chunk, str) else chunk.decode("utf-8", "replace")
                if len(body) >= _MAX_BODY:
                    break
        return resp.status_code, resp.url, body


def check_links(jobs: list[Job], opts: dict, fetch: Fetch | None = None, user_agent: str = "job-finder/0.1") -> Counter:
    """Set `link_status` on up to `max_links` postings, in order. Returns how many ended in each status."""
    if not opts.get("enabled", True):
        return Counter()
    timeout = opts.get("timeout", 10)
    if fetch is None:
        def fetch(url: str) -> tuple[int | None, str, str]:
            return fetch_page(url, timeout=timeout, user_agent=user_agent)

    def one(job: Job) -> str:
        if not job.url.startswith(("http://", "https://")):
            return "unknown"
        try:
            return classify(*fetch(job.url))
        except Exception as exc:  # network errors only mean we could not tell
            log.debug("link check failed for %s: %s", job.url, exc)
            return "unknown"

    todo = jobs[: opts.get("max_links", 200)]
    with ThreadPoolExecutor(max_workers=max(1, opts.get("workers", 6))) as pool:
        for job, status in zip(todo, pool.map(one, todo)):
            job.link_status = status
    return Counter(j.link_status for j in todo)
