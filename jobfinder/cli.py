"""Entry point: python -m jobfinder [--config config.yaml]"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

from .insights import SkillGap, annotate_missing, mark_new, skill_gaps
from .models import Job
from .scoring import evaluate
from .sources import SOURCES, Http

log = logging.getLogger("jobfinder")


def load_config(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"Config not found: {path}. Copy config.example.yaml to config.yaml and fill in your profile.")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def collect(cfg: dict) -> list[Job]:
    http = Http(cfg.get("http", {}).get("user_agent", "job-finder/0.1"),
                timeout=cfg.get("http", {}).get("timeout", 20))
    jobs: list[Job] = []
    for name, opts in (cfg.get("sources") or {}).items():
        if not opts or not opts.get("enabled"):
            continue
        fetch = SOURCES.get(name)
        if not fetch:
            log.warning("unknown source %s", name)
            continue
        try:
            found = fetch(http, opts)
            log.info("%-12s %4d postings", name, len(found))
            jobs.extend(found)
        except Exception as exc:  # one broken source must not kill the run
            log.warning("%s failed: %s", name, exc)
    return jobs


def dedupe(jobs: list[Job]) -> list[Job]:
    best: dict[tuple[str, str], Job] = {}
    for job in jobs:
        current = best.get(job.dedup_key)
        if current is None or job.score > current.score:
            best[job.dedup_key] = job
    return list(best.values())


def _salary(job: Job) -> str:
    if job.monthly_usd_max is None:
        return "?"
    if round(job.monthly_usd_min or 0) == round(job.monthly_usd_max):
        return f"{job.monthly_usd_max:,.0f}"
    return f"{job.monthly_usd_min:,.0f}-{job.monthly_usd_max:,.0f}"


def _cell(value: object) -> str:
    return str(value).replace("|", "/")[:60]


def _missing(job: Job, limit: int = 5) -> str:
    extra = len(job.missing_skills) - limit
    text = ", ".join(job.missing_skills[:limit])
    return f"{text} (+{extra})" if extra > 0 else text or "—"


def write_reports(kept: list[Job], near: list[Job], gaps: list[SkillGap], eligible_count: int,
                  rejected: Counter, out_dir: Path, top_n: int) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    csv_path, md_path = out_dir / f"jobs-{stamp}.csv", out_dir / f"jobs-{stamp}.md"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["score", "title", "company", "location", "usd_month", "flags", "skills", "missing_skills",
                         "source", "published", "url"])
        for j in kept:
            writer.writerow([j.score, j.title, j.company, j.location, _salary(j), " ".join(j.flags),
                             ", ".join(j.matched_skills), ", ".join(j.missing_skills), j.source,
                             j.published_at.date().isoformat() if j.published_at else "", j.url])

    new_count = sum(j.is_new for j in kept)
    lines = [f"# Job matches {stamp}", "",
             f"{len(kept)} matches ({new_count} new since the last run). Top {min(top_n, len(kept))}.",
             "\"Missing skills\" = skills the posting asks for that your profile does not list: prepare them "
             "before applying.", "",
             "| Score | Role | Company | Location | USD/month | Flags | Missing skills | URL |",
             "|---|---|---|---|---|---|---|---|"]
    for j in kept[:top_n]:
        lines.append(f"| {j.score} | {_cell(j.title)} | {_cell(j.company)} | {_cell(j.location)} | {_salary(j)} "
                     f"| {' '.join(j.flags)} | {_missing(j)} | {j.url} |")

    lines += ["", "## Near misses", "",
              "Postings that failed a single soft filter by a small margin. Worth a look: the blocker may be "
              "negotiable.", ""]
    if near:
        lines += ["| Score | Role | Company | Location | USD/month | Blocked by | Missing skills | New | URL |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for j in near:
            lines.append(f"| {j.score} | {_cell(j.title)} | {_cell(j.company)} | {_cell(j.location)} "
                         f"| {_salary(j)} | {_cell(j.reject_reason)} | {_missing(j)} | {'yes' if j.is_new else ''} "
                         f"| {j.url} |")
    else:
        lines.append("None this run.")

    lines += ["", "## Skill gaps", "",
              f"Skills requested by the {eligible_count} eligible data postings (data role, eligible location, any "
              "seniority) that are not in your profile, most demanded first.", ""]
    if gaps:
        lines += ["| Skill | Postings | Share |", "|---|---|---|"]
        lines += [f"| {g.skill} | {g.count} | {g.share:.0%} |" for g in gaps]
    else:
        lines.append("No gaps found (or no skills catalog configured).")

    lines += ["", "## Rejected by reason", ""] + [f"- {r}: {n}" for r, n in rejected.most_common(15)]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, md_path


def load_catalog(path: Path) -> dict[str, str]:
    if not path.exists():
        log.warning("skills catalog not found: %s (skill gaps skipped)", path)
        return {}
    with path.open(encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("skills", {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find remote data jobs that fit your profile (no auto-apply).")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    cfg = load_config(args.config)
    out = cfg.get("output", {})
    ins = cfg.get("insights", {})
    out_dir = Path(out.get("dir", "output"))

    raw = collect(cfg)
    evaluated = [evaluate(j, cfg) for j in raw]
    min_score = out.get("min_score", 0)
    by_score = lambda jobs: sorted(dedupe(jobs), key=lambda j: j.score, reverse=True)  # noqa: E731
    kept = [j for j in by_score([j for j in evaluated if not j.reject_reason]) if j.score >= min_score]
    near = by_score([j for j in evaluated if j.near_miss])[: ins.get("near_miss_top_n", 15)]
    rejected = Counter(j.reject_reason.split(" (")[0] for j in evaluated if j.reject_reason)

    catalog = load_catalog(Path(ins.get("skills_catalog", "skills_catalog.yaml")))
    gaps = skill_gaps(evaluated, catalog, cfg["profile"]["skills"], ins.get("gaps_min_count", 2))
    gaps = gaps[: ins.get("gaps_top_n", 15)]
    annotate_missing(kept + near, catalog, cfg["profile"]["skills"])
    eligible_count = len({j.dedup_key for j in evaluated if j.eligible})

    if ins.get("track_new", True):
        mark_new(kept + near, out_dir / "seen.json", retention_days=ins.get("new_retention_days", 120))
        for j in kept:
            if j.is_new:
                j.flags.insert(0, "new")

    csv_path, md_path = write_reports(kept, near, gaps, eligible_count, rejected, out_dir, out.get("top_n", 40))
    log.info("\n%d fetched, %d kept (%d new), %d near misses, %d skill gaps. Reports:\n  %s\n  %s",
             len(raw), len(kept), sum(j.is_new for j in kept), len(near), len(gaps), csv_path, md_path)
    return 0
