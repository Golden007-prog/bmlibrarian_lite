"""The report says what the retraction check left out and found."""

from __future__ import annotations

import difflib
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

from bmlib.publications.retraction_status import (
    RetractionEvidence,
    RetractionKind,
    RetractionSource,
    RetractionStatus,
    RetractionVerdict,
)

from bmlibrarian_lite.agents.reporting_agent import LiteReportingAgent
from bmlibrarian_lite.data_models import (
    AnalysisShortfall,
    AnalysisStage,
    Citation,
    LiteDocument,
    ReportMetadata,
)
from bmlibrarian_lite.retraction_screen import screen_documents
from bmlibrarian_lite.retraction_store import PaperCheck

WHEN = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)
QUESTION = "Does aspirin prevent stroke?"


def _doc(i: str) -> LiteDocument:
    """Doc."""
    return LiteDocument(id=i, title=f"Title {i}", abstract="", doi=f"10.1/{i}", pmid="1", year=2020)


def _check(status: RetractionStatus, notice: str | None = None) -> PaperCheck:
    """Check."""
    evidence = None
    if notice:
        evidence = RetractionEvidence(
            source=RetractionSource.RETRACTION_WATCH_EXPORT,
            kind=(
                RetractionKind.EXPRESSION_OF_CONCERN if status is RetractionStatus.CONCERN else RetractionKind.RETRACTION
            ),
            date="2026-03-09",
            notice_doi=notice,
        )
    return PaperCheck(RetractionVerdict(status, evidence, ()), WHEN, date(2026, 10, 1), None)


def _screen(**statuses):
    """Screen of docs named by keyword, e.g. a=RETRACTED."""
    docs = [_doc(k) for k in statuses]
    checks = {
        k: _check(v, "10.9/n" if v is not RetractionStatus.CLEAR else None)
        for k, v in statuses.items()
    }
    return screen_documents(docs, checks)


def _report(screen, citations=None, **meta) -> str:
    """Report with a stubbed model."""
    agent = LiteReportingAgent(config=MagicMock())
    agent._chat = MagicMock(return_value="Aspirin reduced stroke.")  # type: ignore[method-assign]
    if citations is None:
        citations = [Citation(document=_doc("ok"), passage="p", relevance_score=5)]
    metadata = ReportMetadata(retraction_screen=screen, **meta)
    return agent.generate_report(QUESTION, citations, metadata)


def test_report_lists_left_out_papers() -> None:
    """Report lists left out papers."""
    report = _report(_screen(ok=RetractionStatus.CLEAR, bad=RetractionStatus.RETRACTED))
    assert "## Left out as retracted" in report
    assert "- Title bad. Retracted 2026-03-09; notice 10.9/n" in report
    assert report.index("## References") < report.index("## Left out as retracted")
    assert report.index("## Left out as retracted") < report.index("## Methodology")


def test_cited_concern_paper_carries_a_caveat_at_its_reference() -> None:
    """Cited concern paper carries a caveat at its reference."""
    report = _report(_screen(ok=RetractionStatus.CONCERN))
    assert "    ⚠️ Expression of concern (2026-03-09, notice 10.9/n)" in report


def test_methods_carries_the_checked_on_line() -> None:
    """Methods carries the checked on line."""
    report = _report(_screen(ok=RetractionStatus.CLEAR))
    section = report.split("### Document Screening")[1].split("###")[0]
    assert "Retraction status checked 2026-10-18" in section


def test_report_without_exclusions_or_concerns_gains_only_the_checked_on_line() -> None:
    """Report without exclusions or concerns gains only the checked on line."""
    with_screen = _report(_screen(ok=RetractionStatus.CLEAR))
    without = _report(None)
    added = [
        line
        for line in difflib.ndiff(without.splitlines(), with_screen.splitlines())
        if line[0] in "+-"
    ]
    assert len(added) == 1 and "Retraction status checked 2026-10-18" in added[0]


def test_retraction_notice_leads_the_report_after_the_other_notices() -> None:
    """Retraction notice leads the report after the other notices."""
    shortfall = AnalysisShortfall(
        stage=AnalysisStage.SCORING, documents_failed=1, documents_attempted=3
    )
    report = _report(
        _screen(ok=RetractionStatus.CLEAR, bad=RetractionStatus.RETRACTED),
        analysis_shortfalls=[shortfall],
    )
    notice = "> **Retracted papers left out:** 1 retracted paper left out."
    assert report.count(notice) == 1
    assert 0 < report.index(notice) < report.index("Aspirin reduced stroke.")


def test_no_evidence_report_carries_section_and_line() -> None:
    """No evidence report carries section and line."""
    report = _report(_screen(bad=RetractionStatus.RETRACTED), citations=[])
    assert "No relevant evidence" in report
    assert "- Title bad. Retracted 2026-03-09; notice 10.9/n" in report
    assert "Retraction status checked 2026-10-18" in report


def test_metadata_round_trips() -> None:
    """Metadata round trips."""
    screen = _screen(ok=RetractionStatus.CONCERN, bad=RetractionStatus.RETRACTED)
    metadata = ReportMetadata(retraction_screen=screen)
    back = ReportMetadata.from_dict(metadata.to_dict())
    assert back.retraction_screen.to_snapshot() == screen.to_snapshot()
    assert back.retraction_screen.checked_at == screen.checked_at
    assert len(back.retraction_screen.excluded) == 1
    assert ReportMetadata.from_dict({}).retraction_screen is None
    assert ReportMetadata().to_dict()["retraction_screen"] is None
