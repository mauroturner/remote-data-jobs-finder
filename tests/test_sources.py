from jobfinder.sources import SOURCES, workable


class FakeHttp:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        return self.payload


def test_workable_maps_remote_and_onsite_postings():
    http = FakeHttp({"jobs": [
        {"title": "Data Analyst", "shortlink": "https://apply.workable.com/j/A1", "telecommuting": True,
         "city": "Bogotá", "country": "Colombia", "description": "<p>SQL</p>", "published_on": "2026-11-10"},
        {"title": "Account Specialist", "url": "https://apply.workable.com/j/B2", "telecommuting": "False",
         "city": "", "country": "Mexico", "description": "", "published_on": "2026-11-11"},
    ]})
    jobs = workable(http, {"companies": ["acme"]})

    assert http.calls == [("https://apply.workable.com/api/v1/widget/accounts/acme", {"details": "true"})]
    remote, onsite = jobs
    assert (remote.title, remote.location, remote.remote, remote.url) == (
        "Data Analyst", "Remote, Bogotá, Colombia", True, "https://apply.workable.com/j/A1")
    assert remote.source == "workable:acme" and remote.published_at.year == 2026
    assert (onsite.location, onsite.remote, onsite.url) == ("Mexico", False, "https://apply.workable.com/j/B2")


def test_workable_is_registered_as_source():
    assert SOURCES["workable"] is workable
