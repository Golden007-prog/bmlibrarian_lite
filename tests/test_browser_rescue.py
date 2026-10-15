"""The browser rescue queue's pure rules (#480, stage 2)."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bmlibrarian_lite.browser_rescue import (
    OPEN_RESCUE_STATES,
    DismissOutcome,
    RescueEntry,
    RescueOutcome,
    RescueRequest,
    RescueState,
    RescueSubject,
    RescueVerdict,
    WaitOutcome,
    WaitResult,
    awaiting_reader_states,
    dismiss_entry,
    dismissed_state,
    download_verdict,
    fetchable_host,
    http_status_or_none,
    next_state,
    page_verdict,
    queue_challenged_copies,
    rescue_pdf_path,
    rescue_requests,
    stamp,
    title_checked_verdict,
    wait_outcome,
    wait_watch,
)
from bmlibrarian_lite.constants import (
    MAX_RESCUE_ATTEMPTS,
    SERVICE_OPENALEX_PDF,
    SERVICE_PDF_DOWNLOAD,
    SERVICE_UNPAYWALL_PDF,
)
from bmlibrarian_lite.data_models import (
    LookupRecord,
    RequestFailure,
    RequestFailureKind,
    SourceLookupFailure,
)
from bmlibrarian_lite.exceptions import SQLiteError
from bmlibrarian_lite.pdf_title_check import TitleCheck, TitleCheckOutcome
from bmlibrarian_lite.rescue_queue_store import RecordedVerdict

DOI = "10.1000/xyz"
ADDRESS = "https://www.sciencedirect.com/science/article/pii/S1/pdfft"
CHALLENGED = RequestFailure(RequestFailureKind.CHALLENGED, 403)
SUBJECT = RescueSubject("doc-1", DOI, "123", "A title")


def _record(*failures: SourceLookupFailure) -> LookupRecord:
    return LookupRecord(failures=failures)


def _challenged(address: str | None = ADDRESS, service: str = SERVICE_UNPAYWALL_PDF) -> SourceLookupFailure:
    return SourceLookupFailure(service, CHALLENGED, address)


# -- what is queued ----------------------------------------------------------


def test_a_challenged_open_access_copy_is_queued_with_its_host() -> None:
    """A challenged open-access copy is queued with its host."""
    assert rescue_requests(SUBJECT, _record(_challenged())) == (
        RescueRequest("doc-1", DOI, "123", "A title", ADDRESS, "www.sciencedirect.com", SERVICE_UNPAYWALL_PDF),
    )


def test_openalex_copies_are_queued_too() -> None:
    """OpenAlex's challenged copies are queued as well."""
    (request,) = rescue_requests(SUBJECT, _record(_challenged(service=SERVICE_OPENALEX_PDF)))
    assert request.service == SERVICE_OPENALEX_PDF


@pytest.mark.parametrize(
    "failure",
    [
        # An ordinary refusal is the host's answer, not a wall
        SourceLookupFailure(SERVICE_UNPAYWALL_PDF, RequestFailure(RequestFailureKind.HTTP_STATUS, 403), ADDRESS),
        # Not an open-access copy's PDF
        SourceLookupFailure(SERVICE_PDF_DOWNLOAD, CHALLENGED, ADDRESS),
        # No address to visit
        SourceLookupFailure(SERVICE_UNPAYWALL_PDF, CHALLENGED, None),
    ],
)
def test_only_a_challenged_open_access_copy_with_an_address_is_queued(failure: SourceLookupFailure) -> None:
    """Only a challenged, addressed, open-access copy is queued."""
    assert rescue_requests(SUBJECT, _record(failure)) == ()


@pytest.mark.parametrize(
    "address", ["ftp://repo.example.org/a.pdf", "/relative/a.pdf", "https:///a.pdf", "javascript:alert(1)"]
)
def test_rescue_requests_skip_addresses_a_browser_cannot_visit(address: str) -> None:
    """Addresses a browser cannot visit are not queued."""
    assert rescue_requests(SUBJECT, _record(_challenged(address))) == ()


def test_each_address_is_queued_once_in_record_order() -> None:
    """Each address is queued once, in the record's order."""
    other = "https://pubs.rsc.org/a.pdf"
    requests = rescue_requests(SUBJECT, _record(_challenged(), _challenged(other), _challenged()))
    assert [r.address for r in requests] == [ADDRESS, other]


