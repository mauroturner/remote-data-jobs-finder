"""Market snapshot of remote data jobs: postings by seniority, by role, and the skills each role asks for.

Independent of your profile: it classifies every remote data posting the sources return.
Usage: python -m jobfinder.market [--config config.yaml] [--min-share 0.4]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
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
    skills: list[tuple[str, float]] = field(default_factory=list)  # (skill, share) at or above min_share
    all_skills: list[tuple[str, float]] = field(default_factory=list)  # every mentioned skill, highest share first


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
        shares = [(s, c / len(members)) for s, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        skills = [(s, share) for s, share in shares if share >= min_share]
        roles.append(RoleStats(role, len(members), skills[:max_skills], shares))
    roles.sort(key=lambda r: (-r.count, r.name))
    return MarketReport(len(unique), dict(by_seniority), roles,
                        {r: dict(c) for r, c in seniority_by_role.items()})


def skills_at(role: RoleStats, threshold: float, max_skills: int | None = None) -> list[tuple[str, float]]:
    """Skills mentioned in at least `threshold` of the role's postings."""
    kept = [(s, share) for s, share in role.all_skills if share >= threshold]
    return kept[:max_skills] if max_skills else kept


def segmentation(report: MarketReport, thresholds: list[float], min_postings: int = 5) -> list[dict]:
    """How well each threshold separates roles.

    For roles with at least `min_postings`: average skills kept per role, roles left with no skill, and the mean
    pairwise Jaccard similarity of the roles' skill sets (lower = roles are more distinct from each other).
    """
    roles = [r for r in report.roles if r.count >= min_postings]
    rows = []
    for t in thresholds:
        sets = [{s for s, _ in skills_at(r, t)} for r in roles]
        pairs = [(a, b) for i, a in enumerate(sets) for b in sets[i + 1:] if a | b]
        jaccard = sum(len(a & b) / len(a | b) for a, b in pairs) / len(pairs) if pairs else 0.0
        rows.append({
            "threshold": t,
            "roles": len(roles),
            "avg_skills": sum(map(len, sets)) / len(sets) if sets else 0.0,
            "roles_without_skills": sum(1 for s in sets if not s),
            "mean_jaccard": jaccard,
        })
    return rows


def save_snapshot(report: MarketReport, path: Path, meta: dict) -> None:
    """Aggregated numbers only (no postings), so charts can be regenerated without fetching again."""
    data = {"meta": meta, "report": asdict(report)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def load_snapshot(path: Path) -> tuple[MarketReport, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rep = data["report"]
    roles = [RoleStats(r["name"], r["count"], [tuple(s) for s in r["skills"]], [tuple(s) for s in r["all_skills"]])
             for r in rep["roles"]]
    return MarketReport(rep["total"], rep["by_seniority"], roles, rep["seniority_by_role"]), data["meta"]


def _render_thresholds(report: MarketReport, thresholds: list[float], min_postings: int) -> list[str]:
    lines = ["", "## Skills by threshold", "",
             "Shared skills per role: a skill is listed when it appears in at least that share of the role's "
             f"postings (roles with {min_postings}+ postings).", "",
             "| Threshold | Roles | Avg skills per role | Roles with no skill | Mean overlap between roles (Jaccard) |",
             "|---|---|---|---|---|"]
    for row in segmentation(report, thresholds, min_postings):
        lines.append(f"| {row['threshold']:.0%} | {row['roles']} | {row['avg_skills']:.1f} "
                     f"| {row['roles_without_skills']} | {row['mean_jaccard']:.2f} |")
    for t in thresholds:
        lines += ["", f"### At {t:.0%}", "", "| Role | Postings | Shared skills |", "|---|---|---|"]
        for role in report.roles:
            if role.count >= min_postings:
                kept = ", ".join(f"{s} ({share:.0%})" for s, share in skills_at(role, t)) or "—"
                lines.append(f"| {role.name} | {role.count} | {kept} |")
    return lines


def render(report: MarketReport, day: str, min_share: float, min_role_postings: int = 5,
           thresholds: list[float] | None = None) -> str:
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
    if thresholds:
        lines += _render_thresholds(report, thresholds, min_role_postings)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from .cli import collect, load_catalog, load_config

    parser = argparse.ArgumentParser(description="Snapshot of the remote data job market (independent of your profile).")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="only its sources/http are used")
    parser.add_argument("--min-share", type=float, default=0.4, help="minimum share for a skill to be listed")
    parser.add_argument("--thresholds", default="", help="compare several shares, e.g. 0.4,0.5,0.6")
    parser.add_argument("--charts", type=Path, help="write SVG charts (light and dark) to this folder")
    parser.add_argument("--save-snapshot", type=Path, help="save the aggregated numbers as JSON")
    parser.add_argument("--from-snapshot", type=Path, help="reuse a saved snapshot instead of fetching")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    thresholds = [float(t) for t in args.thresholds.split(",") if t.strip()]

    cfg = load_config(args.config)
    now = datetime.now()
    if args.from_snapshot:
        report, meta = load_snapshot(args.from_snapshot)
    else:
        catalog = load_catalog(Path(cfg.get("insights", {}).get("skills_catalog", "skills_catalog.yaml")))
        report = analyze(collect(cfg), catalog, args.min_share)
        enabled = {name: {k: v for k, v in opts.items() if k != "enabled"}
                   for name, opts in (cfg.get("sources") or {}).items() if opts and opts.get("enabled")}
        meta = {"date": now.date().isoformat(), "sources": enabled}
    if args.save_snapshot:
        save_snapshot(report, args.save_snapshot, meta)

    out_dir = Path(cfg.get("output", {}).get("dir", "output"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"market-{now:%Y%m%d-%H%M}.md"
    path.write_text(render(report, meta["date"], args.min_share, thresholds=thresholds), encoding="utf-8")
    log.info("\n%d remote data postings analyzed. Report: %s", report.total, path)
    if args.charts:
        from .market_charts import render_charts

        for chart in render_charts(report, args.charts, thresholds or [args.min_share], meta["date"]):
            log.info("  chart: %s", chart)
    return 0


if __name__ == "__main__":
    sys.exit(main())
