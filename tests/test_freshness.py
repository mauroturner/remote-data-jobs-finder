from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from jobfinder import cli
from jobfinder.models import Job
from jobfinder.scoring import evaluate, freshness_points

NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
EXAMPLE = Path(__file__).parents[1] / "config.example.yaml"


def make(hours_old=None, **kw):
    base = dict(source="t", title="Data Analyst", company="Acme", url="https://example.com/j/1",
                location="Worldwide", description="Python and SQL dashboards.", remote=True,
                published_at=None if hours_old is None else NOW - timedelta(hours=hours_old))
    base.update(kw)
    return Job(**base)


def example_cfg() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- freshness

def test_example_config_keeps_one_week_by_default():
    assert example_cfg()["filters"]["max_age_days"] == 7


@pytest.mark.parametrize("hours,expected", [(2, 1.0), (24, 1.0), (96, 0.5), (168, 0.0)])
def test_freshness_points_favor_the_first_24_hours(hours, expected):
    assert freshness_points(make(hours), max_age_days=7, now=NOW) == pytest.approx(expected)


def test_freshness_points_are_neutral_without_a_date():
    assert freshness_points(make(None), max_age_days=7, now=NOW) == 0.5


def test_posting_from_the_last_24_hours_is_flagged_and_ranked_higher():
    cfg = example_cfg()
    fresh = evaluate(make(3), cfg, NOW)
    older = evaluate(make(24 * 5), cfg, NOW)
    assert "24h" in fresh.flags and "24h" not in older.flags
    assert fresh.score > older.score


def test_posting_older_than_one_week_is_rejected_by_default():
    assert evaluate(make(24 * 8), example_cfg(), NOW).reject_reason == "too old"


def test_configs_without_freshness_weight_keep_their_scores():
    cfg = example_cfg()
    cfg["scoring"].pop("freshness")
    assert evaluate(make(3), cfg, NOW).score == evaluate(make(24 * 5), cfg, NOW).score


# ---------------------------------------------------------------- only new postings by default

@pytest.fixture
def run(tmp_path, monkeypatch):
    cfg = example_cfg()
    cfg["output"].update(dir=str(tmp_path / "out"), min_score=0)
    cfg["link_check"] = {"enabled": False}
    cfg["insights"]["skills_catalog"] = str(tmp_path / "missing.yaml")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    jobs: list[Job] = []
    monkeypatch.setattr(cli, "collect", lambda _cfg: [Job(**vars(j)) for j in jobs])

    def _run(postings, *flags):
        jobs[:] = postings
        assert cli.main(["--config", str(path), *flags]) == 0
        report = max((tmp_path / "out").glob("jobs-*.md"), key=lambda p: p.stat().st_mtime_ns)
        text = report.read_text(encoding="utf-8")
        report.unlink()  # two runs in the same minute would share a file name
        return text

    return _run


def recent(title, company, url):
    return Job(source="t", title=title, company=company, url=url, location="Worldwide",
               description="Python and SQL", remote=True, published_at=datetime.now(timezone.utc))


def test_second_run_hides_postings_already_seen(run):
    first = [recent("Data Analyst", "Acme", "https://a/1")]
    assert "https://a/1" in run(first)
    second = run(first + [recent("Data Engineer", "Beta", "https://b/2")])
    assert "https://b/2" in second and "https://a/1" not in second
    assert "1 already seen in earlier runs" in second


def test_all_flag_shows_postings_already_seen(run):
    postings = [recent("Data Analyst", "Acme", "https://a/1")]
    run(postings)
    assert "https://a/1" in run(postings, "--all")


def test_only_new_can_be_disabled_in_config(run, tmp_path):
    path = tmp_path / "config.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["output"]["only_new"] = False
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    postings = [recent("Data Analyst", "Acme", "https://a/1")]
    run(postings)
    assert "https://a/1" in run(postings)
