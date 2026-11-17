"""Pure filtering and scoring rules. No I/O here, so everything is unit-testable."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any

from .models import Job

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    # Some sources double-escape HTML (&lt;p&gt;), so unescape before stripping tags.
    text = html.unescape(html.unescape(text or ""))
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text)).strip()


def _any_match(patterns: list[str], text: str) -> str | None:
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return pattern
    return None


# ---------------------------------------------------------------- salary

_PERIOD_ALIASES = {
    "year": "year", "yearly": "year", "annual": "year", "annually": "year", "yr": "year",
    "month": "month", "monthly": "month", "mo": "month",
    "hour": "hour", "hourly": "hour", "hr": "hour",
}
_MONEY_RE = re.compile(
    r"(?P<cur>US\$|USD|EUR|GBP|CAD|\$|€|£)?\s*(?P<num>\d{1,3}(?:[.,]\d{3})+|\d+(?:\.\d+)?)\s*(?P<k>[kK])?"
)
_CURRENCY_SYMBOLS = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP"}


def normalize_period(period: str | None) -> str | None:
    if not period:
        return None
    return _PERIOD_ALIASES.get(period.strip().lower())


def parse_salary_text(text: str) -> tuple[float | None, float | None, str | None, str | None]:
    """Extract (min, max, currency, period) from free text like '$170k - $200k' or '$87,069-$124,385/yr'."""
    if not text:
        return None, None, None, None
    values: list[float] = []
    currency: str | None = None
    prev_end = -1
    for m in _MONEY_RE.finditer(text):
        cur, num, k = m.group("cur"), m.group("num"), m.group("k")
        # Bare numbers are ambiguous (years, team sizes...) unless they close a range: "USD 3000 - 4000".
        continues_range = values and re.fullmatch(r"\s*(-|–|to|a)\s*", text[prev_end:m.start()], re.IGNORECASE)
        if not cur and not k and not continues_range:
            continue
        prev_end = m.end()
        value = float(re.sub(r"[.,](?=\d{3}\b)", "", num).replace(",", ""))
        if k:
            value *= 1000
        values.append(value)
        if cur and not currency:
            currency = _CURRENCY_SYMBOLS.get(cur, cur.upper())
    if not values:
        return None, None, None, None
    period = None
    lowered = text.lower()
    for token, norm in (("hour", "hour"), ("/hr", "hour"), ("month", "month"), ("/mo", "month"),
                        ("year", "year"), ("/yr", "year"), ("annual", "year")):
        if token in lowered:
            period = norm
            break
    return min(values), max(values), currency or "USD", period


def _guess_period(value: float) -> str:
    if value <= 300:
        return "hour"
    if value >= 20000:
        return "year"
    return "month"


def to_monthly_usd(value: float | None, currency: str | None, period: str | None, cfg: dict) -> float | None:
    if value is None or value <= 0:
        return None
    rates: dict[str, float] = cfg.get("fx_to_usd", {"USD": 1.0})
    rate = rates.get((currency or "USD").upper())
    if rate is None:
        return None  # unknown currency: treat as unknown salary instead of guessing
    period = normalize_period(period) or _guess_period(value)
    usd = value * rate
    if period == "year":
        return usd / 12
    if period == "hour":
        return usd * cfg.get("hours_per_month", 160)
    return usd


def monthly_range(job: Job, cfg: dict) -> tuple[float | None, float | None]:
    lo, hi, cur, period = job.salary_min, job.salary_max, job.salary_currency, job.salary_period
    if lo is None and hi is None and job.salary_text:
        lo, hi, cur, period = parse_salary_text(job.salary_text)
    lo_m = to_monthly_usd(lo, cur, period, cfg)
    hi_m = to_monthly_usd(hi, cur, period, cfg)
    if lo_m is None:
        lo_m = hi_m
    if hi_m is None:
        hi_m = lo_m
    return lo_m, hi_m


# ---------------------------------------------------------------- rules

_YEARS_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years?|yrs?|años)\b[^.]{0,60}?(?:experience|experiencia)",
    re.IGNORECASE,
)


def required_years(text: str) -> int | None:
    found = [int(m.group(1)) for m in _YEARS_RE.finditer(text)]
    found = [y for y in found if 0 < y <= 20]
    return min(found) if found else None


def looks_spanish(job: Job) -> bool:
    if job.language:
        return job.language.lower().startswith("es")
    words = re.findall(r"[a-záéíóúñ]+", (job.title + " " + job.description[:1500]).lower())
    if not words:
        return False
    es = {"de", "la", "el", "y", "en", "para", "con", "los", "las", "del", "experiencia", "datos", "trabajo"}
    return sum(w in es for w in words) / len(words) > 0.08


def is_too_old(job: Job, max_age_days: int | None, now: datetime | None = None) -> bool:
    if not max_age_days or not job.published_at:
        return False
    now = now or datetime.now(timezone.utc)
    published = job.published_at if job.published_at.tzinfo else job.published_at.replace(tzinfo=timezone.utc)
    return (now - published).days > max_age_days


_PLAIN_SENIOR_RE = re.compile(r"^\s*(senior|sr\.?)\b", re.IGNORECASE)


def _search(patterns: list[str], text: str) -> tuple[str, str] | None:
    """Return (pattern, matched text) for the first matching pattern."""
    for pattern in patterns:
        if m := re.search(pattern, text, re.IGNORECASE):
            return pattern, m.group(0)
    return None


def evaluate(job: Job, cfg: dict[str, Any], now: datetime | None = None) -> Job:
    """Score the job and record every failed filter.

    `reject_reason` is the first failed filter (empty for a match), `reasons` lists all of them, and
    `near_miss` marks postings that failed a single soft filter by a small margin.
    """
    filters = cfg["filters"]
    near = cfg.get("near_miss", {})
    title = job.title or ""
    desc = job.description = strip_html(job.description)
    location = (job.location or "").strip()
    flags: list[str] = []
    failed: list[tuple[str, bool]] = []  # (reason, is_soft)

    def fail(reason: str, soft: bool = False) -> None:
        failed.append((reason, soft))

    # 1. Domain: must be a data role. Nothing else matters otherwise.
    if not _any_match(filters["title_include"], title):
        return _finish(job, [("not a data role", False)])
    if hit := _any_match(filters.get("title_exclude", []), title):
        return _finish(job, [(f"title excluded ({hit})", False)])

    # 2. Seniority. A plain "Senior" (not lead/staff/manager...) or a few extra years is a soft failure.
    sen = filters["seniority"]
    soft_senior = near.get("include_senior_titles", True)
    if found := _search(sen["reject_title"], title):
        fail(f"seniority in title ({found[1]})", soft_senior and bool(_PLAIN_SENIOR_RE.match(found[1])))
    elif job.seniority_hint and _any_match(sen.get("reject_hint", []), job.seniority_hint):
        fail(f"seniority from source ({job.seniority_hint})",
             soft_senior and bool(_PLAIN_SENIOR_RE.match(job.seniority_hint)))
    years = required_years(desc)
    if years is not None and years > sen["max_years_required"]:
        fail(f"requires {years}+ years", years <= sen["max_years_required"] + near.get("max_extra_years", 2))
    seniority_fit = bool(
        _any_match(sen["accept"], title) or (job.seniority_hint and _any_match(sen["accept"], job.seniority_hint))
    )

    # 3. Location / remote (hard).
    loc = filters["location"]
    region_ok = _any_match(loc["allowed"], location)
    blocked = _any_match(loc["blocked_phrases"], f"{location} {desc}")
    if loc.get("require_remote", True) and job.remote is False:
        fail("not remote")
    elif blocked and not region_ok:
        fail(f"location restricted ({blocked})")
    elif location and not region_ok and not _any_match(loc.get("neutral", []), location):
        fail(f"location not eligible ({location[:60]})")
    # Eligible = a data role you could legally take; used for skill-demand statistics.
    job.eligible = not any(r.startswith(("not remote", "location")) for r, _ in failed)
    if region_ok and _any_match(loc.get("preferred", []), location):
        flags.append("latam-friendly")

    # 4. Freshness (hard).
    if is_too_old(job, filters.get("max_age_days"), now):
        fail("too old")

    # 5. Salary. Slightly below the floor is a soft failure.
    sal = filters["salary"]
    job.monthly_usd_min, job.monthly_usd_max = monthly_range(job, sal)
    salary_points = 0.0
    if job.monthly_usd_max is None:
        if not sal.get("keep_unknown", True):
            fail("salary unknown", True)
        flags.append("salary-unknown")
        salary_points = 0.5
    elif job.monthly_usd_max < sal["min_monthly_usd"]:
        tolerance = near.get("salary_tolerance", 0.15)
        fail(f"salary below floor (~{job.monthly_usd_max:,.0f} USD/month)",
             job.monthly_usd_max >= sal["min_monthly_usd"] * (1 - tolerance))
    else:
        salary_points = 1.0
        if job.monthly_usd_max > sal["target_max_monthly_usd"]:
            flags.append("ask-for-more")

    # 6. Language (soft).
    lang = filters["language"]
    spanish = looks_spanish(job)
    demanding = _any_match(lang["english_demanding"], desc)
    if spanish:
        flags.append("spanish")
        language_points = 1.0
    elif demanding:
        if lang.get("reject_english_demanding", False):
            fail("requires advanced English", True)
        flags.append("english-demanding")
        language_points = 0.2
    else:
        language_points = 0.7

    # 7. Profile match. Computed for every data role so near misses can be ranked too.
    skills: dict[str, float] = cfg["profile"]["skills"]
    text = f"{title} {desc}"
    # Skill keys are regex fragments (e.g. 'llms?'), matched on word boundaries.
    matched = [s for s in skills if re.search(rf"(?<![\w])(?:{s})(?![\w])", text, re.IGNORECASE)]
    total = sum(skills.values()) or 1
    # A posting rarely lists every skill; saturate at a fraction of the full profile.
    saturation = cfg["profile"].get("match_saturation", 0.35)
    skill_points = min(1.0, sum(skills[s] for s in matched) / (total * saturation))

    w = cfg["scoring"]
    job.score = round(
        100
        * (
            w["skills"] * skill_points
            + w["seniority"] * (1.0 if seniority_fit else 0.5)
            + w["salary"] * salary_points
            + w["location"] * (1.0 if "latam-friendly" in flags else 0.6)
            + w["language"] * language_points
        )
        / sum(w.values()),
        1,
    )
    if seniority_fit:
        flags.append("junior/mid")
    job.flags = flags
    job.matched_skills = matched
    return _finish(job, failed)


def _finish(job: Job, failed: list[tuple[str, bool]]) -> Job:
    job.reasons = [reason for reason, _ in failed]
    job.reject_reason = job.reasons[0] if job.reasons else ""
    job.near_miss = len(failed) == 1 and failed[0][1]
    return job
