"""Market snapshot of remote data jobs: postings by seniority, by role, and the skills each role asks for.

Independent of your profile: it classifies every remote data posting the sources return.
Usage: python -m jobfinder.market [--config config.yaml] [--min-share 0.4]
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .insights import compile_catalog, mentioned
from .models import Job
from .scoring import strip_html

log = logging.getLogger("jobfinder.market")

# Order matters: the first matching level wins ("Senior Manager" is a manager, "Semi Senior" is mid).
SENIORITY: list[tuple[str, re.Pattern]] = [
    ("Intern / Trainee", re.compile(r"(?i:\bintern(ship)?\b|pasant|trainee|aprendiz|practicante)")),
    ("Lead / Staff / Manager", re.compile(r"(?i:\blead\b|\bstaff\b|principal|\bhead\b|director|manager|\bvp\b|"
                                          r"chief|architect|arquitect|\bjefe\b|gerente|expert)")),
    ("Mid", re.compile(r"(?i:semi.?senior|\bssr\b|\bmid\b|mid.?level|intermediate)|\bII\b")),
    ("Senior", re.compile(r"(?i:senior|\bsr\b)|\bIII\b")),
    # Roman numerals stay case-sensitive so the English word "I" does not count as level I.
    ("Junior", re.compile(r"(?i:junior|\bjr\b|entry|graduate|no experience|sin experiencia)|\bI\b")),
]
# Display order, from least to most senior. (SENIORITY above is the matching order.)
SENIORITY_ORDER = ["Intern / Trainee", "Junior", "Mid", "Senior", "Lead / Staff / Manager", "Not stated"]

# Titles that mention data/AI but are not data roles (sales, product, data labeling gigs, data centers...).
_NOT_DATA = re.compile(
    r"sales|account (executive|manager)|business development|product manager|recruit|marketing manager|"
    r"\btrainer\b|annotat|data collection|freelance|\bceo\b|\bcto\b|full.?stack|front.?end|back.?end|"
    r"data cent(er|re)|civil|electrical|mechanical|enablement|transformation|solutions architect|"
    r"\bai tooling\b|\bai gateway\b",
    re.IGNORECASE,
)

# Order matters: more specific families first.
ROLES: list[tuple[str, str]] = [
    ("Analytics Engineer", r"analytics engineer"),
    ("ML Engineer / MLOps", r"machine learning (\(ml\) )?(engineer|specialist)|\bml engineer|mlops|ml ops|"
                            r"ml platform|ingenier[oa]\S* (de )?machine learning|ingenier[oa]\S* ml\b"),
    ("AI / LLM Engineer", r"\b(ai|ia|llm|genai|gen ai)\b[^,|]{0,20}\b(engineer|developer|ingenier)|"
                          r"\bingenier[oa]\S* (de )?(ia|ai|llm)\b|ai/ml engineer|forward deployed ai"),
    ("Data Scientist", r"scientist|cient[ií]fic[oa] de datos|cientista|data science"),
    ("Data Architect", r"data architect|arquitect[oa]\S* de datos|arquitecto de datos"),
    ("Data Engineer", r"data engineer|ingenier[oa]\S* de datos|big ?data|data platform|etl developer|"
                      r"data (layer |pipeline |api )?(developer|engineer)|datasphere"),
    ("BI Analyst / Developer", r"\bbi\b|business intelligence|power bi|looker|tableau"),
    ("Data Analyst", r"\banalyst\b|analista|analytics"),
    ("Data Governance / Quality", r"governance|data quality|gobierno de datos|calidad de datos"),
    ("Other data roles", r"\bdata\b|\bdatos\b|machine learning"),
]
_DATA_ANALYST_HINT = re.compile(r"data|datos|analytics|insight|reporting|product|marketing|\bbi\b|business", re.I)

_REMOTE_RE = re.compile(r"remote|remoto|anywhere|worldwide|work from home", re.IGNORECASE)


def classify_seniority(title: str, hint: str = "") -> str:
    """Level from the title, falling back to the level the source reports."""
    for text in (title, hint):
        for name, pattern in SENIORITY:
            if text and pattern.search(text):
                return name
    return "Not stated"


def classify_role(title: str) -> str | None:
    """Data role family of a title, or None when it is not a data role."""
    if _NOT_DATA.search(title):
        return None
    for name, pattern in ROLES:
        if re.search(pattern, title, re.IGNORECASE):
            if name == "Data Analyst" and not _DATA_ANALYST_HINT.search(title):
                continue  # e.g. "Financial Analyst" is not a data role
            return name
    return None


def is_remote(job: Job) -> bool:
    if job.remote is False:
        return False
    return job.remote is True or bool(_REMOTE_RE.search(job.location or ""))


@dataclass
class RoleStats:
    name: str
    count: int
    skills: list[tuple[str, float]] = field(default_factory=list)  # (skill, share of the role's postings)


@dataclass
class MarketReport:
    total: int
    by_seniority: dict[str, int]
    roles: list[RoleStats]
    seniority_by_role: dict[str, dict[str, int]]


def analyze(jobs: list[Job], catalog: dict[str, str], min_share: float = 0.4, max_skills: int = 8) -> MarketReport:
    unique: dict[tuple[str, str], tuple[Job, str]] = {}
    for job in jobs:
        role = classify_role(job.title or "")
        if role and is_remote(job):
            unique.setdefault(job.dedup_key, (job, role))
    compiled = compile_catalog(catalog)
    by_seniority: Counter = Counter()
    by_role: dict[str, list[Job]] = {}
    seniority_by_role: dict[str, Counter] = {}
    for job, role in unique.values():
        level = classify_seniority(job.title, job.seniority_hint)
        by_seniority[level] += 1
        by_role.setdefault(role, []).append(job)
        seniority_by_role.setdefault(role, Counter())[level] += 1

    roles = []
    for role, members in by_role.items():
        counts: Counter = Counter()
        for job in members:
            job.description = strip_html(job.description)
            counts.update(mentioned(job, compiled))
        skills = [(s, c / len(members)) for s, c in counts.most_common() if c / len(members) >= min_share]
        roles.append(RoleStats(role, len(members), skills[:max_skills]))
    roles.sort(key=lambda r: (-r.count, r.name))
    return MarketReport(len(unique), dict(by_seniority), roles,
                        {r: dict(c) for r, c in seniority_by_role.items()})


def render(report: MarketReport, day: str, min_share: float, min_role_postings: int = 5) -> str:
    pct = lambda n: f"{n / report.total:.0%}" if report.total else "0%"  # noqa: E731
    lines = [f"# Remote data job market snapshot ({day})", "",
             f"{report.total} remote data postings (deduplicated) from the enabled sources.", "",
             "## By seniority", "", "| Seniority | Postings | Share |", "|---|---|---|"]
    for level in SENIORITY_ORDER:
        if n := report.by_seniority.get(level, 0):
            lines.append(f"| {level} | {n} | {pct(n)} |")
    lines += ["", "## By role", "",
              f"Skills listed are mentioned in at least {min_share:.0%} of the role's postings "
              f"(roles with fewer than {min_role_postings} postings are not detailed).", "",
              "| Role | Postings | Most requested skills |", "|---|---|---|"]
    for role in report.roles:
        if role.count < min_role_postings:
            skills = "(too few postings)"
        else:
            skills = ", ".join(f"{s} ({share:.0%})" for s, share in role.skills) or "(no skill above the threshold)"
        lines.append(f"| {role.name} | {role.count} | {skills} |")
    lines += ["", "## Seniority by role", "",
              "| Role | " + " | ".join(SENIORITY_ORDER) + " |", "|---|" + "---|" * len(SENIORITY_ORDER)]
    for role in report.roles:
        row = report.seniority_by_role.get(role.name, {})
        lines.append(f"| {role.name} | " + " | ".join(str(row.get(level, 0)) for level in SENIORITY_ORDER) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from .cli import collect, load_catalog, load_config

    parser = argparse.ArgumentParser(description="Snapshot of the remote data job market (independent of your profile).")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="only its sources/http are used")
    parser.add_argument("--min-share", type=float, default=0.4, help="minimum share for a skill to be listed")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cfg = load_config(args.config)
    catalog = load_catalog(Path(cfg.get("insights", {}).get("skills_catalog", "skills_catalog.yaml")))
    report = analyze(collect(cfg), catalog, args.min_share)
    out_dir = Path(cfg.get("output", {}).get("dir", "output"))
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    path = out_dir / f"market-{now:%Y%m%d-%H%M}.md"
    path.write_text(render(report, now.date().isoformat(), args.min_share), encoding="utf-8")
    log.info("\n%d remote data postings analyzed. Report: %s", report.total, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