def test_a_document_without_a_doi_queues_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """The DOI names the saved file; without one nothing could find it."""
    assert rescue_requests(RescueSubject("doc-1", None), _record(_challenged())) == ()
    assert "no DOI" in caplog.text


def test_a_subject_names_its_document() -> None:
    """A rescue subject must name its document."""
    with pytest.raises(ValueError):
        RescueSubject("  ", DOI)


def test_a_subject_strips_what_it_is_given() -> None:
    """A rescue subject strips the text it is given."""
    assert RescueSubject(" doc-1 ", "  ", " 1 ", None) == RescueSubject("doc-1", None, "1", "")  # type: ignore[arg-type]


# -- what a visit concluded --------------------------------------------------


def test_a_page_still_on_its_challenge_needs_a_person() -> None:
    """A page still on its challenge needs a person."""
    html = "<html><head><title>Just a moment...</title></head><script>window._cf_chl_opt={}</script></html>"
    assert page_verdict(403, {"Server": "cloudflare"}, html) == RescueVerdict(
        RescueOutcome.CHALLENGE, 403, ("cloudflare",)
    )


def test_cloudflares_refusal_is_a_challenge_whatever_its_page_says() -> None:
    """Cloudflare's refusal is a challenge whatever the page says."""
    verdict = page_verdict(403, {"Server": "cloudflare"}, "<html><style></style></html>")
    assert verdict.outcome is RescueOutcome.CHALLENGE


def test_an_unchallenged_page_has_no_pdf() -> None:
    """An unchallenged page with no PDF is recorded as such."""
    assert page_verdict(200, {}, "<html><title>Abstract</title></html>") == RescueVerdict(RescueOutcome.NO_PDF, 200)


@pytest.mark.parametrize("status", [404, 410])
def test_a_gone_status_is_gone_whatever_the_page_says(status: int) -> None:
    """A 404 or 410 is gone whatever the page says."""
    assert page_verdict(status, {}, "Just a moment...") == RescueVerdict(RescueOutcome.GONE, status)


def test_a_page_with_no_status_learnt_nothing() -> None:
    """No server answered: the page is the empty initial document, never no_pdf."""
    assert page_verdict(None, {}, "<p>nothing</p>") == RescueVerdict(RescueOutcome.TRANSPORT)
    assert page_verdict(None, {}, "") == RescueVerdict(RescueOutcome.TRANSPORT)
    assert page_verdict(None, {}, "Verify you are human") == RescueVerdict(RescueOutcome.TRANSPORT)


def test_only_the_first_64_kib_of_a_page_are_read() -> None:
    """Only the first 64 KiB of a page are read."""
    html = "x" * (64 * 1024) + "Just a moment..."
    assert page_verdict(200, {}, html).outcome is RescueOutcome.NO_PDF


def test_a_download_is_the_pdf_only_when_it_begins_with_the_magic_bytes() -> None:
    """A download is a PDF only when it begins with the magic bytes."""
    assert download_verdict(b"%PDF-1.7") == RescueVerdict(RescueOutcome.PDF)
    assert download_verdict(b"<html>An abstract.</html>") == RescueVerdict(RescueOutcome.NO_PDF)


def test_an_empty_download_learnt_nothing() -> None:
    """An empty file says nothing about the article: unparsed is not absent."""
    assert download_verdict(b"") == RescueVerdict(RescueOutcome.TRANSPORT)


def test_a_wall_served_as_a_download_waits_for_a_person() -> None:
    """A challenge page that arrived as a download is a challenge, not no_pdf."""
    verdict = download_verdict(b"<html><title>Just a moment...</title></html>")
    assert verdict == RescueVerdict(RescueOutcome.CHALLENGE, None, ("cloudflare",))


def test_only_the_first_64_kib_of_a_download_are_read_for_markers() -> None:
    """A download is read for markers as a page is: its first 64 KiB."""
    body = b"x" * (64 * 1024) + b"Just a moment..."
    assert download_verdict(body).outcome is RescueOutcome.NO_PDF


