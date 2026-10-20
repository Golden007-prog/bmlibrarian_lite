"""Where walled PDFs are queued for the browser (#480, stage 2)."""

import logging
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bmlibrarian_lite.browser_rescue import OPEN_RESCUE_STATES, RescueState
from bmlibrarian_lite.config import LiteConfig
from bmlibrarian_lite.constants import SERVICE_UNPAYWALL_PDF
from bmlibrarian_lite.data_models import (
    LookupRecord,
    RequestFailure,
    RequestFailureKind,
    SourceLookupFailure,
)
from bmlibrarian_lite.fulltext_discovery import FulltextResult, FulltextSourceType
from bmlibrarian_lite.mcp_server import _AgentsContext, _handle_fulltext
from bmlibrarian_lite.storage import LiteStorage
from bmlibrarian_lite.study_transparency_analyzer.study_transparency_analyzer import (
    StudyTransparencyAnalyzer,
    TransparencyReport,
)
from bmlibrarian_lite.transparency.assessment import assess_document
from bmlibrarian_lite.transparency.transparency_settings import TransparencySettings
from tests.mcp_retraction_fixture import mcp_retraction_clear  # noqa: F401

pytestmark = pytest.mark.usefixtures("mcp_retraction_clear")

DOI = "10.1016/j.example.2021.1"
ADDRESS = "https://www.sciencedirect.com/science/article/pii/S1/pdfft"
RECORD = LookupRecord(
    failures=(
        SourceLookupFailure(
            SERVICE_UNPAYWALL_PDF, RequestFailure(RequestFailureKind.CHALLENGED, 403), ADDRESS
        ),
    )
)


class _Discoverer:
    def __init__(self, result: FulltextResult) -> None:
        self.result = result

    def discover_fulltext(self, **kwargs: object) -> FulltextResult:
        return self.result


def _analyzer(result: FulltextResult) -> StudyTransparencyAnalyzer:
    analyzer = StudyTransparencyAnalyzer(email="test@example.com", auto_discover_fulltext=True)
    analyzer._fetch_basic_metadata = lambda report: None  # type: ignore[method-assign]
    analyzer._fetch_funder_info = lambda report: None  # type: ignore[method-assign]
    analyzer._fetch_trial_info = lambda report: None  # type: ignore[method-assign]
    analyzer._fulltext_discoverer = lambda: _Discoverer(result)  # type: ignore[method-assign,assignment,return-value]
    return analyzer


def test_the_report_carries_what_an_unsuccessful_discovery_left() -> None:
    """An unsuccessful discovery's lookups reach the report."""
    result = FulltextResult(success=False, source_type=FulltextSourceType.NOT_ASSESSED, lookups=RECORD)
    assert _analyzer(result).analyze(doi=DOI).fulltext_lookups == RECORD


def test_a_successful_discovery_leaves_nothing_to_queue() -> None:
    """A discovery that obtained the text leaves an empty record."""
    result = FulltextResult(
        success=True,
        source_type=FulltextSourceType.EUROPEPMC_XML,
        markdown_content="# Methods\n\nText.",
        lookups=RECORD,
    )
    assert _analyzer(result).analyze(doi=DOI).fulltext_lookups == LookupRecord()


def test_a_report_without_discovery_has_an_empty_record() -> None:
    """A fresh report has an empty record."""
    assert TransparencyReport().fulltext_lookups == LookupRecord()


@pytest.fixture
def storage(tmp_path: Path) -> LiteStorage:
    """A storage in a temporary directory."""
    config = LiteConfig()
    config.storage.data_dir = tmp_path
    return LiteStorage(config)


def _report_analyzer(report: TransparencyReport) -> MagicMock:
    analyzer = MagicMock()
    analyzer.analyze.return_value = report
    return analyzer


def test_an_assessment_queues_its_walled_copies(storage: LiteStorage) -> None:
    """An assessment queues the report's walled copies."""
    report = TransparencyReport(doi=DOI, pmid="123", title="A trial", fulltext_lookups=RECORD)
    assess_document(_report_analyzer(report), storage, TransparencySettings(), "doc-1", "123", None)
    (entry,) = storage.rescue_entries(OPEN_RESCUE_STATES)
    assert (entry.request.document_id, entry.request.doi, entry.request.address, entry.state) == (
        "doc-1", DOI, ADDRESS, RescueState.PENDING
    )
    assert storage.get_transparency_result("doc-1") is not None


def test_an_assessment_that_cannot_queue_still_stores_its_result(
    storage: LiteStorage, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A queue failure is logged and the result is still stored."""
    def _locked(*args: object, **kwargs: object) -> int:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(storage, "enqueue_rescues", _locked)
    report = TransparencyReport(doi=DOI, fulltext_lookups=RECORD)
    with caplog.at_level(logging.ERROR):
        result = assess_document(_report_analyzer(report), storage, TransparencySettings(), "doc-1", None, DOI)
    assert result.document_id == "doc-1"
    assert storage.get_transparency_result("doc-1") is not None
    assert "Could not queue" in caplog.text


def _context(result: FulltextResult) -> _AgentsContext:
    discoverer = MagicMock()
    discoverer.discover_fulltext.return_value = result
    storage = MagicMock()
    storage.enqueue_rescues.return_value = 1  # a count, as LiteStorage returns
    return _AgentsContext(
        config=MagicMock(), storage=storage, llm_client=MagicMock(), search_agent=MagicMock(),
        scoring_agent=MagicMock(), citation_agent=MagicMock(), reporting_agent=MagicMock(),
        interrogation_agent=MagicMock(), fulltext_discoverer=discoverer,
    )


def test_the_mcp_server_queues_its_walled_copies_under_its_own_id() -> None:
    """The MCP server queues under the ID built from the request."""
    ctx = _context(FulltextResult(success=False, source_type=FulltextSourceType.NOT_ASSESSED, lookups=RECORD))
    payload = _handle_fulltext({"doi": DOI}, ctx)
    assert payload["success"] is False
    ((requests, priority), _) = ctx.storage.enqueue_rescues.call_args
    assert [(r.document_id, r.address) for r in requests] == [(f"doi-{DOI}", ADDRESS)]
    assert priority == 0


def test_the_mcp_server_queues_nothing_after_a_success() -> None:
    """A success queues nothing."""
    ctx = _context(
        FulltextResult(success=True, source_type=FulltextSourceType.EUROPEPMC_XML, markdown_content="text")
    )
    _handle_fulltext({"doi": DOI}, ctx)
    ctx.storage.enqueue_rescues.assert_not_called()
