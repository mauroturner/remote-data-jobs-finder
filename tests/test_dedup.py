import pytest

from jobfinder.cli import dedupe
from jobfinder.models import Job, normalize_company, normalize_title


def job(company, title, score=0.0, source="t"):
    j = Job(source=source, title=title, company=company, url="u")
    j.score = score
    return j


@pytest.mark.parametrize("a,b", [
    ("Mutt Data", "muttdata"),
    ("SoftServe Inc.", "Softserve"),
    ("Grafana Labs", "grafanalabs"),
    ("Pedidos Ya S.A.", "PedidosYa"),
    ("Café Data", "Cafe Data"),
])
def test_company_variants_normalize_to_the_same_key(a, b):
    assert normalize_company(a) == normalize_company(b)


@pytest.mark.parametrize("a,b", [
    ("Data Engineer (Remote)", "data engineer"),
    ("Data Engineer - Remote", "Data  Engineer"),
    ("Analista de Datos Sr.", "analista de datos sr"),
    ("Científico de Datos", "Cientifico de Datos"),
])
def test_title_variants_normalize_to_the_same_key(a, b):
    assert normalize_title(a) == normalize_title(b)


@pytest.mark.parametrize("a,b", [
    ("Data Engineer", "Senior Data Engineer"),
    ("C++ Engineer", "C# Engineer"),
    ("データエンジニア", "セールス"),  # non-Latin titles must not collapse to an empty key
])
def test_different_titles_stay_different(a, b):
    assert normalize_title(a) != normalize_title(b)


def test_dedupe_merges_the_same_posting_from_two_sources_keeping_best_score():
    jobs = [job("Mutt Data", "Data Engineer Senior - Databricks", 50, "himalayas"),
            job("muttdata", "Data Engineer Senior - Databricks", 70, "lever:muttdata"),
            job("Mutt Data", "Data Engineer Lead - Databricks", 40, "himalayas")]
    kept = dedupe(jobs)
    assert len(kept) == 2
    assert {j.source for j in kept} == {"lever:muttdata", "himalayas"}