# -- how an entry moves -------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (RescueOutcome.PDF, RescueState.RESCUED),
        (RescueOutcome.CHALLENGE, RescueState.NEEDS_PERSON),
        (RescueOutcome.NO_PDF, RescueState.NO_PDF),
        (RescueOutcome.GONE, RescueState.GONE),
        (RescueOutcome.TOO_LARGE, RescueState.TOO_LARGE),
        (RescueOutcome.WRONG_PDF, RescueState.NEEDS_PERSON),
    ],
)
@pytest.mark.parametrize("state", sorted(OPEN_RESCUE_STATES, key=lambda s: s.value))
def test_an_answer_settles_an_open_entry(state: RescueState, outcome: RescueOutcome, expected: RescueState) -> None:
    """An answering outcome settles an open entry."""
    assert next_state(state, RescueVerdict(outcome), 1) is expected


def test_a_transport_failure_keeps_a_pending_entry_pending_until_the_cap() -> None:
    """A transport failure keeps a pending entry pending until the attempt cap."""
    transport = RescueVerdict(RescueOutcome.TRANSPORT)
    assert next_state(RescueState.PENDING, transport, MAX_RESCUE_ATTEMPTS - 1) is RescueState.PENDING
    assert next_state(RescueState.PENDING, transport, MAX_RESCUE_ATTEMPTS) is RescueState.NEEDS_PERSON


def test_a_transport_failure_leaves_an_entry_waiting_for_a_person_where_it_is() -> None:
    """A transport failure leaves an entry waiting for a person where it is."""
    assert next_state(RescueState.NEEDS_PERSON, RescueVerdict(RescueOutcome.TRANSPORT), 1) is RescueState.NEEDS_PERSON


@pytest.mark.parametrize("state", sorted(set(RescueState) - OPEN_RESCUE_STATES, key=lambda s: s.value))
def test_a_settled_entry_never_moves(state: RescueState) -> None:
    """A settled entry never moves."""
    assert next_state(state, RescueVerdict(RescueOutcome.PDF), 1) is state


# -- a stored verdict ---------------------------------------------------------


def test_a_verdict_round_trips() -> None:
    """A verdict survives a round trip through its dict form."""
    verdict = RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare", "captcha"))
    assert RescueVerdict.from_dict(verdict.to_dict()) == verdict


@pytest.mark.parametrize(
    "data", [None, [], "pdf", {"outcome": "teleported"}, {"outcome": ["pdf"]}, {}]
)
def test_an_unreadable_verdict_is_none(data: object) -> None:
    """An unreadable stored verdict is None."""
    assert RescueVerdict.from_dict(data) is None


def test_a_stored_verdict_keeps_only_what_it_can_vouch_for() -> None:
    """A stored verdict keeps only the fields it can vouch for."""
    data = {"outcome": "challenge", "http_status": True, "markers": ["captcha", 7, None]}
    assert RescueVerdict.from_dict(data) == RescueVerdict(RescueOutcome.CHALLENGE, None, ("captcha",))
    assert RescueVerdict.from_dict({"outcome": "gone", "http_status": 99}) == RescueVerdict(RescueOutcome.GONE)
    # Markers name a challenge's defences; on any other outcome they are dropped
    data = {"outcome": "no_pdf", "http_status": 200, "markers": ["captcha"]}
    assert RescueVerdict.from_dict(data) == RescueVerdict(RescueOutcome.NO_PDF, 200)


@pytest.mark.parametrize("status", [99, 1000, 0, -3])
def test_a_verdict_refuses_a_status_that_is_no_http_status(status: int) -> None:
    """A verdict is built only with a status from 100 to 999."""
    with pytest.raises(ValueError):
        RescueVerdict(RescueOutcome.NO_PDF, status)


def test_only_a_challenge_names_defences() -> None:
    """Markers on anything but a challenge are refused."""
    with pytest.raises(ValueError):
        RescueVerdict(RescueOutcome.PDF, None, ("cloudflare",))
    assert RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare",)).markers == ("cloudflare",)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(200, 200), (100, 100), (999, 999), (99, None), (1000, None), (True, None), ("200", None), (None, None)],
)
def test_http_status_or_none(value: object, expected: int | None) -> None:
    """Only an integer from 100 to 999 is an HTTP status."""
    assert http_status_or_none(value) == expected


# -- where a rescued PDF goes -------------------------------------------------


def test_a_rescued_pdf_lands_where_the_analysis_looks_first(tmp_path: Path) -> None:
    """The analysis asks discovery by DOI with no year: its expected path."""
    from bmlibrarian_lite.pdf_utils import find_existing_pdf

    path = rescue_pdf_path(DOI, tmp_path)
    path.write_bytes(b"%PDF-1.4")
    assert find_existing_pdf({"doi": DOI}, tmp_path) == path
    assert find_existing_pdf({"doi": DOI, "year": 2021}, tmp_path) == path


