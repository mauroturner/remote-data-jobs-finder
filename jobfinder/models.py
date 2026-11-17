"""Domain model shared by sources, scoring and reporting."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Job:
    source: str
    title: str
    company: str
    url: str
    location: str = ""
    description: str = ""
    published_at: datetime | None = None
    # Raw salary as provided by the source (any currency/period).
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None  # "year" | "month" | "hour" | None (unknown)
    salary_text: str = ""  # free-text salary, parsed when min/max are missing
    seniority_hint: str = ""  # seniority label given by the source, if any
    language: str = ""  # ISO code given by the source, if any ("es", "en")
    remote: bool | None = None

    # Filled by scoring.
    score: float = 0.0
    monthly_usd_min: float | None = None
    monthly_usd_max: float | None = None
    flags: list[str] = field(default_factory=list)
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)  # catalog skills it asks for that the profile lacks
    reject_reason: str = ""  # first failed filter; empty means the posting is a match
    reasons: list[str] = field(default_factory=list)  # every failed filter, in evaluation order
    near_miss: bool = False  # failed exactly one soft filter by a small margin
    eligible: bool = False  # data role in an eligible location (used for skill-gap statistics)
    is_new: bool = False  # not seen in previous runs
    link_status: str = ""  # "open" | "closed" | "unknown" after the link check; "" = not checked

    @property
    def dedup_key(self) -> tuple[str, str]:
        """Same posting across sources: normalized company and title."""
        return normalize_company(self.company), normalize_title(self.title)


_COMPANY_SUFFIXES = {"inc", "llc", "ltd", "corp", "co", "sa", "srl", "gmbh", "labs", "group", "hq", "the"}
_TITLE_NOISE = {"remote", "remoto", "100"}


def _fold(text: str) -> str:
    """Lowercase and strip accents, keeping non-Latin scripts intact."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def normalize_company(name: str) -> str:
    """'Mutt Data', 'muttdata' and 'MuttData Inc.' -> 'muttdata'; 'Grafana Labs' and 'grafanalabs' -> 'grafana'."""
    # Dots are dropped first so "S.A." and "Inc." become the suffix tokens "sa" and "inc".
    tokens = [t for t in re.split(r"[\W_]+", _fold(name).replace(".", "")) if t and t not in _COMPANY_SUFFIXES]
    joined = "".join(tokens)
    for suffix in ("labs", "inc", "hq"):  # slugs glue the suffix on: "grafanalabs"
        if joined.endswith(suffix) and len(joined) > len(suffix) + 2:
            joined = joined[: -len(suffix)]
    return joined


def normalize_title(title: str) -> str:
    """'Data Engineer (Remote)' and 'Data engineer - remote' -> 'data engineer'."""
    words = re.split(r"[^\w+#]+", _fold(title))
    return " ".join(w for w in words if w and w not in _TITLE_NOISE)
