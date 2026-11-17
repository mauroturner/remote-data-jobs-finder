"""Run-level insights: skill gaps across eligible postings, and which postings are new since the last run."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .models import Job


@dataclass
class SkillGap:
    skill: str
    count: int  # eligible postings that mention it
    share: float  # count / eligible postings


def _covered(name: str, pattern: str, profile_keys: list[str]) -> bool:
    """A catalog skill is covered when a profile key matches its name, or its pattern matches the key."""
    for key in profile_keys:
        try:
            if re.fullmatch(key, name, re.IGNORECASE):
                return True
        except re.error:
            pass
        if re.fullmatch(pattern, key, re.IGNORECASE):
            return True
    return False


def compile_catalog(catalog: dict[str, str]) -> dict[str, re.Pattern]:
    return {name: re.compile(rf"(?<![\w])(?:{pattern})(?![\w])", re.IGNORECASE) for name, pattern in catalog.items()}


def uncovered(catalog: dict[str, str], profile_skills: dict) -> dict[str, re.Pattern]:
    """Compiled catalog entries that no profile skill covers."""
    keys = list(profile_skills)
    return compile_catalog({n: p for n, p in catalog.items() if not _covered(n, p, keys)})


def mentioned(job: Job, compiled: dict[str, re.Pattern]) -> list[str]:
    """Catalog skill names the posting mentions, in catalog order."""
    text = f"{job.title} {job.description}"
    return [name for name, regex in compiled.items() if regex.search(text)]


def annotate_missing(jobs: list[Job], catalog: dict[str, str], profile_skills: dict) -> None:
    """Set `missing_skills` on each job: what it asks for that the profile does not list."""
    gaps = uncovered(catalog, profile_skills)
    for job in jobs:
        job.missing_skills = mentioned(job, gaps)


def skill_gaps(jobs: list[Job], catalog: dict[str, str], profile_skills: dict, min_count: int = 1) -> list[SkillGap]:
    """Catalog skills that eligible postings ask for and the profile does not list, most demanded first."""
    unique: dict[tuple[str, str], Job] = {}
    for job in jobs:
        if job.eligible:
            unique.setdefault(job.dedup_key, job)
    if not unique:
        return []
    counts: dict[str, int] = {}
    gaps_re = uncovered(catalog, profile_skills)
    for job in unique.values():
        for name in mentioned(job, gaps_re):
            counts[name] = counts.get(name, 0) + 1
    gaps = [SkillGap(name, c, c / len(unique)) for name, c in counts.items() if c >= min_count]
    return sorted(gaps, key=lambda g: (-g.count, g.skill))


def _key(job: Job) -> str:
    return "|".join(job.dedup_key)


def mark_new(jobs: list[Job], store: Path, today: date | None = None, retention_days: int = 120) -> None:
    """Set `is_new` on postings not seen in previous runs, then record them all in `store`.

    The store maps posting keys to the last date they were seen; entries older than `retention_days` are dropped.
    """
    today = today or date.today()
    seen: dict[str, str] = {}
    if store.exists():
        try:
            seen = json.loads(store.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            seen = {}  # a corrupt store only means everything looks new once
    cutoff = today - timedelta(days=retention_days)
    seen = {k: v for k, v in seen.items() if date.fromisoformat(v) >= cutoff}
    for job in jobs:
        job.is_new = _key(job) not in seen
    for job in jobs:
        seen[_key(job)] = today.isoformat()
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(seen, indent=0, sort_keys=True), encoding="utf-8")
