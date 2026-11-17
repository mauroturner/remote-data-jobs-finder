from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from jobfinder import cli
from jobfinder.models import Job
from jobfinder.scoring import evaluate, is_internship
from jobfinder.sources import SOURCES, simplify

EXAMPLE = Path(__file__).parents[1] / "config.example.yaml"
NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def example_cfg() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def posting(title="Data Analyst", hint="", location="Worldwide", salary=None, company="Acme", url="https://a/1"):
    return Job(source="t", title=title, company=company, url=url, location=location, description="Python and SQL",
               remote=True, published_at=datetime.now(timezone.utc), seniority_hint=hint,
               salary_min=salary, salary_max=salary, salary_currency="USD" if salary else None,
               salary_period="month" if salary else None)


@pytest.mark.parametrize("title,hint,expected", [
    ("Data Analyst Intern", "", True),
    ("Data Science Internship - Summer 2027", "", True),
    ("Pasante de Análisis de Datos", "", True),
    ("Trainee Data Engineer", "", True),
    ("Becario/a de Datos", "", True),
    ("Estagiário de Dados", "", True),
    ("Data Analyst", "No experience", True),      # Get on Board level "no experience required"
    ("Data Scientist", "New grad", True),
    ("Data Analyst", "Junior", False),
    ("Internal Tools Data Engineer", "", False),  # "internal" is not "intern"
    ("International Data Analyst", "", False),
])
def test_is_internship(title, hint, expected):
    assert is_internship(posting(title, hint), example_cfg()) is expected


def test_internships_can_be_disabled():
    cfg = example_cfg()
    cfg["internships"]["enabled"] = False
    assert evaluate(posting("Data Analyst Intern"), cfg, NOW).internship is False


@pytest.fixture
def report(tmp_path, monkeypatch):
    cfg = example_cfg()
    cfg["output"]["dir"] = str(tmp_path / "out")
    cfg["filters"]["salary"]["min_monthly_usd"] = 3000
    cfg["filters"]["seniority"]["reject_title"].append(r"\bintern(ship)?\b")  # like a Junior/Mid profile does
    cfg["filters"]["location"]["allowed"] = ["worldwide", "latam"]
    cfg["link_check"]["enabled"] = False
    cfg["insights"]["skills_catalog"] = str(tmp_path / "missing.yaml")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    def _run(postings):
        monkeypatch.setattr(cli, "collect", lambda _cfg: postings)
        assert cli.main(["--config", str(path)]) == 0
        return next((tmp_path / "out").glob("jobs-*.md")).read_text(encoding="utf-8")

    return _run


def test_report_lists_internships_in_their_own_section_ignoring_salary_and_seniority(report):
    text = report([
        posting("Data Analyst Intern", salary=800, url="https://a/intern"),
        posting("Data Analyst", hint="No experience", company="Beta", url="https://a/noexp"),
        posting("Data Science Intern", location="New York, NY", company="Gamma", url="https://a/onsite"),
        posting("Marketing Intern", company="Delta", url="https://a/marketing"),
    ])
    section = text.split("## Internships and first-experience roles", 1)[1].split("\n## ", 1)[0]
    assert "https://a/intern" in section and "https://a/noexp" in section
    assert "https://a/onsite" not in section       # location not eligible
    assert "https://a/marketing" not in section    # not a data role
    matches = text.split("## Near misses", 1)[0]
    assert "https://a/intern" not in matches        # still rejected from regular matches by your filters


def test_report_says_when_there_are_no_internships(report):
    assert "No internships this run." in report([posting("Data Analyst", salary=4000)])


# ---------------------------------------------------------------- Simplify internship lists

class FakeHttp:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def get_json(self, url, params=None):
        self.calls.append(url)
        return self.payload


def listing(**kw):
    base = {"title": "Data Analyst Intern", "company_name": "Acme", "url": "https://acme.com/j/1",
            "locations": ["Remote in USA"], "date_posted": 1794925226, "active": True, "is_visible": True,
            "category": "AI/ML/Data", "terms": ["Summer 2027"], "degrees": ["Bachelor's"], "sponsorship": "Other"}
    base.update(kw)
    return base


def test_simplify_keeps_active_remote_data_listings():
    http = FakeHttp([
        listing(),
        listing(title="Closed", active=False),
        listing(title="Hidden", is_visible=False),
        listing(title="Software Intern", category="Software"),
        listing(title="On site", locations=["NYC"]),
    ])
    jobs = simplify(http, {"lists": ["Summer2027-Internships"]})
    assert http.calls == ["https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/"
                          "listings.json"]
    assert [j.title for j in jobs] == ["Data Analyst Intern"]
    job = jobs[0]
    assert (job.company, job.location, job.remote, job.seniority_hint) == ("Acme", "Remote in USA", True, "Internship")
    assert job.source == "simplify" and job.published_at.year == 2026
    assert "Summer 2027" in job.description


def test_simplify_new_grad_list_is_tagged_as_new_grad_and_can_include_onsite():
    http = FakeHttp([listing(title="Data Scientist", locations=["NYC"])])
    jobs = simplify(http, {"lists": ["New-Grad-Positions"], "remote_only": False})
    assert jobs[0].seniority_hint == "New grad" and jobs[0].remote is False


def test_simplify_is_registered_as_source():
    assert SOURCES["simplify"] is simplify


# ---------------------------------------------------------------- Get on Board keyword search

class FakeGob:
    def __init__(self, items):
        self.items, self.calls = items, []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        return {"data": self.items}


def gob_item(title, job_id):
    return {"id": job_id, "attributes": {"title": title, "remote_modality": "fully_remote", "remote_zone": None,
                                         "countries": ["Argentina"], "published_at": 1794925226,
                                         "seniority": {"data": {"id": 1}}, "description": "SQL"},
            "links": {"public_url": f"https://www.getonbrd.com/jobs/{job_id}"}}


def test_getonbrd_queries_search_by_keyword_without_duplicates():
    from jobfinder.sources import getonbrd

    http = FakeGob([gob_item("Practicante de Datos", "a1"), gob_item("Trainee BI", "b2")])
    jobs = getonbrd(http, {"categories": [], "queries": ["practicante", "trainee"]})
    assert [c[0] for c in http.calls] == ["https://www.getonbrd.com/api/v0/search/jobs"] * 2
    assert http.calls[0][1]["query"] == "practicante"
    assert sorted(j.title for j in jobs) == ["Practicante de Datos", "Trainee BI"]  # same ids from both queries
    assert jobs[0].seniority_hint == "No experience" and jobs[0].location == "Remote LATAM"


def test_getonbrd_without_queries_only_reads_categories():
    from jobfinder.sources import getonbrd

    http = FakeGob([])
    getonbrd(http, {"categories": ["data-science-analytics"], "pages": 1})
    assert [c[0] for c in http.calls] == ["https://www.getonbrd.com/api/v0/categories/data-science-analytics/jobs"]
