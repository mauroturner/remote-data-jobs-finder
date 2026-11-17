import pytest

from jobfinder.linkcheck import check_links, classify
from jobfinder.models import Job

URL = "https://jobs.example.com/acme/123"


@pytest.mark.parametrize("status,final_url,body,expected", [
    (200, URL, "<h1>Data Analyst</h1> Apply now", "open"),
    (404, URL, "", "closed"),
    (410, URL, "", "closed"),
    (200, "https://job-boards.greenhouse.io/acme?error=true", "", "closed"),         # Greenhouse redirect
    (200, "https://apply.workable.com/acme/?not_found=true", "", "closed"),          # Workable redirect
    (200, URL, "Sorry, this job is no longer accepting applications.", "closed"),
    (200, URL, "This position has been filled", "closed"),
    (200, URL, "La oferta ya no está disponible", "closed"),
    (403, URL, "", "unknown"),                                                         # bot protection
    (429, URL, "", "unknown"),
    (503, URL, "", "unknown"),
    (None, URL, "", "unknown"),                                                        # network error
])
def test_classify(status, final_url, body, expected):
    assert classify(status, final_url, body) == expected


def job(url):
    return Job(source="t", title="Data Analyst", company="Acme", url=url)


def test_check_links_marks_each_posting_and_skips_empty_urls():
    responses = {"https://a/open": (200, "https://a/open", "ok"), "https://a/gone": (404, "https://a/gone", "")}

    def fetch(url):
        if url == "https://a/boom":
            raise ConnectionError("down")
        return responses[url]

    jobs = [job("https://a/open"), job("https://a/gone"), job("https://a/boom"), job("")]
    counts = check_links(jobs, {"enabled": True, "workers": 2}, fetch=fetch)
    assert [j.link_status for j in jobs] == ["open", "closed", "unknown", "unknown"]
    assert counts == {"open": 1, "closed": 1, "unknown": 2}


def test_check_links_does_nothing_when_disabled():
    jobs = [job("https://a/open")]
    assert check_links(jobs, {"enabled": False}, fetch=lambda url: pytest.fail("must not fetch")) == {}
    assert jobs[0].link_status == ""


def test_check_links_stops_at_max_links():
    calls = []
    jobs = [job(f"https://a/{i}") for i in range(5)]
    check_links(jobs, {"max_links": 2}, fetch=lambda url: calls.append(url) or (200, url, ""))
    assert len(calls) == 2
    assert [j.link_status for j in jobs] == ["open", "open", "", "", ""]


def test_report_drops_closed_postings_and_counts_them(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from pathlib import Path

    import yaml

    from jobfinder import cli, linkcheck

    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.example.yaml").read_text(encoding="utf-8"))
    cfg["output"]["dir"] = str(tmp_path / "out")
    cfg["insights"]["skills_catalog"] = str(tmp_path / "missing.yaml")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    now = datetime.now(timezone.utc)
    postings = [Job(source="t", title="Data Analyst", company=c, url=f"https://a/{c}", location="Worldwide",
                    description="Python and SQL", remote=True, published_at=now) for c in ("open", "gone")]
    monkeypatch.setattr(cli, "collect", lambda _cfg: postings)
    monkeypatch.setattr(linkcheck, "fetch_page", lambda url, **kw: (404, url, "") if "gone" in url else (200, url, ""))

    assert cli.main(["--config", str(path)]) == 0
    report = next((tmp_path / "out").glob("jobs-*.md")).read_text(encoding="utf-8")
    assert "https://a/open" in report and "https://a/gone" not in report
    assert "- posting closed (link check): 1" in report
