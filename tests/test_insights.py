from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from jobfinder.insights import mark_new, skill_gaps
from jobfinder.models import Job
from jobfinder.scoring import evaluate

NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)
ROOT = Path(__file__).parents[1]


@pytest.fixture
def cfg():
    data = yaml.safe_load(ROOT.joinpath("config.example.yaml").read_text(encoding="utf-8"))
    data["filters"]["salary"].update(min_monthly_usd=3000, target_max_monthly_usd=3500)
    data["filters"]["location"].update(allowed=["latam", "worldwide"], preferred=["latam"])
    data["filters"]["seniority"]["max_years_required"] = 5
    data["near_miss"] = {"max_extra_years": 2, "salary_tolerance": 0.15, "include_senior_titles": True}
    return data


def make(**kw):
    base = dict(source="t", title="Data Analyst", company="Acme", url="u", location="LATAM",
                description="Python and SQL dashboards.", published_at=NOW - timedelta(days=2), remote=True)
    base.update(kw)
    return Job(**base)


# ---------------------------------------------------------------- near misses

def test_match_has_no_reasons_and_is_not_a_near_miss(cfg):
    job = evaluate(make(), cfg, NOW)
    assert job.reasons == [] and not job.near_miss


def test_all_failed_filters_are_collected(cfg):
    job = evaluate(make(title="Senior Data Analyst", location="United States"), cfg, NOW)
    assert [r.split(" (")[0] for r in job.reasons] == ["seniority in title", "location not eligible"]
    assert job.reject_reason == job.reasons[0]


@pytest.mark.parametrize("years,expected", [(6, True), (7, True), (8, False)])
def test_years_slightly_above_limit_is_near_miss(cfg, years, expected):
    job = evaluate(make(description=f"Requires {years}+ years of experience with Python"), cfg, NOW)
    assert job.reject_reason == f"requires {years}+ years"
    assert job.near_miss is expected


@pytest.mark.parametrize("monthly_max,expected", [(2700, True), (2000, False)])
def test_salary_slightly_below_floor_is_near_miss(cfg, monthly_max, expected):
    job = evaluate(make(salary_min=monthly_max, salary_max=monthly_max, salary_currency="USD",
                        salary_period="month"), cfg, NOW)
    assert job.reject_reason.startswith("salary below floor")
    assert job.near_miss is expected


@pytest.mark.parametrize("title,hint,expected", [
    ("Senior Data Analyst", "", True),
    ("Sr. Data Engineer", "", True),
    ("Data Analyst", "Senior", True),
    ("Lead Data Analyst", "", False),
    ("Staff Data Scientist", "", False),
])
def test_plain_senior_is_near_miss_but_leadership_is_not(cfg, title, hint, expected):
    assert evaluate(make(title=title, seniority_hint=hint), cfg, NOW).near_miss is expected


def test_senior_titles_can_be_excluded_from_near_misses(cfg):
    cfg["near_miss"]["include_senior_titles"] = False
    assert not evaluate(make(title="Senior Data Analyst"), cfg, NOW).near_miss


def test_two_soft_failures_are_not_a_near_miss(cfg):
    job = evaluate(make(title="Senior Data Analyst", description="Requires 6+ years of experience"), cfg, NOW)
    assert len(job.reasons) == 2 and not job.near_miss


def test_hard_failure_blocks_near_miss(cfg):
    job = evaluate(make(location="United States", description="Requires 6+ years of experience"), cfg, NOW)
    assert not job.near_miss


def test_near_miss_still_gets_a_score_for_ranking(cfg):
    assert evaluate(make(title="Senior Data Analyst"), cfg, NOW).score > 0


# ---------------------------------------------------------------- skill gaps

CATALOG = {"Snowflake": "snowflake", "Apache Spark": "(apache )?spark|pyspark", "Python": "python",
           "LLM": "llms?"}


