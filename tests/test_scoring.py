from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from jobfinder.models import Job
from jobfinder.scoring import evaluate, parse_salary_text, required_years, to_monthly_usd

NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


@pytest.fixture
def cfg():
    # Tests use the committed example config, then set the rules under test explicitly.
    data = yaml.safe_load(Path(__file__).parents[1].joinpath("config.example.yaml").read_text(encoding="utf-8"))
    data["filters"]["salary"].update(min_monthly_usd=3000, target_max_monthly_usd=3500)
    data["filters"]["location"].update(allowed=["argentina", "latam", "worldwide"], preferred=["argentina", "latam"])
    return data


def make(**kw):
    base = dict(source="t", title="Data Analyst", company="Acme", url="u", location="LATAM",
                description="Python and SQL dashboards.", published_at=NOW - timedelta(days=2), remote=True)
    base.update(kw)
    return Job(**base)


@pytest.mark.parametrize("text,expected", [
    ("$170k - $200k", (170000, 200000, "USD", None)),
    ("$87,069-$124,385 per year", (87069, 124385, "USD", "year")),
    ("USD 3000 - 4000 / month", (3000, 4000, "USD", "month")),
    ("€45/hour", (45, 45, "EUR", "hour")),
    ("Competitive", (None, None, None, None)),
])
def test_parse_salary_text(text, expected):
    assert parse_salary_text(text) == expected


def test_to_monthly_usd_converts_period_and_currency():
    fx = {"fx_to_usd": {"USD": 1.0, "EUR": 1.1}, "hours_per_month": 160}
    assert to_monthly_usd(60000, "USD", "yearly", fx) == 5000
    assert to_monthly_usd(20, "USD", "hour", fx) == 3200
    assert to_monthly_usd(1000, "EUR", "month", fx) == pytest.approx(1100)
    assert to_monthly_usd(1000, "XYZ", "month", fx) is None  # unknown currency is not guessed


def test_required_years():
    assert required_years("We need 3+ years of experience in SQL") == 3
    assert required_years("Al menos 6 años de experiencia") == 6
    assert required_years("Founded 10 years ago") is None


def test_keeps_matching_latam_role_with_salary_in_range(cfg):
    job = evaluate(make(salary_min=3000, salary_max=3500, salary_currency="USD", salary_period="month"), cfg, NOW)
    assert job.reject_reason == ""
    assert "latam-friendly" in job.flags
    assert job.score > 0


def test_flags_ask_for_more_when_pay_exceeds_target(cfg):
    job = evaluate(make(salary_min=4000, salary_max=5000, salary_currency="USD", salary_period="month"), cfg, NOW)
    assert job.reject_reason == ""
    assert "ask-for-more" in job.flags


def test_rejects_salary_below_floor(cfg):
    job = evaluate(make(salary_min=1500, salary_max=2000, salary_currency="USD", salary_period="month"), cfg, NOW)
    assert job.reject_reason.startswith("salary below floor")


def test_unknown_salary_is_kept_and_flagged(cfg):
    job = evaluate(make(), cfg, NOW)
    assert job.reject_reason == ""
    assert "salary-unknown" in job.flags


@pytest.mark.parametrize("title", ["Senior Data Scientist", "Sr. Data Engineer", "Staff ML Engineer", "Lead Data Analyst"])
def test_rejects_senior_titles(cfg, title):
    assert evaluate(make(title=title), cfg, NOW).reject_reason.startswith("seniority")


@pytest.mark.parametrize("title,hint", [("Data Engineer Semi Senior", ""), ("Data Analyst", "Semi Senior")])
def test_semi_senior_is_mid_level_and_kept(cfg, title, hint):
    assert evaluate(make(title=title, seniority_hint=hint), cfg, NOW).reject_reason == ""


def test_rejects_senior_hint_from_source(cfg):
    assert evaluate(make(seniority_hint="Senior"), cfg, NOW).reject_reason.startswith("seniority from source")


def test_rejects_non_data_role(cfg):
    assert evaluate(make(title="Backend Engineer"), cfg, NOW).reject_reason == "not a data role"


def test_rejects_location_outside_allowed_regions(cfg):
    assert evaluate(make(location="United States"), cfg, NOW).reject_reason.startswith("location")


def test_rejects_us_only_phrase_when_location_is_generic(cfg):
    job = evaluate(make(location="Remote", description="This role is US only."), cfg, NOW)
    assert job.reject_reason.startswith("location restricted")


def test_rejects_too_many_years_required(cfg):
    job = evaluate(make(description="Requires 8+ years of experience with Python"), cfg, NOW)
    assert job.reject_reason == "requires 8+ years"


def test_english_demanding_is_penalized_not_rejected(cfg):
    plain = evaluate(make(), cfg, NOW)
    demanding = evaluate(make(description="Python and SQL. Fluent English required."), cfg, NOW)
    assert demanding.reject_reason == ""
    assert "english-demanding" in demanding.flags
    assert demanding.score < plain.score


def test_spanish_posting_is_flagged(cfg):
    job = evaluate(make(language="es", description="Buscamos analista de datos con Python y SQL."), cfg, NOW)
    assert "spanish" in job.flags


def test_rejects_old_postings(cfg):
    assert evaluate(make(published_at=NOW - timedelta(days=60)), cfg, NOW).reject_reason == "too old"