# -- queueing ------------------------------------------------------------------


class _Queue:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[tuple[RescueRequest, ...], int]] = []
        self.error = error

    def enqueue_rescues(self, requests, priority=0):  # type: ignore[no-untyped-def]
        if self.error is not None:
            raise self.error
        self.calls.append((tuple(requests), priority))
        return len(requests)


def test_challenged_copies_are_handed_to_the_queue() -> None:
    """Challenged copies are handed to the queue."""
    queue = _Queue()
    assert queue_challenged_copies(queue, SUBJECT, _record(_challenged())) == 1
    assert queue.calls == [(rescue_requests(SUBJECT, _record(_challenged())), 0)]


def test_nothing_challenged_asks_the_queue_nothing() -> None:
    """Nothing challenged means the queue is not asked."""
    queue = _Queue()
    assert queue_challenged_copies(queue, SUBJECT, LookupRecord()) == 0
    assert queue.calls == []


@pytest.mark.parametrize("error", [SQLiteError("locked"), sqlite3.OperationalError("locked")])
def test_a_queue_that_cannot_be_written_is_logged_not_raised(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    """The reader's sentence for the copy is already true; only a later try is lost."""
    assert queue_challenged_copies(_Queue(error), SUBJECT, _record(_challenged())) == 0
    assert "Could not queue" in caplog.text


def test_an_entry_carries_its_request() -> None:
    """A queue entry carries the request it was made from."""
    request = rescue_requests(SUBJECT, _record(_challenged()))[0]
    entry = RescueEntry(1, request, RescueState.PENDING, 0, None, 0, "t", "t")
    assert entry.request.address == ADDRESS


def test_a_loopback_visit_is_never_paced() -> None:
    """A visit to this machine is not somebody else's service (#480, stage 2)."""
    from bmlibrarian_lite.browser_rescue import rescue_delay_ms

    assert rescue_delay_ms("127.0.0.1") == 0
    assert rescue_delay_ms("localhost") == 0


def test_a_visit_takes_its_hosts_shared_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """The visit claims its host's slot from the shared limiter (#480, stage 2)."""
    from bmlibrarian_lite import browser_rescue

    claimed: list[str] = []

    class _Limiter:
        def claim(self) -> float:
            return 1.2345

    def _limiter_for(host: str) -> _Limiter:
        claimed.append(host)
        return _Limiter()

    monkeypatch.setattr(browser_rescue, "limiter_for", _limiter_for)
    assert browser_rescue.rescue_delay_ms("pubs.rsc.org") == 1235
    assert claimed == ["pubs.rsc.org"]


def _rescued_entry(document_id: str = "doc-1") -> RescueEntry:
    """A rescued entry for a document."""
    request = RescueRequest(document_id, DOI, "123", "A title", ADDRESS, "www.sciencedirect.com", SERVICE_UNPAYWALL_PDF)
    return RescueEntry(1, request, RescueState.RESCUED, 1, RescueVerdict(RescueOutcome.PDF), 0, "t", "t")


def test_a_rescued_document_is_reanalysed_by_its_doi() -> None:
    """A held document is re-analysed under its PMID and DOI."""
    from bmlibrarian_lite.browser_rescue import reanalyse_rescued

    asked: list[tuple[str, str | None, str | None]] = []
    assert reanalyse_rescued(_rescued_entry(), lambda doc_id: object(), lambda *a: asked.append(a) or True)
    assert asked == [("doc-1", "123", DOI)]


def test_a_rescue_of_a_document_storage_does_not_hold_is_not_reanalysed() -> None:
    """The MCP server queues under its own ID; analysing it would store a row for nothing."""
    from bmlibrarian_lite.browser_rescue import reanalyse_rescued

    asked: list[object] = []
    assert not reanalyse_rescued(_rescued_entry(f"doi-{DOI}"), lambda doc_id: None, lambda *a: asked.append(a))
    assert asked == []


def test_a_rescue_whose_reanalysis_was_not_queued_is_not_reported_as_asked() -> None:
    """The result is the manager's own answer, not just that storage holds the document."""
    from bmlibrarian_lite.browser_rescue import reanalyse_rescued

    assert not reanalyse_rescued(_rescued_entry(), lambda doc_id: object(), lambda *a: False)


# -- a page that says "not now" -----------------------------------------------


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503])
def test_a_temporary_error_page_with_no_marker_learns_nothing(status: int) -> None:
    """It stays pending under the retry delay and the attempt cap."""
    verdict = page_verdict(status, {"Server": "nginx"}, "<html>temporarily unavailable</html>")
    assert verdict == RescueVerdict(RescueOutcome.TRANSPORT, status)
    assert next_state(RescueState.PENDING, verdict, 1) is RescueState.PENDING