def test_skill_gaps_count_only_eligible_postings_and_skip_covered_skills(cfg):
    profile = {"python": 3, "llms?": 2}
    jobs = [
        evaluate(make(company="A", description="Python, Snowflake and PySpark"), cfg, NOW),
        evaluate(make(company="B", title="Senior Data Engineer", description="Snowflake, LLMs"), cfg, NOW),
        evaluate(make(company="C", location="United States", description="Snowflake"), cfg, NOW),  # not eligible
        evaluate(make(company="D", title="Backend Engineer", description="Snowflake"), cfg, NOW),   # not data
    ]
    gaps = skill_gaps(jobs, CATALOG, profile)
    assert [(g.skill, g.count) for g in gaps] == [("Snowflake", 2), ("Apache Spark", 1)]
    assert gaps[0].share == pytest.approx(1.0)  # 2 of 2 eligible postings


def test_skill_gaps_deduplicate_the_same_posting_from_two_sources(cfg):
    jobs = [evaluate(make(source=s, description="Snowflake"), cfg, NOW) for s in ("a", "b")]
    assert skill_gaps(jobs, CATALOG, {})[0].count == 1


def test_committed_catalog_is_valid():
    catalog = yaml.safe_load(ROOT.joinpath("skills_catalog.yaml").read_text(encoding="utf-8"))["skills"]
    assert len(catalog) >= 40
    for name, pattern in catalog.items():
        assert isinstance(name, str) and isinstance(pattern, str)
        import re
        re.compile(pattern)


# ---------------------------------------------------------------- new since last run

def test_mark_new_flags_unseen_postings_and_persists_them(tmp_path):
    store = tmp_path / "seen.json"
    first = [make(company="A"), make(company="B")]
    mark_new(first, store, today=NOW.date())
    assert all(j.is_new for j in first) and store.exists()

    second = [make(company="A"), make(company="C")]
    mark_new(second, store, today=(NOW + timedelta(days=7)).date())
    assert [j.is_new for j in second] == [False, True]


def test_mark_new_forgets_entries_older_than_retention(tmp_path):
    store = tmp_path / "seen.json"
    mark_new([make(company="A")], store, today=NOW.date(), retention_days=30)
    later = [make(company="A")]
    mark_new(later, store, today=(NOW + timedelta(days=40)).date(), retention_days=30)
    assert later[0].is_new


# ---------------------------------------------------------------- report

def test_report_has_near_miss_and_skill_gap_sections(cfg, tmp_path):
    from collections import Counter

    from jobfinder.cli import write_reports
    from jobfinder.insights import SkillGap

    match = evaluate(make(company="A"), cfg, NOW)
    match.is_new = True
    near = evaluate(make(company="B", title="Senior Data Analyst"), cfg, NOW)
    _, md = write_reports([match], [near], [SkillGap("Snowflake", 3, 0.5)], 6, Counter({"too old": 2}),
                          tmp_path, top_n=10)
    text = md.read_text(encoding="utf-8")
    assert "1 matches (1 new since the last run)" in text
    assert "## Near misses" in text and "seniority in title" in text
    assert "## Skill gaps" in text and "| Snowflake | 3 | 50% |" in text and "6 eligible data postings" in text
    assert "- too old: 2" in text


def test_report_shows_missing_skills_per_posting(cfg, tmp_path):
    from collections import Counter

    from jobfinder.cli import write_reports

    match = evaluate(make(company="A"), cfg, NOW)
    match.missing_skills = ["Snowflake", "dbt", "Airflow", "Spark", "Kafka", "Docker"]
    near = evaluate(make(company="B", title="Senior Data Analyst"), cfg, NOW)
    near.missing_skills = []
    csv_path, md = write_reports([match], [near], [], 1, Counter(), tmp_path, top_n=10)
    text = md.read_text(encoding="utf-8")
    assert "| Missing skills |" in text
    assert "Snowflake, dbt, Airflow, Spark, Kafka (+1)" in text
    assert "| — |" in text
    assert "missing_skills" in csv_path.read_text(encoding="utf-8-sig").splitlines()[0]
