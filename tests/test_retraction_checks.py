"""Stored retraction verdicts and the checker (stub sessions, never the network)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
import requests
from bmlib.publications.retraction_status import (
    RetractionEvidence,
    RetractionKind,
    RetractionLookup,
    RetractionSource,
    RetractionStatus,
    RetractionVerdict,
)

from bmlibrarian_lite.config import LiteConfig
from bmlibrarian_lite.constants import RETRACTION_WATCH_EXPORT_URL
from bmlibrarian_lite.data_models import LiteDocument, RequestFailure, RequestFailureKind
from bmlibrarian_lite.retraction_checks import PaperCheck, RetractionChecker, paper_key
from bmlibrarian_lite.retraction_export import ExportState, refresh_export
from bmlibrarian_lite.storage import LiteStorage

_HEADER = (
    "Record ID,Title,Subject,Institution,Journal,Publisher,Country,Author,URLS,"
    "ArticleType,RetractionDate,RetractionDOI,RetractionPubMedID,OriginalPaperDate,"
    "OriginalPaperDOI,OriginalPaperPubMedID,RetractionNature,Reason,Paywalled,Notes,\n"
)
_ROW = (
    "1,A paper,Subject,Inst,Journal,Pub,AU,Author,URL,Article,"
    "3/9/2026 0:00,10.9/notice,87654321,5/6/2023 0:00,10.9/retracted,12345678,"
    "Retraction,Reason;,No,Notes,\n"
)
_CONCERN_ROW = (
    "2,Another,Subject,Inst,Journal,Pub,AU,Author,URL,Article,"
    "3/9/2026 0:00,10.9/eoc,22222223,5/6/2023 0:00,10.9/concern,22222222,"
    "Expression of concern,Reason;,No,Notes,\n"
)
CSV = (_HEADER + _ROW + _CONCERN_ROW).encode()
START = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
MAILTO = "t@example.org"


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status_code, self._body, self.headers = status, body, {}
        self.content = body

    def iter_content(self, size: int):
        yield self._body

    def close(self) -> None:
        pass


def _served(url: str) -> _Response:
    filters = url.split("filter=")[1].split("&")[0]
    items = [{"DOI": f.removeprefix("doi:")} for f in filters.split(",")]
    body = {"status": "ok", "message-type": "work-list", "message": {"items": items}}
    return _Response(200, json.dumps(body).encode())


class StubSession:
    """The export answers ``export_status``; Crossref answers by ``crossref``."""

    def __init__(self, crossref=_served, export_status: int = 200) -> None:
        """Keep the answers; count the calls."""
        self.crossref, self.export_status = crossref, export_status
        self.crossref_urls: list[str] = []
        self.export_calls = 0

    def get(self, url, **kwargs):
        """Answer the export or Crossref."""
        if url.startswith(RETRACTION_WATCH_EXPORT_URL):
            self.export_calls += 1
            return _Response(self.export_status, CSV)
        self.crossref_urls.append(url)
        result = self.crossref(url)
        if isinstance(result, Exception):
            raise result
        return result


def _unreachable(url: str):
    return requests.ConnectionError("down")


class Clock:
    """A clock the test moves."""

    def __init__(self) -> None:
        """Start at START."""
        self.now = START

    def __call__(self) -> datetime:
        """The current time."""
        return self.now


@pytest.fixture
def storage(tmp_path):
    """A fresh store under tmp_path."""
    config = LiteConfig()
    config.storage.data_dir = tmp_path / "store"
    return LiteStorage(config)


def _current_export(tmp_path) -> None:
    copy = refresh_export(tmp_path, StubSession(), mailto=MAILTO, today=START.date())
    assert copy.state is ExportState.CURRENT


def _doc(doc_id: str, doi: str | None = None, pmid: str | None = None, types=None):
    return LiteDocument(
        id=doc_id, title="t", abstract="a", doi=doi, pmid=pmid, publication_types=types
    )


def _checker(storage, tmp_path, session, clock=None):
    return RetractionChecker(storage, tmp_path, session, mailto=MAILTO, now=clock or Clock())


def test_documents_sharing_a_doi_are_asked_once(storage, tmp_path):
    """Two spellings of one DOI make one request, and both ids get the verdict."""
    _current_export(tmp_path)
    session = StubSession()
    docs = [_doc("a", doi="10.1/A"), _doc("b", doi="https://doi.org/10.1/a")]
    out = _checker(storage, tmp_path, session).check(docs)
    assert len(session.crossref_urls) == 1
    assert session.crossref_urls[0].count("doi:") == 1
    assert out["a"].verdict.status is RetractionStatus.CLEAR
    assert out["a"] == out["b"]


def test_a_stored_verdict_younger_than_7_days_is_reused(storage, tmp_path):
    """Reused up to 7 days; asked again at 7 days + 1 s, and always on refresh."""
    _current_export(tmp_path)
    session, clock = StubSession(), Clock()
    checker = _checker(storage, tmp_path, session, clock)
    docs = [_doc("a", doi="10.1/a")]
    first = checker.check(docs)["a"]
    clock.now = START + timedelta(days=7)
    assert checker.check(docs)["a"] == first
    assert len(session.crossref_urls) == 1
    assert len(_checker(storage, tmp_path, session, clock).check(docs, refresh=True)) == 1
    assert len(session.crossref_urls) == 2
    clock.now = START + timedelta(days=14, seconds=1)
    later = checker.check(docs)["a"]
    assert len(session.crossref_urls) == 3
    assert later.checked_at == clock.now


def test_unchecked_is_never_stored_as_clear(storage, tmp_path):
    """Crossref unreachable: a stored unchecked with its failure; served: clear."""
    _current_export(tmp_path)
    docs = [_doc("a", doi="10.1/a")]
    out = _checker(storage, tmp_path, StubSession(_unreachable)).check(docs)
    check = out["a"]
    assert check.verdict.status is RetractionStatus.UNCHECKED
    assert check.verdict.unanswered == (RetractionLookup.CROSSREF,)
    assert check.crossref_failure == RequestFailure(RequestFailureKind.CONNECTION)
    stored = storage.retraction_checks(["doi:10.1/a"])["doi:10.1/a"]
    assert stored.verdict.status is RetractionStatus.UNCHECKED
    assert stored.crossref_failure == RequestFailure(RequestFailureKind.CONNECTION)
    # control: the served path decides clear, and the export date is kept
    served = _checker(storage, tmp_path, StubSession()).check(docs)["a"]
    assert served.verdict.status is RetractionStatus.CLEAR
    assert served.crossref_failure is None
    assert served.export_data_as_of == START.date()
    stored = storage.retraction_checks(["doi:10.1/a"])["doi:10.1/a"]
    assert stored == served


def test_an_unconsulted_export_is_never_clear(storage, tmp_path):
    """Crossref served but the export missing and its refresh refused: unchecked."""
    session = StubSession(export_status=500)
    check = _checker(storage, tmp_path, session).check([_doc("a", doi="10.1/a")])["a"]
    assert check.verdict.status is RetractionStatus.UNCHECKED
    assert check.verdict.unanswered == (RetractionLookup.RETRACTION_WATCH_EXPORT,)
    assert check.export_data_as_of is None


def test_a_stored_unchecked_row_is_asked_again_on_every_check(storage, tmp_path):
    """A failure is not knowledge: the next check, minutes later, asks again."""
    _current_export(tmp_path)
    session, clock = StubSession(_unreachable), Clock()
    checker = _checker(storage, tmp_path, session, clock)
    docs = [_doc("a", doi="10.1/a")]
    checker.check(docs)
    clock.now = START + timedelta(minutes=1)
    session.crossref = _served
    assert checker.check(docs)["a"].verdict.status is RetractionStatus.CLEAR
    assert len(session.crossref_urls) == 2
    # control: a decided row is not asked again a minute later
    clock.now = START + timedelta(minutes=2)
    checker.check(docs)
    assert len(session.crossref_urls) == 2


def test_ensure_export_downloads_once_per_checker_when_stale(storage, tmp_path):
    """A failed refresh is not retried by the same checker."""
    session = StubSession(export_status=500)
    checker = _checker(storage, tmp_path, session)
    assert checker.ensure_export().state is ExportState.MISSING
    assert checker.ensure_export().state is ExportState.MISSING
    checker.check([_doc("a", doi="10.1/a")])
    assert session.export_calls == 1
    # control: a new checker tries again, and a served download makes it current
    session.export_status = 200
    assert _checker(storage, tmp_path, session).ensure_export().state is ExportState.CURRENT
    assert session.export_calls == 2


def test_a_current_export_is_not_downloaded(storage, tmp_path):
    """A current copy is used as it is."""
    _current_export(tmp_path)
    session = StubSession()
    assert _checker(storage, tmp_path, session).ensure_export().state is ExportState.CURRENT
    assert session.export_calls == 0


def test_the_export_decides_retracted(storage, tmp_path):
    """A paper the export holds is retracted, found by DOI or by PMID alone."""
    _current_export(tmp_path)
    docs = [_doc("a", doi="10.9/retracted"), _doc("b", pmid="12345678")]
    out = _checker(storage, tmp_path, StubSession()).check(docs)
    assert out["a"].verdict.status is RetractionStatus.RETRACTED
    assert out["b"].verdict.status is RetractionStatus.RETRACTED
    assert out["b"].verdict.deciding is not None
    stored = storage.retraction_checks(["pmid:12345678"])["pmid:12345678"]
    assert stored.verdict == out["b"].verdict


def test_label_alone_retracts_with_everything_unreachable(storage, tmp_path):
    """PubMed's label retracts with no export and Crossref down."""
    session = StubSession(_unreachable, export_status=500)
    doc = _doc("a", doi="10.1/a", types=["Retracted Publication"])
    check = _checker(storage, tmp_path, session).check([doc])["a"]
    assert check.verdict.status is RetractionStatus.RETRACTED
    # control: without the label, the same failures leave it unchecked
    plain = _checker(storage, tmp_path, session).check([_doc("b", doi="10.1/b")])["b"]
    assert plain.verdict.status is RetractionStatus.UNCHECKED


