"""Screening by retraction verdict and the reader's sentences (pure)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from bmlib.publications.retraction_status import (
    RetractionEvidence,
    RetractionKind,
    RetractionLookup,
    RetractionSource,
    RetractionStatus,
    RetractionVerdict,
)

from bmlibrarian_lite.data_models import LiteDocument
from bmlibrarian_lite.retraction_screen import RetractionScreen, screen_documents
from bmlibrarian_lite.retraction_store import PaperCheck

WHEN = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)
BOTH = (RetractionLookup.RETRACTION_WATCH_EXPORT, RetractionLookup.CROSSREF)


def _doc(i: str) -> LiteDocument:
    """Doc."""
    return LiteDocument(id=i, title=f"Title {i}", abstract="", doi=f"10.1/{i}", pmid="1")


def _check(status, deciding=None, unanswered=(), failure=None, asof=date(2026, 10, 1), at=WHEN):
    """Check."""
    return PaperCheck(RetractionVerdict(status, deciding, unanswered), at, asof, failure)


def _evidence(notice="10.9/n", day="2026-03-09", kind=RetractionKind.RETRACTION):
    """Evidence."""
    return RetractionEvidence(
        source=RetractionSource.RETRACTION_WATCH_EXPORT, kind=kind, date=day, notice_doi=notice
    )


def test_no_identifier_is_unchecked_and_counted():
    """Test no identifier is unchecked and counted."""
    docs = [_doc("a"), _doc("b")]
    checks = {"a": _check(RetractionStatus.UNCHECKED, unanswered=BOTH)}
    screen = screen_documents(docs, checks)
    assert screen.unchecked_count == 2  # "b" has no check at all
    assert screen.kept == tuple(docs)


def test_retracted_is_excluded_and_concern_kept():
    """Test retracted is excluded and concern kept."""
    docs = [_doc("a"), _doc("b"), _doc("c"), _doc("d")]
    checks = {
        "a": _check(RetractionStatus.RETRACTED, _evidence()),
        "b": _check(RetractionStatus.CONCERN, _evidence(kind=RetractionKind.EXPRESSION_OF_CONCERN)),
        "c": _check(RetractionStatus.CLEAR),
        "d": _check(RetractionStatus.RETRACTED, _evidence()),
    }
    screen = screen_documents(docs, checks)
    assert [d.id for d in screen.excluded] == ["a", "d"]
    assert [d.id for d in screen.kept] == ["b", "c"]
    assert screen.concern_ids == ("b",)
    assert screen.unchecked_count == 0


def test_checked_at_is_the_oldest():
    """Test checked at is the oldest."""
    old = datetime(2026, 1, 2, tzinfo=UTC)
    screen = screen_documents(
        [_doc("a"), _doc("b")],
        {"a": _check(RetractionStatus.CLEAR), "b": _check(RetractionStatus.CLEAR, at=old)},
    )
    assert screen.checked_at == old
    assert screen_documents([], {}).checked_at is None


def test_snapshot_round_trips():
    """Test snapshot round trips."""
    docs = [_doc("a"), _doc("b")]
    screen = screen_documents(
        docs,
        {
            "a": _check(RetractionStatus.RETRACTED, _evidence()),
            "b": _check(RetractionStatus.UNCHECKED, unanswered=BOTH),
        },
    )
    rows = screen.to_snapshot()
    back = RetractionScreen.from_snapshot(rows)
    assert set(back) == {"a", "b"}
    assert back["a"]["status"] == "retracted"
    assert back["a"]["title"] == "Title a"
    assert back["a"]["checked_at"] == WHEN.isoformat()
    assert RetractionEvidence.from_dict(back["a"]["deciding"]) == _evidence()
    assert back["b"]["deciding"] is None


def test_snapshot_tolerates_older_rows(caplog):
    """Test snapshot tolerates older rows."""
    got = RetractionScreen.from_snapshot([{"document_id": "x"}, {"document_id": "y", "status": "clear"}])
    assert list(got) == ["y"]
    assert "older build" in caplog.text
