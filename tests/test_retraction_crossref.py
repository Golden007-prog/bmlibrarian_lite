"""Crossref lookups over lite's transport: a stub session, never the network."""

from __future__ import annotations

import io
import json

import pytest
import requests
from bmlib.publications.retraction_status import CrossrefOutcome, crossref_key

from bmlibrarian_lite.constants import CROSSREF_MAX_RETRIES
from bmlibrarian_lite.data_models import RequestFailure, RequestFailureKind
from bmlibrarian_lite.rate_limit import (
    RateLimiter,
    _registry,
    policy_for_host,
    reset_limiters,
)
from bmlibrarian_lite.retraction_crossref import crossref_session, lookup_crossref

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


def test_connection_error_is_unreachable_with_a_connection_failure():
    """A wire error fails its batch with the typed kind."""
    out = lookup_crossref(
        ["10.1/a"], StubSession(lambda u: requests.ConnectionError("down")), mailto=MAILTO
    )
    assert out.results["10.1/a"].outcome is CrossrefOutcome.UNREACHABLE
    assert out.failures["10.1/a"].kind is RequestFailureKind.CONNECTION


def test_malformed_json_is_unreachable_and_a_served_batch_is_not():
    """An unreadable 200 body is MALFORMED_RESPONSE; the control has no failure."""
    out = lookup_crossref(["10.1/a"], StubSession(lambda u: (200, b"<html>")), mailto=MAILTO)
    assert out.results["10.1/a"].outcome is CrossrefOutcome.UNREACHABLE
    assert out.failures["10.1/a"] == RequestFailure(RequestFailureKind.MALFORMED_RESPONSE)
    assert lookup_crossref(["10.1/a"], StubSession(_served), mailto=MAILTO).failures == {}


def test_a_5xx_is_an_http_status_failure():
    """A 503 reaching lookup_crossref is recorded with its status."""
    out = lookup_crossref(["10.1/a"], StubSession(lambda u: (503, b"")), mailto=MAILTO)
    assert out.failures["10.1/a"] == RequestFailure(RequestFailureKind.HTTP_STATUS, 503)


def test_one_unreadable_item_in_a_200_is_malformed_for_it_alone():
    """A DOI whose own relations are unreadable fails alone; siblings are served."""

    def answer(url):
        items = [
            {"DOI": "10.1/bad", "updated-by": "not a list"},
            {"DOI": "10.1/good"},
        ]
        body = {"status": "ok", "message-type": "work-list", "message": {"items": items}}
        return 200, json.dumps(body).encode()

    out = lookup_crossref(["10.1/bad", "10.1/good"], StubSession(answer), mailto=MAILTO)
    assert out.results["10.1/bad"].outcome is CrossrefOutcome.UNREACHABLE
    assert out.failures == {"10.1/bad": RequestFailure(RequestFailureKind.MALFORMED_RESPONSE)}
    assert out.results["10.1/good"].outcome is CrossrefOutcome.SERVED


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
    out = lookup_crossref(
        _dois(120), session, mailto=MAILTO, should_cancel=lambda: len(session.urls) >= 1
    )
    assert len(session.urls) == 1
    assert len(out.results) == 50


def _scripted_session(statuses, headers=None):
    """A real crossref_session whose wire answers the scripted statuses."""
    session = crossref_session(MAILTO)
    adapter = session.adapters["https://"]
    sent = []

    def send_once(request, **kwargs):
        status = statuses.pop(0) if statuses else statuses_last[0]
        sent.append(status)
        response = requests.Response()
        response.status_code = status
        response.headers.update(headers or {})
        response.raw = io.BytesIO()  # the adapter closes a replaced throttle
        response._content = _served(request.url)[1] if status == 200 else b""
        return response

    statuses_last = [statuses[-1]]
    adapter._send_once = send_once
    return session, sent


@pytest.fixture
def no_sleep_limiter():
    """Crossref's limiter over a no-op sleep, so retries never really wait."""
    reset_limiters()
    _registry["api.crossref.org"] = RateLimiter(
        policy_for_host("api.crossref.org"), sleep=lambda seconds: None
    )
    yield
    reset_limiters()


def test_a_429_with_retry_after_is_retried_into_served_results(no_sleep_limiter):
    """The session's one budget absorbs a throttle: no failure recorded."""
    session, sent = _scripted_session([429, 200], {"Retry-After": "0"})
    out = lookup_crossref(["10.1/a"], session, mailto=MAILTO)
    assert sent == [429, 200]
    assert out.results["10.1/a"].outcome is CrossrefOutcome.SERVED
    assert out.failures == {}


def test_a_persistent_503_ends_as_http_status_after_the_budget(no_sleep_limiter):
    """Retries are bounded; the last answer is reported with its status."""
    session, sent = _scripted_session([503])
    out = lookup_crossref(["10.1/a"], session, mailto=MAILTO)
    assert len(sent) == CROSSREF_MAX_RETRIES + 1
    assert out.failures["10.1/a"] == RequestFailure(RequestFailureKind.HTTP_STATUS, 503)


def test_the_session_names_the_app_and_the_mailto():
    """The User-Agent carries the contact address."""
    agent = crossref_session(MAILTO).headers["User-Agent"]
    assert agent.startswith("bmlibrarian-lite/") and f"(mailto:{MAILTO})" in agent