def test_a_new_retracted_label_overrides_a_stored_clear(storage, tmp_path):
    """A record now labelled retracted is checked again despite a fresh clear row."""
    _current_export(tmp_path)
    session = StubSession()
    checker = _checker(storage, tmp_path, session)
    checker.check([_doc("a", doi="10.1/a")])
    labelled = _doc("a", doi="10.1/a", types=["Retracted Publication"])
    assert checker.check([labelled])["a"].verdict.status is RetractionStatus.RETRACTED
    assert len(session.crossref_urls) == 2


def test_no_key_is_computed_and_not_stored(storage, tmp_path):
    """No DOI and no canonical PMID: a verdict, unchecked, never stored."""
    _current_export(tmp_path)
    session = StubSession()
    docs = [_doc("none"), _doc("bad", pmid="PMID:123"), _doc("pmc", pmid="PMC123")]
    out = _checker(storage, tmp_path, session).check(docs)
    assert {d: c.verdict.status for d, c in out.items()} == {
        "none": RetractionStatus.UNCHECKED,
        "bad": RetractionStatus.UNCHECKED,
        "pmc": RetractionStatus.UNCHECKED,
    }
    assert [paper_key(d) for d in docs] == [None, None, None]
    assert session.crossref_urls == []
    with sqlite3.connect(storage.config.storage.sqlite_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM retraction_checks").fetchone()[0] == 0


def test_paper_key_prefers_the_doi_then_the_canonical_pmid():
    """The DOI's Crossref key first; else the PMID without leading zeros."""
    assert paper_key(_doc("a", doi=" https://doi.org/10.1/AB ", pmid="1")) == "doi:10.1/ab"
    assert paper_key(_doc("a", pmid="0012345")) == "pmid:12345"


def test_a_cancelled_check_stores_only_decided_rows(storage, tmp_path):
    """No row for a paper whose batch was never asked; a PMID-only paper is decided."""
    _current_export(tmp_path)
    session = StubSession()
    docs = [_doc("a", doi="10.1/a"), _doc("b", pmid="12345678")]
    out = _checker(storage, tmp_path, session).check(docs, should_cancel=lambda: True)
    assert session.crossref_urls == []
    assert set(out) == {"b"}
    assert storage.retraction_checks(["doi:10.1/a", "pmid:12345678"]).keys() == {"pmid:12345678"}


def test_an_unreadable_stored_row_is_checked_again(storage, tmp_path):
    """A row this build cannot read is no verdict: the paper is asked again."""
    _current_export(tmp_path)
    session = StubSession()
    checker = _checker(storage, tmp_path, session)
    docs = [_doc("a", doi="10.1/a")]
    checker.check(docs)
    with sqlite3.connect(storage.config.storage.sqlite_path) as conn:
        conn.execute("UPDATE retraction_checks SET status = 'maybe'")
    assert checker.check(docs)["a"].verdict.status is RetractionStatus.CLEAR
    assert len(session.crossref_urls) == 2


def test_schema_is_idempotent(tmp_path):
    """Opening storage twice keeps the table and its rows."""
    config = LiteConfig()
    config.storage.data_dir = tmp_path / "store"
    _current_export(tmp_path)
    _checker(LiteStorage(config), tmp_path, StubSession()).check([_doc("a", doi="10.1/a")])
    again = LiteStorage(config)
    assert again.retraction_checks(["doi:10.1/a"])["doi:10.1/a"].verdict.status is (
        RetractionStatus.CLEAR
    )


_NOTICE = RetractionEvidence(RetractionSource.CROSSREF_PUBLISHER, RetractionKind.RETRACTION, "2026-01")
_CONCERN = RetractionEvidence(
    RetractionSource.CROSSREF_PUBLISHER, RetractionKind.EXPRESSION_OF_CONCERN, "2026-01"
)
_KEY = "doi:10.1/a"


def _store(storage, status, deciding=None, unanswered=(), age=timedelta(days=8), key=_KEY):
    """Store a crafted verdict under *key*, checked *age* before START."""
    check = PaperCheck(
        RetractionVerdict(status, deciding, tuple(unanswered)), START - age, START.date(), None
    )
    storage.store_retraction_checks({key: check})
    return check


def test_an_outage_never_erases_a_stored_retraction(storage, tmp_path):
    """Crossref down on the re-check: the row is unchanged and still says retracted."""
    _current_export(tmp_path)
    old = _store(storage, RetractionStatus.RETRACTED, _NOTICE)
    session = StubSession(_unreachable)
    out = _checker(storage, tmp_path, session).check([_doc("a", doi="10.1/a")])
    assert out["a"] == old
    assert storage.retraction_checks([_KEY])[_KEY] == old
    assert len(session.crossref_urls) == 1


def test_an_outage_does_not_extend_a_stored_clear(storage, tmp_path):
    """An expired clear is kept as it was, but the reader is told unchecked, and why."""
    _current_export(tmp_path)
    old = _store(storage, RetractionStatus.CLEAR)
    out = _checker(storage, tmp_path, StubSession(_unreachable)).check([_doc("a", doi="10.1/a")])
    assert out["a"].verdict.status is RetractionStatus.UNCHECKED
    assert out["a"].crossref_failure == RequestFailure(RequestFailureKind.CONNECTION)
    assert storage.retraction_checks([_KEY])[_KEY] == old


def test_a_new_answer_replaces_a_stored_concern(storage, tmp_path):
    """Control: an answered re-check overwrites, here a concern Crossref no longer holds."""
    _current_export(tmp_path)
    _store(storage, RetractionStatus.CONCERN, _CONCERN)
    out = _checker(storage, tmp_path, StubSession()).check([_doc("a", doi="10.1/a")])
    assert out["a"].verdict.status is RetractionStatus.CLEAR
    assert storage.retraction_checks([_KEY])[_KEY] == out["a"]


def test_a_decided_verdict_with_a_lookup_unanswered_is_asked_again(storage, tmp_path):
    """A fresh concern is reused only when every lookup answered it."""
    _current_export(tmp_path)
    session = StubSession()
    docs = [_doc("a", doi="10.1/a")]
    _store(storage, RetractionStatus.CONCERN, _CONCERN, age=timedelta(hours=1))
    _checker(storage, tmp_path, session).check(docs)
    assert session.crossref_urls == []
    _store(storage, RetractionStatus.CONCERN, _CONCERN, [RetractionLookup.CROSSREF], timedelta(hours=1))
    _checker(storage, tmp_path, session).check(docs)
    assert len(session.crossref_urls) == 1


def test_a_cancelled_check_does_not_download_the_export(storage, tmp_path):
    """A cancel never waits for the export; a check not cancelled downloads it."""
    session = StubSession()
    docs = [_doc("a", doi="10.1/a")]
    _checker(storage, tmp_path, session).check(docs, should_cancel=lambda: True)
    assert session.export_calls == 0
    _checker(storage, tmp_path, session).check(docs)
    assert session.export_calls == 1


def _recheck(storage, tmp_path, doc, refresh=False):
    """Re-check *doc* with Crossref down; return the check and the stored row."""
    out = _checker(storage, tmp_path, StubSession(_unreachable)).check([doc], refresh=refresh)
    key = paper_key(doc)
    return out[doc.id], storage.retraction_checks([key])[key]


def test_a_fresh_retraction_replaces_a_stored_concern_during_an_outage(storage, tmp_path):
    """Crossref down, the export now says retracted: stored and returned retracted."""
    _current_export(tmp_path)
    _store(storage, RetractionStatus.CONCERN, _CONCERN, key="doi:10.9/retracted")
    check, row = _recheck(storage, tmp_path, _doc("a", doi="10.9/retracted"))
    assert check.verdict.status is RetractionStatus.RETRACTED
    assert row == check


def test_a_fresh_retraction_replaces_a_fresh_clear_on_refresh(storage, tmp_path):
    """Refresh with Crossref down: the export's or the label's retraction is stored."""
    _current_export(tmp_path)
    _store(storage, RetractionStatus.CLEAR, age=timedelta(hours=1), key="doi:10.9/retracted")
    check, row = _recheck(storage, tmp_path, _doc("a", doi="10.9/retracted"), refresh=True)
    assert check.verdict.status is RetractionStatus.RETRACTED and row == check
    _store(storage, RetractionStatus.CLEAR, age=timedelta(hours=1))
    labelled = _doc("b", doi="10.1/a", types=["Retracted Publication"])
    check, row = _recheck(storage, tmp_path, labelled, refresh=True)
    assert check.verdict.status is RetractionStatus.RETRACTED and row == check


def test_a_fresh_concern_replaces_a_stored_clear_during_an_outage(storage, tmp_path):
    """Crossref down, the export now holds a concern: stored and returned concern."""
    _current_export(tmp_path)
    _store(storage, RetractionStatus.CLEAR, key="doi:10.9/concern")
    check, row = _recheck(storage, tmp_path, _doc("a", doi="10.9/concern"))
    assert check.verdict.status is RetractionStatus.CONCERN
    assert row == check


def test_a_fresh_concern_does_not_replace_a_stored_retraction_during_an_outage(
    storage, tmp_path
):
    """Control: a concern seen while Crossref is down never downgrades a retraction."""
    _current_export(tmp_path)
    old = _store(storage, RetractionStatus.RETRACTED, _NOTICE, key="doi:10.9/concern")
    check, row = _recheck(storage, tmp_path, _doc("a", doi="10.9/concern"))
    assert check == old and row == old
