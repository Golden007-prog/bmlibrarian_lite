"""Crossref lookups over lite's transport: a stub session, never the network."""

from __future__ import annotations

import json

import requests
from bmlib.publications.retraction_status import CrossrefOutcome, crossref_key

from bmlibrarian_lite.data_models import RequestFailure, RequestFailureKind
from bmlibrarian_lite.retraction_crossref import lookup_crossref

MAILTO = "t@example.org"


class StubSession:
    """Answers every GET by a function of its URL; records the URLs."""

    def __init__(self, answer):
        """Keep the answering function."""
        self.answer = answer
        self.urls: list[str] = []

    def get(self, url, timeout=None):
        """Answer or raise as the function says."""
        self.urls.append(url)
        result = self.answer(url)
        if isinstance(result, Exception):
            raise result
        status, body = result
        response = requests.Response()
        response.status_code = status
        response._content = body
        return response


def _served(url: str) -> tuple[int, bytes]:
    filters = url.split("filter=")[1].split("&")[0]
    items = [{"DOI": f.removeprefix("doi:")} for f in filters.split(",")]
    body = {"status": "ok", "message-type": "work-list", "message": {"items": items}}
    return 200, json.dumps(body).encode()


def _dois(n: int) -> list[str]:
    return [f"10.1000/x{i}" for i in range(n)]


def test_served_batch_gives_served_results_keyed_by_crossref_key():
    """A served batch gives SERVED results under the normalised key."""
    out = lookup_crossref(["https://doi.org/10.1000/ABC"], StubSession(_served), mailto=MAILTO)
    assert out.results[crossref_key("10.1000/abc")].outcome is CrossrefOutcome.SERVED
    assert out.failures == {}


def test_one_failed_batch_leaves_only_its_dois_unanswered():
    """A 429 on one batch fails only its 50 DOIs."""
    dois = _dois(120)
    calls = []

    def answer(url):
        calls.append(url)
        return (429, b"") if len(calls) == 2 else _served(url)

    out = lookup_crossref(dois, StubSession(answer), mailto=MAILTO)
    bad = [d for d, r in out.results.items() if r.outcome is CrossrefOutcome.UNREACHABLE]
    assert len(bad) == 50
    assert all(out.failures[d] == RequestFailure(RequestFailureKind.HTTP_STATUS, 429) for d in bad)
    served = [d for d, r in out.results.items() if r.outcome is CrossrefOutcome.SERVED]
    assert len(served) == 70 and not set(served) & set(out.failures)


def test_connection_error_and_malformed_json_are_unreachable():
    """Wire and body failures are unreachable with a typed failure."""
    for dois, kind in ((["10.1/a"], RequestFailureKind.MALFORMED_RESPONSE),):
        out = lookup_crossref(dois, StubSession(lambda u: (200, b"<html>")), mailto=MAILTO)
        key = crossref_key(dois[0])
        assert out.results[key].outcome is CrossrefOutcome.UNREACHABLE
        assert out.failures[key] == RequestFailure(kind)
    out = lookup_crossref(
        ["10.1/a"], StubSession(lambda u: requests.ConnectionError("down")), mailto=MAILTO
    )
    assert out.results["10.1/a"].outcome is CrossrefOutcome.UNREACHABLE
    assert out.failures["10.1/a"].kind is RequestFailureKind.CONNECTION
    ok = lookup_crossref(["10.1/a"], StubSession(_served), mailto=MAILTO)
    assert ok.failures == {}


def test_a_comma_doi_is_never_sent():
    """A comma DOI is never requested and has no entry."""
    session = StubSession(_served)
    out = lookup_crossref(["10.1/a,b", "10.1/c"], session, mailto=MAILTO)
    assert not any("a,b" in u or "a%2Cb" in u or "10.1/a" in u for u in session.urls)
    assert "10.1/a,b" not in out.results and "10.1/a,b" not in out.failures
    assert "10.1/c" in out.results


def test_cancel_between_batches_stops_sending():
    """A cancel stops before the next batch; unasked DOIs get no entry."""
    session = StubSession(_served)
    out = lookup_crossref(_dois(120), session, mailto=MAILTO, should_cancel=lambda: len(session.urls) >= 1)
    assert len(session.urls) == 1
    assert len(out.results) == 50
