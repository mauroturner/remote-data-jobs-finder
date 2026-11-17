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


def evaluate(job: Job, cfg: dict[str, Any], now: datetime | None = None) -> Job:
    """Fill score/flags/reject_reason on the job. A non-empty reject_reason means filtered out."""
    filters = cfg["filters"]
    title = job.title or ""
    desc = job.description = strip_html(job.description)
    location = (job.location or "").strip()
    flags: list[str] = []

    # 1. Domain: must be a data role.
    if not _any_match(filters["title_include"], title):
        job.reject_reason = "not a data role"
        return job
    if hit := _any_match(filters.get("title_exclude", []), title):
        job.reject_reason = f"title excluded ({hit})"
        return job

    # 2. Seniority.
    sen = filters["seniority"]
    if hit := _any_match(sen["reject_title"], title):
        job.reject_reason = f"seniority in title ({hit})"
        return job
    if job.seniority_hint and _any_match(sen.get("reject_hint", []), job.seniority_hint):
        job.reject_reason = f"seniority from source ({job.seniority_hint})"
        return job
    years = required_years(desc)
    if years is not None and years > sen["max_years_required"]:
        job.reject_reason = f"requires {years}+ years"
        return job
    seniority_fit = bool(
        _any_match(sen["accept"], title) or (job.seniority_hint and _any_match(sen["accept"], job.seniority_hint))
    )

    # 3. Location / remote.
    loc = filters["location"]
    if loc.get("require_remote", True) and job.remote is False:
        job.reject_reason = "not remote"
        return job
    blob = f"{location} {desc}"
    region_ok = _any_match(loc["allowed"], location)
    if hit := _any_match(loc["blocked_phrases"], blob):
        if not region_ok:
            job.reject_reason = f"location restricted ({hit})"
            return job
    if location and not region_ok and not _any_match(loc.get("neutral", []), location):
        job.reject_reason = f"location not eligible ({location[:60]})"
        return job
    if region_ok and _any_match(loc.get("preferred", []), location):
        flags.append("latam-friendly")

    # 4. Freshness.
    if is_too_old(job, filters.get("max_age_days"), now):
        job.reject_reason = "too old"
        return job

    # 5. Salary.
    sal = filters["salary"]
    job.monthly_usd_min, job.monthly_usd_max = monthly_range(job, sal)
    salary_points = 0.0
    if job.monthly_usd_max is None:
        if not sal.get("keep_unknown", True):
            job.reject_reason = "salary unknown"
            return job
        flags.append("salary-unknown")
        salary_points = 0.5
    elif job.monthly_usd_max < sal["min_monthly_usd"]:
        job.reject_reason = f"salary below floor (~{job.monthly_usd_max:,.0f} USD/month)"
        return job
    else:
        salary_points = 1.0
        if job.monthly_usd_max > sal["target_max_monthly_usd"]:
            flags.append("ask-for-more")

    # 6. Language.
    lang = filters["language"]
    spanish = looks_spanish(job)
    demanding = _any_match(lang["english_demanding"], desc)
    if spanish:
        flags.append("spanish")
        language_points = 1.0
    elif demanding:
        if lang.get("reject_english_demanding", False):
            job.reject_reason = "requires advanced English"
            return job
        flags.append("english-demanding")
        language_points = 0.2
    else:
        language_points = 0.7

    # 7. Profile match.
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
    return job
