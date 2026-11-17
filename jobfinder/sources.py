"""Job sources. Each fetcher uses an official/public JSON endpoint and returns list[Job].

Only read-only GET requests are made. Nothing is ever submitted to any site (no auto-apply).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Job

log = logging.getLogger(__name__)


class Http:
    def __init__(self, user_agent: str, timeout: float = 20, delay: float = 0.5):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        retry = Retry(total=2, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.timeout = timeout
        self.delay = delay

    def get_json(self, url: str, params: dict | None = None) -> Any:
        time.sleep(self.delay)  # be polite with free public APIs
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()


def _ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, OSError):
        return None


def _num(value: Any) -> float | None:
    try:
        f = float(value)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _join(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value or "")


# ---------------------------------------------------------------- job boards

def remotive(http: Http, opts: dict) -> list[Job]:
    jobs = []
    for category in opts.get("categories", ["data"]):
        data = http.get_json("https://remotive.com/api/remote-jobs", {"category": category})
        for j in data.get("jobs", []):
            jobs.append(Job(
                source="remotive", title=j.get("title", ""), company=j.get("company_name", ""),
                url=j.get("url", ""), location=j.get("candidate_required_location", ""),
                description=j.get("description", ""), published_at=_ts(j.get("publication_date")),
                salary_text=j.get("salary") or "", remote=True,
            ))
    return jobs


def jobicy(http: Http, opts: dict) -> list[Job]:
    jobs = []
    for geo in opts.get("geos", [None]):
        params = {"count": 100, "industry": opts.get("industry", "data-science")}
        if geo:
            params["geo"] = geo
        data = http.get_json("https://jobicy.com/api/v2/remote-jobs", params)
        for j in data.get("jobs", []):
            jobs.append(Job(
                source="jobicy", title=j.get("jobTitle", ""), company=j.get("companyName", ""),
                url=j.get("url", ""), location=_join(j.get("jobGeo")),
                description=j.get("jobDescription", ""), published_at=_ts(j.get("pubDate")),
                salary_min=_num(j.get("salaryMin")), salary_max=_num(j.get("salaryMax")),
                salary_currency=j.get("salaryCurrency"), salary_period=j.get("salaryPeriod"),
                seniority_hint=_join(j.get("jobLevel")), remote=True,
            ))
    return jobs


def himalayas(http: Http, opts: dict) -> list[Job]:
    jobs = []
    page_size = 20  # the API caps each page at 20 results
    for query in opts.get("queries", ["data"]):
        for page in range(opts.get("pages", 1)):
            data = http.get_json("https://himalayas.app/jobs/api/search",
                                 {"q": query, "limit": page_size, "offset": page * page_size})
            items = data.get("jobs", [])
            for j in items:
                jobs.append(Job(
                    source="himalayas", title=j.get("title", ""), company=j.get("companyName", ""),
                    url=j.get("applicationLink") or j.get("guid", ""),
                    location=_join(j.get("locationRestrictions")) or "Worldwide",
                    description=j.get("description", ""), published_at=_ts(j.get("pubDate")),
                    salary_min=_num(j.get("minSalary")), salary_max=_num(j.get("maxSalary")),
                    salary_currency=j.get("currency"), salary_period=j.get("salaryPeriod"),
                    seniority_hint=_join(j.get("seniority")), remote=True,
                ))
            if len(items) < page_size:
                break
    return jobs


def remoteok(http: Http, opts: dict) -> list[Job]:
    jobs = []
    for tag in opts.get("tags", ["data"]):
        data = http.get_json("https://remoteok.com/api", {"tag": tag})
        for j in data[1:] if isinstance(data, list) else []:  # item 0 is a legal notice
            jobs.append(Job(
                source="remoteok", title=j.get("position", ""), company=j.get("company", ""),
                url=j.get("url", ""), location=j.get("location", ""),
                description=j.get("description", ""), published_at=_ts(j.get("epoch")),
                salary_min=_num(j.get("salary_min")), salary_max=_num(j.get("salary_max")),
                salary_currency="USD", salary_period="year", remote=True,
            ))
    return jobs


# Get on Board seniority ids (public API): 1 no experience, 2 junior, 3 semi senior, 4 senior, 5 expert.
_GOB_SENIORITY = {1: "No experience", 2: "Junior", 3: "Semi Senior", 4: "Senior", 5: "Expert"}


def _gob_job(item: dict) -> Job:
    a = item.get("attributes", {})
    modality = a.get("remote_modality") or ""
    countries = _join(a.get("countries"))
    if modality == "fully_remote":
        location = "Remote LATAM" if not a.get("remote_zone") else f"Remote {a.get('remote_zone')}"
    else:
        location = countries
    company = (((a.get("company") or {}).get("data") or {}).get("attributes") or {}).get("name", "")
    sen_id = (((a.get("seniority") or {}).get("data")) or {}).get("id")
    desc = " ".join(str(a.get(k) or "") for k in ("description", "functions", "desirable", "benefits"))
    return Job(
        source="getonbrd", title=a.get("title", ""), company=company,
        url=(item.get("links") or {}).get("public_url", ""), location=location,
        description=desc, published_at=_ts(a.get("published_at")),
        salary_min=_num(a.get("min_salary")), salary_max=_num(a.get("max_salary")),
        salary_currency="USD", salary_period="month",
        seniority_hint=_GOB_SENIORITY.get(sen_id, ""), language=a.get("lang") or "",
        remote=modality in ("fully_remote", "remote_local"),
    )


def _gob_items(http: Http, url: str, pages: int, extra: dict | None = None):
    for page in range(1, pages + 1):
        items = http.get_json(url, {"per_page": 50, "page": page, "expand": '["company"]', **(extra or {})})
        items = items.get("data", [])
        yield from items
        if len(items) < 50:
            break


def getonbrd(http: Http, opts: dict) -> list[Job]:
    """Categories (paged) plus optional keyword searches, e.g. queries: [trainee, practicante]."""
    jobs: list[Job] = []
    seen: set[str] = set()
    feeds = [(f"https://www.getonbrd.com/api/v0/categories/{c}/jobs", opts.get("pages", 3), None)
             for c in opts.get("categories", ["data-science-analytics"])]
    feeds += [("https://www.getonbrd.com/api/v0/search/jobs", 1, {"query": q}) for q in opts.get("queries", [])]
    for url, pages, extra in feeds:
        for item in _gob_items(http, url, pages, extra):
            job = _gob_job(item)
            key = str(item.get("id") or job.url)
            if key not in seen:  # a keyword search can return postings already read from a category
                seen.add(key)
                jobs.append(job)
    return jobs


# ---------------------------------------------------------------- company ATS portals

def greenhouse(http: Http, opts: dict) -> list[Job]:
    jobs = []
    for slug in opts.get("companies", []):
        data = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", {"content": "true"})
        for j in data.get("jobs", []):
            jobs.append(Job(
                source=f"greenhouse:{slug}", title=j.get("title", ""), company=j.get("company_name") or slug,
                url=j.get("absolute_url", ""), location=(j.get("location") or {}).get("name", ""),
                description=j.get("content", ""), published_at=_ts(j.get("first_published")),
            ))
    return jobs


def lever(http: Http, opts: dict) -> list[Job]:
    jobs = []
    for slug in opts.get("companies", []):
        data = http.get_json(f"https://api.lever.co/v0/postings/{slug}", {"mode": "json"})
        for j in data if isinstance(data, list) else []:
            cats = j.get("categories") or {}
            rng = j.get("salaryRange") or {}
            jobs.append(Job(
                source=f"lever:{slug}", title=j.get("text", ""), company=slug,
                url=j.get("hostedUrl", ""), location=cats.get("location", "") or _join(cats.get("allLocations")),
                description=f"{j.get('descriptionPlain', '')} {j.get('additionalPlain', '')}",
                published_at=_ts((j.get("createdAt") or 0) // 1000 or None),
                salary_min=_num(rng.get("min")), salary_max=_num(rng.get("max")),
                salary_currency=rng.get("currency"),
                salary_period={"per-year-salary": "year", "per-month-salary": "month",
                               "per-hour-wage": "hour"}.get(rng.get("interval", "")),
                remote=(j.get("workplaceType") == "remote") if j.get("workplaceType") else None,
            ))
    return jobs


def ashby(http: Http, opts: dict) -> list[Job]:
    jobs = []
    for slug in opts.get("companies", []):
        data = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", {"includeCompensation": "true"})
        for j in data.get("jobs", []):
            comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
            secondary = ", ".join((s or {}).get("location", "") for s in j.get("secondaryLocations") or [])
            jobs.append(Job(
                source=f"ashby:{slug}", title=j.get("title", ""), company=slug,
                url=j.get("jobUrl", ""), location=", ".join(x for x in (j.get("location"), secondary) if x),
                description=j.get("descriptionPlain", ""), published_at=_ts(j.get("publishedAt")),
                salary_text=comp, remote=j.get("isRemote"),
            ))
    return jobs


def workable(http: Http, opts: dict) -> list[Job]:
    jobs = []
    for slug in opts.get("companies", []):
        data = http.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", {"details": "true"})
        for j in data.get("jobs", []):
            remote = str(j.get("telecommuting")).lower() == "true"
            place = ", ".join(x for x in (j.get("city"), j.get("country")) if x)
            jobs.append(Job(
                source=f"workable:{slug}", title=j.get("title", ""), company=slug,
                url=j.get("shortlink") or j.get("url", ""),
                location=f"Remote, {place}" if remote and place else ("Remote" if remote else place),
                description=j.get("description", ""), published_at=_ts(j.get("published_on")),
                remote=remote,
            ))
    return jobs


# ---------------------------------------------------------------- optional scraper

# ---------------------------------------------------------------- internship lists

_SIMPLIFY_URL = "https://raw.githubusercontent.com/SimplifyJobs/{repo}/dev/.github/scripts/listings.json"


def simplify(http: Http, opts: dict) -> list[Job]:
    """Internship and new-grad lists curated by SimplifyJobs (public JSON behind their GitHub READMEs)."""
    categories = set(opts.get("categories", ["AI/ML/Data"]))
    jobs = []
    for repo in opts.get("lists", ["Summer2027-Internships"]):
        level = "New grad" if "new-grad" in repo.lower() else "Internship"
        for item in http.get_json(_SIMPLIFY_URL.format(repo=repo)) or []:
            if not item.get("active") or not item.get("is_visible", True):
                continue
            if categories and item.get("category") not in categories:
                continue
            locations = [str(loc) for loc in item.get("locations") or []]
            remote = any("remote" in loc.lower() for loc in locations)
            if opts.get("remote_only", True) and not remote:
                continue
            details = {"Terms": item.get("terms"), "Degrees": item.get("degrees"),
                       "Sponsorship": item.get("sponsorship")}
            jobs.append(Job(
                source="simplify", title=item.get("title", ""), company=item.get("company_name", ""),
                url=item.get("url", ""), location=", ".join(locations),
                description=". ".join(f"{k}: {_join(v)}" for k, v in details.items() if v),
                published_at=_ts(item.get("date_posted")), seniority_hint=level, remote=remote,
            ))
    return jobs


def jobspy(http: Http, opts: dict) -> list[Job]:
    """LinkedIn/Indeed via python-jobspy. Disabled by default: scraping may break site ToS."""
    try:
        from jobspy import scrape_jobs  # type: ignore
    except ImportError:
        log.warning("jobspy enabled but python-jobspy is not installed; skipping")
        return []
    jobs = []
    for term in opts.get("search_terms", ["data analyst"]):
        df = scrape_jobs(
            site_name=opts.get("sites", ["indeed"]), search_term=term, location=opts.get("location", "Argentina"),
            is_remote=True, results_wanted=opts.get("results_wanted", 30), hours_old=opts.get("hours_old", 336),
            country_indeed=opts.get("country_indeed", "argentina"),
        )
        for r in df.to_dict("records"):
            jobs.append(Job(
                source=f"jobspy:{r.get('site')}", title=str(r.get("title") or ""), company=str(r.get("company") or ""),
                url=str(r.get("job_url") or ""), location=str(r.get("location") or ""),
                description=str(r.get("description") or ""), published_at=_ts(r.get("date_posted")),
                salary_min=_num(r.get("min_amount")), salary_max=_num(r.get("max_amount")),
                salary_currency=r.get("currency"), salary_period=r.get("interval"), remote=True,
            ))
    return jobs


SOURCES: dict[str, Callable[[Http, dict], list[Job]]] = {
    "remotive": remotive, "jobicy": jobicy, "himalayas": himalayas, "remoteok": remoteok,
    "getonbrd": getonbrd, "greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workable": workable,
    "simplify": simplify, "jobspy": jobspy,
}
