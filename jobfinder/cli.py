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


def write_reports(kept: list[Job], rejected: Counter, out_dir: Path, top_n: int) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    csv_path, md_path = out_dir / f"jobs-{stamp}.csv", out_dir / f"jobs-{stamp}.md"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["score", "title", "company", "location", "usd_month", "flags", "skills", "source",
                         "published", "url"])
        for j in kept:
            writer.writerow([j.score, j.title, j.company, j.location, _salary(j), " ".join(j.flags),
                             ", ".join(j.matched_skills), j.source,
                             j.published_at.date().isoformat() if j.published_at else "", j.url])
    lines = [f"# Job matches {stamp}", "", f"{len(kept)} matches. Top {min(top_n, len(kept))}:", "",
             "| Score | Role | Company | Location | USD/month | Flags | URL |", "|---|---|---|---|---|---|---|"]
    for j in kept[:top_n]:
        cell = lambda s: str(s).replace("|", "/")[:60]  # noqa: E731
        lines.append(f"| {j.score} | {cell(j.title)} | {cell(j.company)} | {cell(j.location)} | {_salary(j)} "
                     f"| {' '.join(j.flags)} | {j.url} |")
    lines += ["", "## Rejected by reason", ""] + [f"- {r}: {n}" for r, n in rejected.most_common(15)]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find remote data jobs that fit your profile (no auto-apply).")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    cfg = load_config(args.config)
    raw = collect(cfg)
    evaluated = [evaluate(j, cfg) for j in raw]
    kept = sorted(dedupe([j for j in evaluated if not j.reject_reason]), key=lambda j: j.score, reverse=True)
    min_score = cfg.get("output", {}).get("min_score", 0)
    kept = [j for j in kept if j.score >= min_score]
    rejected = Counter(j.reject_reason.split(" (")[0] for j in evaluated if j.reject_reason)

    out = cfg.get("output", {})
    csv_path, md_path = write_reports(kept, rejected, Path(out.get("dir", "output")), out.get("top_n", 40))
    log.info("\n%d fetched, %d kept. Reports:\n  %s\n  %s", len(raw), len(kept), csv_path, md_path)
    return 0
