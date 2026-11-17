import pytest

from jobfinder.insights import annotate_missing
from jobfinder.market import analyze, classify_role, classify_seniority, is_remote, render
from jobfinder.models import Job

CATALOG = {"SQL": "sql", "Python": "python", "Tableau": "tableau", "Spark": "(py)?spark"}


def job(title, desc="", location="Remote", remote=True, hint="", company="Acme"):
    return Job(source="t", title=title, company=company, url="u", location=location, description=desc,
               remote=remote, seniority_hint=hint)


@pytest.mark.parametrize("title,hint,expected", [
    ("Data Analyst Intern", "", "Intern / Trainee"),
    ("Pasante de Datos", "", "Intern / Trainee"),
    ("Junior Data Analyst", "", "Junior"),
    ("Data Engineer Semi Senior", "", "Mid"),
    ("Data Scientist II", "", "Mid"),
    ("Senior Data Engineer", "", "Senior"),
    ("Sr. Data Analyst", "", "Senior"),
    ("Lead Data Scientist", "", "Lead / Staff / Manager"),
    ("Senior Manager, Analytics", "", "Lead / Staff / Manager"),
    ("Data Engineer", "Mid-level", "Mid"),
    ("Data Engineer", "Senior", "Senior"),
    ("Data Engineer", "", "Not stated"),
])
def test_classify_seniority(title, hint, expected):
    assert classify_seniority(title, hint) == expected


@pytest.mark.parametrize("title,expected", [
    ("Senior Data Analyst", "Data Analyst"),
    ("Analista de Datos", "Data Analyst"),
    ("Data Scientist", "Data Scientist"),
    ("Applied Scientist", "Data Scientist"),
    ("Data Engineer (Snowflake)", "Data Engineer"),
    ("Ingeniero de Datos GCP", "Data Engineer"),
    ("Analytics Engineer", "Analytics Engineer"),
    ("Machine Learning Engineer", "ML Engineer / MLOps"),
    ("AI Engineer (LLMs)", "AI / LLM Engineer"),
    ("BI Developer", "BI Analyst / Developer"),
    ("Power BI Analyst", "BI Analyst / Developer"),
    ("Data Architect", "Data Architect"),
    ("Backend Engineer", None),
    ("Machine Learning (ML) Engineer, Ranking & Retrieval", "ML Engineer / MLOps"),
    ("AI Machine Learning Specialist", "ML Engineer / MLOps"),
    ("Cientista de Datos", "Data Scientist"),
    ("Data Developer", "Data Engineer"),
    ("Data API Engineer", "Data Engineer"),
    ("Data Director, Payments", "Other data roles"),
    ("Software Engineer, Machine Learning", "Other data roles"),
    ("Senior Software Engineer – AI / Python", None),
    # Not data roles even though they mention data/AI
    ("Enterprise Sales Executive, AI Solutions", None),
    ("Generalist AI Trainer $35 hour", None),
    ("Egocentric Data Collection Partner", None),
    ("Principal Civil Engineer- Data Center Engineering", None),
    ("Senior Product Manager - AI Gateway", None),
    ("Software Engineer - AI Tooling Infrastructure", None),
    ("Staff Fullstack Engineer, Data Products (Golang / Node)", None),
])
def test_classify_role(title, expected):
    assert classify_role(title) == expected


def test_is_remote():
    assert is_remote(job("x", remote=True, location="USA"))
    assert is_remote(job("x", remote=None, location="Remote, Brazil"))
    assert not is_remote(job("x", remote=None, location="New York, NY"))
    assert not is_remote(job("x", remote=False, location="Remote"))


def test_analyze_counts_remote_data_postings_by_seniority_role_and_skills():
    jobs = [
        job("Data Analyst", "SQL, Python, Tableau", company="A"),
        job("Junior Data Analyst", "SQL and Tableau", company="B"),
        job("Senior Data Analyst", "SQL", company="C"),
        job("Senior Data Analyst", "SQL", company="C"),                    # duplicate
        job("Data Engineer", "PySpark, SQL", company="D", remote=False),   # not remote
        job("Backend Engineer", "Python", company="E"),                    # not a data role
    ]
    report = analyze(jobs, CATALOG, min_share=0.5)
    assert report.total == 3
    assert report.by_seniority == {"Not stated": 1, "Junior": 1, "Senior": 1}
    analyst = report.roles[0]
    assert (analyst.name, analyst.count) == ("Data Analyst", 3)
    assert analyst.skills == [("SQL", 1.0), ("Tableau", pytest.approx(2 / 3))]