@pytest.mark.parametrize("status", [401, 403])
def test_a_refusal_with_no_marker_waits_for_a_person(status: int) -> None:
    """The HTTP client was walled here already: it is not settled as no_pdf."""
    verdict = page_verdict(status, {"Server": "nginx"}, "<html>forbidden</html>")
    assert verdict == RescueVerdict(RescueOutcome.CHALLENGE, status, ())


def test_an_answered_page_with_no_pdf_is_still_no_pdf() -> None:
    """Control: a 200 abstract page settles."""
    assert page_verdict(200, {}, "<html>An abstract.</html>") == RescueVerdict(RescueOutcome.NO_PDF, 200)


def test_a_503_behind_a_cloudflare_marker_is_still_a_challenge() -> None:
    """Control: markers win over the status."""
    verdict = page_verdict(503, {"Server": "cloudflare"}, "<html>Just a moment...</html>")
    assert verdict.outcome is RescueOutcome.CHALLENGE
    assert verdict.markers


def test_a_gone_page_stays_gone() -> None:
    """Control: 404 and 410."""
    assert page_verdict(410, {}, "").outcome is RescueOutcome.GONE


class _StubLimiter:
    """Records what penalised it."""

    def __init__(self) -> None:
        self.penalties: list[float | None] = []

    def penalise(self, retry_after: float | None = None) -> None:
        self.penalties.append(retry_after)


def _stub_limiters(monkeypatch: pytest.MonkeyPatch) -> dict[str, _StubLimiter]:
    from bmlibrarian_lite import browser_rescue

    limiters: dict[str, _StubLimiter] = {}
    monkeypatch.setattr(browser_rescue, "limiter_for", lambda host: limiters.setdefault(host, _StubLimiter()))
    return limiters


