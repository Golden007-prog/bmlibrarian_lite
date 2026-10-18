"""The reader's retraction sentences (pure)."""

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

from bmlibrarian_lite.data_models import LiteDocument, RequestFailure, RequestFailureKind
from bmlibrarian_lite.retraction_screen import RetractionScreen, screen_documents
from bmlibrarian_lite.retraction_store import PaperCheck
from bmlibrarian_lite.retraction_text import (
    checked_on_line,
    format_retraction_notice,
    paper_status_line,
    run_summary_clause,
    with_retraction_notice,
    without_retraction_notice,
)

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


def _screen(retracted: int, unchecked: int) -> RetractionScreen:
    """Screen."""
    docs, checks = [], {}
    for i in range(retracted):
        docs.append(_doc(f"r{i}"))
        checks[f"r{i}"] = _check(RetractionStatus.RETRACTED, _evidence())
    for i in range(unchecked):
        docs.append(_doc(f"u{i}"))
        checks[f"u{i}"] = _check(RetractionStatus.UNCHECKED, unanswered=BOTH)
    return screen_documents(docs, checks)


def test_summary_clause_counts_and_singulars():
    """Test summary clause counts and singulars."""
    assert run_summary_clause(_screen(0, 0)) is None
    assert run_summary_clause(_screen(1, 0)) == "1 retracted paper left out"
    assert (
        run_summary_clause(_screen(2, 3))
        == "2 retracted papers left out; 3 papers' retraction status could not be checked"
    )
    assert run_summary_clause(_screen(0, 1)) == "1 paper's retraction status could not be checked"


def test_notice_is_idempotent():
    """Test notice is idempotent."""
    screen = _screen(2, 0)
    notice = format_retraction_notice(screen)
    assert notice == "> **Retracted papers left out:** 2 retracted papers left out."
    once = with_retraction_notice("Report", screen)
    assert once == f"{notice}\n\nReport"
    assert with_retraction_notice(once, screen) == once
    assert without_retraction_notice(once) == "Report"
    assert with_retraction_notice("Report", _screen(0, 0)) == "Report"
    assert format_retraction_notice(_screen(0, 0)) is None


def test_checked_on_line():
    """Test checked on line."""
    assert checked_on_line(WHEN) == "Retraction status checked 2026-10-18"
    assert checked_on_line(None) == "Retraction status never checked"


def test_status_lines():
    """Test status lines."""
    asof = "; Retraction Watch data as of 2026-10-01"
    assert (
        paper_status_line(_check(RetractionStatus.RETRACTED, _evidence()))
        == "Retracted on 2026-03-09 (notice 10.9/n)" + asof
    )
    label = RetractionEvidence(RetractionSource.PUBMED_LABEL, RetractionKind.RETRACTION)
    assert (
        paper_status_line(_check(RetractionStatus.RETRACTED, label, asof=None))
        == "Retracted (PubMed's label)"
    )
    concern = _evidence(kind=RetractionKind.EXPRESSION_OF_CONCERN, day="2026-03")
    assert (
        paper_status_line(_check(RetractionStatus.CONCERN, concern))
        == "Expression of concern, 2026-03 (notice 10.9/n)" + asof
    )


def test_status_lines_carry_no_provider_text():
    """Only RequestFailure.describe() speaks for a failed source."""
    failure = RequestFailure(RequestFailureKind.HTTP_STATUS, 503)
    line = paper_status_line(
        _check(
            RetractionStatus.UNCHECKED,
            unanswered=(RetractionLookup.CROSSREF,),
            failure=failure,
        )
    )
    assert line == (
        f"Retraction status not checked: Crossref ({failure.describe()}); "
        "Retraction Watch data as of 2026-10-01"
    )


def test_unchecked_export_unanswered_has_no_as_of_clause():
    """An export that was not consulted has no data date to report."""
    line = paper_status_line(
        _check(
            RetractionStatus.UNCHECKED,
            unanswered=(RetractionLookup.RETRACTION_WATCH_EXPORT,),
            asof=None,
        )
    )
    assert line == "Retraction status not checked: Retraction Watch"


def test_unchecked_both_unanswered_names_both():
    """Both sources silent and no failure recorded: names, no cause clause."""
    line = paper_status_line(_check(RetractionStatus.UNCHECKED, unanswered=BOTH, asof=None))
    assert line == "Retraction status not checked: Retraction Watch, Crossref"


def test_clear_line():
    """A clear paper says so and still dates the export."""
    assert (
        paper_status_line(_check(RetractionStatus.CLEAR))
        == "No retraction found; Retraction Watch data as of 2026-10-01"
    )


def test_retracted_with_notice_but_no_as_of():
    """The as-of clause appears only when the export was consulted."""
    assert (
        paper_status_line(_check(RetractionStatus.RETRACTED, _evidence(), asof=None))
        == "Retracted on 2026-03-09 (notice 10.9/n)"
    )


def test_concern_without_a_date():
    """A concern notice with no date omits the date part."""
    concern = _evidence(kind=RetractionKind.EXPRESSION_OF_CONCERN, day=None)
    assert (
        paper_status_line(_check(RetractionStatus.CONCERN, concern, asof=None))
        == "Expression of concern (notice 10.9/n)"
    )


def test_checked_on_line_reads_a_naive_datetime_as_utc():
    """A naive time is read as UTC, not rejected or shifted."""
    assert checked_on_line(datetime(2026, 10, 18, 23, 59)) == "Retraction status checked 2026-10-18"
