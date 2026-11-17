"""Domain model shared by sources, scoring and reporting."""
from __future__ import annotations

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
    reject_reason: str = ""

    @property
    def dedup_key(self) -> tuple[str, str]:
        norm = lambda s: " ".join(s.lower().split())  # noqa: E731
        return norm(self.company), norm(self.title)