@pytest.mark.parametrize("status", [429, 503])
def test_a_throttling_answer_penalises_the_hosts_limiter(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    """The host's shared limiter is penalised with the Retry-After, case-insensitively."""
    from bmlibrarian_lite.browser_rescue import penalise_host_for_answer

    limiters = _stub_limiters(monkeypatch)
    assert penalise_host_for_answer(ADDRESS, status, {"retry-after": "30"})
    assert limiters["www.sciencedirect.com"].penalties == [30.0]


def test_a_throttle_without_retry_after_still_penalises(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Retry-After: the limiter halves, as for the HTTP client."""
    from bmlibrarian_lite.browser_rescue import penalise_host_for_answer

    limiters = _stub_limiters(monkeypatch)
    assert penalise_host_for_answer(ADDRESS, 429, {})
    assert limiters["www.sciencedirect.com"].penalties == [None]


def test_other_answers_and_loopback_do_not_penalise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: a 500 or a loopback host leaves the limiter alone."""
    from bmlibrarian_lite.browser_rescue import penalise_host_for_answer

    limiters = _stub_limiters(monkeypatch)
    assert not penalise_host_for_answer(ADDRESS, 500, {})
    assert not penalise_host_for_answer("http://127.0.0.1:8000/x", 503, {})
    assert limiters == {}


def test_the_transparency_report_with_a_challenged_lookup_serialises() -> None:
    """Lookup records are not part of the report's JSON (a regression guard)."""
    import json

    from bmlibrarian_lite.study_transparency_analyzer.study_transparency_analyzer import (
        TransparencyReport,
    )

    report = TransparencyReport(fulltext_lookups=_record(_challenged()))
    data = report.to_dict()
    json.dumps(data)
    assert "fulltext_lookups" not in data


def test_retry_after_is_read_from_headers_whatever_their_case() -> None:
    """The numeric form only; a date or nothing is None."""
    from bmlibrarian_lite.polite_session import retry_after_from_headers

    assert retry_after_from_headers({"Retry-After": "12"}) == 12.0
    assert retry_after_from_headers({"retry-after": "3"}) == 3.0
    assert retry_after_from_headers({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None
    assert retry_after_from_headers({}) is None


# -- stage 3: the window and the reader's wait ----------------------------------

T0 = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def _entry(entry_id: int, state: RescueState, retry_after: str | None = None) -> RescueEntry:
    request = RescueRequest("doc-1", DOI, "123", "A title", f"{ADDRESS}?{entry_id}",
                            "www.sciencedirect.com", SERVICE_UNPAYWALL_PDF)
    return RescueEntry(entry_id, request, state, 0, None, 0, "t", "t", retry_after)


@pytest.mark.parametrize(
    "address, host",
    [
        ("https://a.example/x.pdf", "a.example"),
        ("HTTP://A.example/x.pdf", "a.example"),
        ("ftp://a.example/x.pdf", None),
        ("/relative/x.pdf", None),
        ("https:///x.pdf", None),
        ("javascript:alert(1)", None),
        ("", None),
        (None, None),
    ],
)
def test_only_a_web_address_with_a_host_is_fetchable(address: str | None, host: str | None) -> None:
    """The window opens only what a browser can visit; a stored address is untrusted."""
    assert fetchable_host(address) == host


@pytest.mark.parametrize("state", sorted(OPEN_RESCUE_STATES, key=lambda s: s.value))
def test_an_open_entry_is_dismissed(state: RescueState) -> None:
    """An open entry is dismissed."""
    assert dismissed_state(state) is RescueState.DISMISSED


@pytest.mark.parametrize("state", [s for s in RescueState if s not in OPEN_RESCUE_STATES])
def test_a_settled_entry_stays_as_it_is(state: RescueState) -> None:
    """A settled entry is never reopened or rewritten by a dismissal."""
    assert dismissed_state(state) is state


class _Dismissals:
    def __init__(self, result: RecordedVerdict | Exception) -> None:
        self.result = result

    def dismiss_rescue(self, entry_id: int) -> RecordedVerdict:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_dismissing_returns_the_entry_it_changed() -> None:
    """A dismissal that applied returns the dismissed entry."""
    dismissed = _entry(1, RescueState.DISMISSED)
    result = dismiss_entry(_Dismissals(RecordedVerdict(dismissed, applied=True)), 1)
    assert result.outcome is DismissOutcome.DISMISSED and result.entry == dismissed


def test_dismissing_an_entry_no_longer_open_changes_nothing() -> None:
    """One another process settled first is not open, and not a failed write."""
    rescued = _entry(1, RescueState.RESCUED)
    result = dismiss_entry(_Dismissals(RecordedVerdict(rescued, applied=False)), 1)
    assert result.outcome is DismissOutcome.NOT_OPEN and result.entry == rescued


def test_dismissing_an_entry_that_is_gone_is_not_a_failed_write() -> None:
    """A row gone meanwhile is not open; the reader is not told a write failed."""
    result = dismiss_entry(_Dismissals(RecordedVerdict(None, applied=False)), 1)
    assert result.outcome is DismissOutcome.NOT_OPEN and result.entry is None


def test_a_dismissal_that_cannot_be_written_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    """The window reports the failure; nothing raises into Qt's slot."""
    result = dismiss_entry(_Dismissals(sqlite3.OperationalError("database is locked")), 1)
    assert result.outcome is DismissOutcome.FAILED and result.entry is None
    assert "could not be dismissed" in caplog.text


def test_an_applied_queue_change_needs_its_entry() -> None:
    """Applied with no entry would leave the caller nothing to report."""
    with pytest.raises(ValueError):
        RecordedVerdict(None, applied=True)


def test_with_a_hidden_pass_only_a_person_s_entries_wait_for_the_reader() -> None:
    """The hidden pass visits pending entries; only needs_person waits for the reader."""
    assert awaiting_reader_states(True) == frozenset({RescueState.NEEDS_PERSON})


def test_without_a_hidden_pass_every_open_entry_waits_for_the_reader() -> None:
    """With no hidden pass to visit them, pending entries wait for the reader too."""
    assert awaiting_reader_states(False) == OPEN_RESCUE_STATES


def test_the_tab_watches_pending_entries_due_now() -> None:
    """Only pending entries the hidden pass will visit now are waited for."""
    later = stamp(T0 + timedelta(minutes=5))
    earlier = stamp(T0 - timedelta(minutes=5))
    entries = [
        _entry(1, RescueState.PENDING),
        _entry(2, RescueState.PENDING, retry_after=later),
        _entry(3, RescueState.PENDING, retry_after=earlier),
        _entry(4, RescueState.NEEDS_PERSON),
    ]
    assert wait_watch(entries, True, T0) == frozenset({1, 3})


def test_without_a_hidden_pass_nothing_is_waited_for() -> None:
    """No pass will come, so the tab does not wait."""
    assert wait_watch([_entry(1, RescueState.PENDING)], False, T0) == frozenset()


def test_a_watched_entry_without_a_verdict_keeps_the_reader_waiting() -> None:
    """Waiting until the watched entry has had its visit."""
    assert wait_outcome([_entry(1, RescueState.PENDING)], {1}, set()).outcome is WaitOutcome.WAITING


def test_a_rescue_during_the_wait_ends_it() -> None:
    """One rescued address is enough, whatever the others do."""
    entries = [_entry(1, RescueState.RESCUED), _entry(2, RescueState.PENDING)]
    assert wait_outcome(entries, {1, 2}, {1}).outcome is WaitOutcome.RESCUED


def test_a_rescue_from_before_the_wait_is_not_this_wait_s() -> None:
    """A PDF rescued earlier is not on disk, or discovery would have found it."""
    assert wait_outcome([_entry(1, RescueState.RESCUED)], set(), set()).outcome is WaitOutcome.NOTHING


def test_a_transport_verdict_ends_the_wait_with_the_entry_still_open() -> None:
    """The entry stays pending, a retry is minutes away: the reader is offered the queue."""
    assert wait_outcome([_entry(1, RescueState.PENDING)], {1}, {1}).outcome is WaitOutcome.NEEDS_READER


def test_a_challenge_ends_the_wait_and_offers_the_queue() -> None:
    """A wall the hidden pass could not pass waits for the reader."""
    assert wait_outcome([_entry(1, RescueState.NEEDS_PERSON)], {1}, {1}).outcome is WaitOutcome.NEEDS_READER


def test_every_entry_settled_without_a_pdf_offers_nothing() -> None:
    """No PDF behind the wall: today's fallback."""
    entries = [_entry(1, RescueState.NO_PDF), _entry(2, RescueState.GONE)]
    assert wait_outcome(entries, {1, 2}, {1, 2}).outcome is WaitOutcome.NOTHING


def test_the_rescue_carries_the_entry_it_came_from() -> None:
    """The caller loads the PDF of the entry that decided the outcome, never one it looks for."""
    rescued = _entry(1, RescueState.RESCUED)
    result = wait_outcome([rescued, _entry(2, RescueState.PENDING)], {1, 2}, {1})
    assert result == WaitResult(WaitOutcome.RESCUED, rescued)


def test_the_offer_carries_an_open_entry() -> None:
    """The review queue opens on an entry that is still open."""
    open_entry = _entry(2, RescueState.NEEDS_PERSON)
    result = wait_outcome([_entry(1, RescueState.NO_PDF), open_entry], {1, 2}, {1, 2})
    assert result == WaitResult(WaitOutcome.NEEDS_READER, open_entry)


def test_at_its_limit_a_watched_rescue_not_heard_of_here_counts() -> None:
    """Another process recorded the rescue: no verdict reached this tab, yet its PDF is this wait's."""
    rescued = _entry(1, RescueState.RESCUED)
    result = wait_outcome([rescued], {1}, set(), final=True)
    assert result == WaitResult(WaitOutcome.RESCUED, rescued)


def test_at_its_limit_the_wait_never_goes_on() -> None:
    """A watched entry still pending is decided as open: the offer, not more waiting."""
    pending = _entry(1, RescueState.PENDING)
    assert wait_outcome([pending], {1}, set(), final=True) == WaitResult(WaitOutcome.NEEDS_READER, pending)


def test_at_its_limit_an_earlier_rescue_is_still_not_this_wait_s() -> None:
    """Control: final counts only watched entries, not one rescued before the wait."""
    assert wait_outcome([_entry(1, RescueState.RESCUED)], set(), set(), final=True).outcome is WaitOutcome.NOTHING


def test_a_wait_about_no_entry_is_refused() -> None:
    """An empty list is a failed queue write, never "every entry settled without a PDF"."""
    with pytest.raises(ValueError):
        wait_outcome([], set(), set())


@pytest.mark.parametrize(
    ("outcome", "with_entry"),
    [
        (WaitOutcome.RESCUED, False),
        (WaitOutcome.NEEDS_READER, False),
        (WaitOutcome.WAITING, True),
        (WaitOutcome.NOTHING, True),
    ],
)
def test_a_wait_result_holds_its_entry_with_its_outcome(outcome: WaitOutcome, with_entry: bool) -> None:
    """An outcome decided by an entry carries it; the others carry none."""
    with pytest.raises(ValueError):
        WaitResult(outcome, _entry(1, RescueState.PENDING) if with_entry else None)


def test_the_wait_lasts_while_any_watched_entry_is_unvisited() -> None:
    """Two addresses: one answered, one still to visit."""
    entries = [_entry(1, RescueState.NEEDS_PERSON), _entry(2, RescueState.PENDING)]
    assert wait_outcome(entries, {1, 2}, {1}).outcome is WaitOutcome.WAITING


def test_a_naive_moment_is_not_stamped() -> None:
    """Read as local time, it would compare wrongly with every stored stamp."""
    with pytest.raises(ValueError):
        stamp(datetime(2026, 10, 15, 12, 0))


def test_every_outcome_but_transport_settles_in_a_state_of_its_own() -> None:
    """The exhaustiveness the import-time assert held, now held whatever ``-O`` says."""
    from bmlibrarian_lite.browser_rescue import _SETTLED_BY

    assert set(_SETTLED_BY) | {RescueOutcome.TRANSPORT} == set(RescueOutcome)


@pytest.mark.parametrize(
    ("address", "host", "doi"),
    [
        ("file:///etc/passwd", "", DOI),
        ("javascript:alert(1)", "alert(1)", DOI),
        (ADDRESS, "elsewhere.example", DOI),
        (ADDRESS, "www.sciencedirect.com", " "),
    ],
)
def test_a_request_a_browser_cannot_trust_is_refused(address: str, host: str, doi: str) -> None:
    """A browser goes to the address, pacing goes by the host, and the DOI names the PDF."""
    with pytest.raises(ValueError):
        RescueRequest("doc-1", doi, None, "T", address, host, SERVICE_UNPAYWALL_PDF)


# -- a PDF checked against the title (#532) ---------------------------------------


@pytest.mark.parametrize(
    ("check", "outcome"),
    [
        (TitleCheck(TitleCheckOutcome.MATCHES, 1.0), RescueOutcome.PDF),
        (TitleCheck(TitleCheckOutcome.NO_TITLE), RescueOutcome.PDF),
        (TitleCheck(TitleCheckOutcome.NO_TEXT), RescueOutcome.PDF),
        (TitleCheck(TitleCheckOutcome.MISMATCH, 0.1), RescueOutcome.WRONG_PDF),
        (TitleCheck(TitleCheckOutcome.UNREADABLE), RescueOutcome.TRANSPORT),
    ],
)
def test_a_pdf_is_kept_only_when_it_is_not_another_article_s(
    check: TitleCheck, outcome: RescueOutcome
) -> None:
    """A mismatch is not kept; what the check cannot judge is; an unreadable file learnt nothing."""
    assert title_checked_verdict(check) == RescueVerdict(outcome)


def test_every_title_check_has_a_verdict() -> None:
    """No outcome of the check falls through."""
    for outcome in TitleCheckOutcome:
        share = 0.5 if outcome in {TitleCheckOutcome.MATCHES, TitleCheckOutcome.MISMATCH} else None
        assert isinstance(title_checked_verdict(TitleCheck(outcome, share)), RescueVerdict)


def test_a_wrong_pdf_waits_for_a_person_and_is_never_an_absence() -> None:
    """The right PDF may still be on the page: the entry stays open."""
    for state in OPEN_RESCUE_STATES:
        assert next_state(state, RescueVerdict(RescueOutcome.WRONG_PDF), 1) in OPEN_RESCUE_STATES


def test_a_wrong_pdf_verdict_round_trips() -> None:
    """Stored as its value, and read back."""
    verdict = RescueVerdict(RescueOutcome.WRONG_PDF)
    assert RescueVerdict.from_dict(verdict.to_dict()) == verdict