def test_render_orders_seniority_from_intern_to_lead():
    jobs = [job(t, company=c) for t, c in [("Lead Data Analyst", "A"), ("Junior Data Analyst", "B"),
                                            ("Senior Data Analyst", "C"), ("Data Analyst Intern", "D")]]
    text = render(analyze(jobs, CATALOG), "2026-11-17", min_share=0.5)
    order = [text.index(f"| {level} |") for level in ("Intern / Trainee", "Junior", "Senior", "Lead / Staff / Manager")]
    assert order == sorted(order)


def test_render_lists_roles_with_their_demanded_skills():
    report = analyze([job("Data Analyst", "SQL, Tableau", company=c) for c in "ABCDE"], CATALOG, min_share=0.5)
    text = render(report, "2026-11-17", min_share=0.5)
    assert "5 remote data postings" in text
    assert "| Data Analyst | 5 | SQL (100%), Tableau (100%) |" in text


# ---------------------------------------------------------------- per-posting missing skills

def test_annotate_missing_lists_uncovered_skills_each_posting_asks_for():
    jobs = [job("Data Analyst", "SQL, Tableau and Spark"), job("Data Analyst", "SQL only", company="B")]
    annotate_missing(jobs, CATALOG, {"sql": 3, "python": 2})
    assert jobs[0].missing_skills == ["Tableau", "Spark"]
    assert jobs[1].missing_skills == []


# ---------------------------------------------------------------- thresholds, segmentation, snapshot, charts

from jobfinder.market import MarketReport, RoleStats, load_snapshot, save_snapshot, segmentation, skills_at


def sample_report():
    roles = [
        RoleStats("Data Analyst", 10, all_skills=[("SQL", 0.9), ("Excel", 0.55), ("Python", 0.45), ("Tableau", 0.2)]),
        RoleStats("Data Engineer", 8, all_skills=[("Python", 0.8), ("SQL", 0.7), ("Spark", 0.5), ("AWS", 0.42)]),
        RoleStats("Tiny Role", 2, all_skills=[("SQL", 1.0)]),
    ]
    return MarketReport(20, {"Senior": 12, "Mid": 8}, roles, {"Data Analyst": {"Senior": 6, "Mid": 4}})


def test_skills_at_filters_by_threshold():
    analyst = sample_report().roles[0]
    assert [s for s, _ in skills_at(analyst, 0.4)] == ["SQL", "Excel", "Python"]
    assert [s for s, _ in skills_at(analyst, 0.6)] == ["SQL"]


def test_segmentation_metrics_per_threshold():
    rows = {r["threshold"]: r for r in segmentation(sample_report(), [0.4, 0.6], min_postings=5)}
    # 0.4: analyst {SQL, Excel, Python}, engineer {Python, SQL, Spark, AWS} -> jaccard 2/5
    assert rows[0.4]["avg_skills"] == pytest.approx(3.5)
    assert rows[0.4]["mean_jaccard"] == pytest.approx(0.4)
    assert rows[0.4]["roles_without_skills"] == 0
    # 0.6: analyst {SQL}, engineer {Python, SQL} -> jaccard 1/2
    assert rows[0.6]["avg_skills"] == pytest.approx(1.5)
    assert rows[0.6]["mean_jaccard"] == pytest.approx(0.5)
    assert rows[0.6]["roles"] == 2  # "Tiny Role" is below min_postings


def test_snapshot_round_trip(tmp_path):
    path = tmp_path / "snap.json"
    save_snapshot(sample_report(), path, {"date": "2026-11-17"})
    report, meta = load_snapshot(path)
    assert meta == {"date": "2026-11-17"}
    assert report == sample_report()


def test_render_with_thresholds_adds_comparison_sections():
    text = render(sample_report(), "2026-11-17", min_share=0.4, thresholds=[0.4, 0.5, 0.6])
    assert "## Skills by threshold" in text
    assert "### At 50%" in text and "| Data Analyst | 10 | SQL (90%), Excel (55%) |" in text
    assert "| 60% | 2 | 1.5 |" in text


def test_charts_are_written_in_light_and_dark(tmp_path):
    pytest.importorskip("matplotlib")
    from jobfinder.market_charts import render_charts

    paths = render_charts(sample_report(), tmp_path, [0.4, 0.5, 0.6], "2026-11-17", min_postings=5)
    names = sorted(p.name for p in paths)
    assert names == sorted(f"{n}-{m}.svg" for n in ("skills-by-role", "skills-per-threshold", "seniority")
                           for m in ("light", "dark"))
    assert all(p.read_text(encoding="utf-8").lstrip().startswith("<?xml") for p in paths)
