"""The MCP tools leave out or flag retracted papers."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from bmlib.publications.retraction_status import (
    RetractionEvidence,
    RetractionKind,
    RetractionLookup,
    RetractionSource,
    RetractionStatus,
    RetractionVerdict,
)

from bmlibrarian_lite import mcp_server
from bmlibrarian_lite.fulltext_discovery import FulltextResult, FulltextSourceType
from bmlibrarian_lite.retraction_store import PaperCheck
from tests.test_mcp_server import (
    _make_citation,
    _make_context,
    _make_document,
    _make_scored_document,
    _make_session,
)

WHEN = datetime(2026, 10, 1, tzinfo=UTC)
NOTICE = "10.1/notice"


def _check(status: RetractionStatus) -> PaperCheck:
    if status is RetractionStatus.RETRACTED:
        verdict = RetractionVerdict(
            status,
            RetractionEvidence(
                RetractionSource.CROSSREF_PUBLISHER,
                RetractionKind.RETRACTION,
                "2025-03-04",
                NOTICE,
            ),
            (),
        )
    elif status is RetractionStatus.UNCHECKED:
        verdict = RetractionVerdict(status, None, (RetractionLookup.CROSSREF,))
    else:
        verdict = RetractionVerdict(status, None, ())
    return PaperCheck(verdict, WHEN, None, None)


class FakeChecker:
    """A checker that counts calls and answers from a status table."""

    def __init__(self, by_id: dict[str, RetractionStatus]) -> None:
        """Keep the table."""
        self.by_id = by_id
        self.calls = 0
        self.closed = False

    def check(self, documents, **_kw):
        """Answer for each document."""
        self.calls += 1
        return {
            d.id: _check(self.by_id.get(d.id, RetractionStatus.CLEAR)) for d in documents
        }

    def close(self) -> None:
        """Note the close."""
        self.closed = True


@pytest.fixture
def checker(monkeypatch: pytest.MonkeyPatch) -> FakeChecker:
    """Install a fake checker, all clear until a test says otherwise."""
    fake = FakeChecker({})
    monkeypatch.setattr(mcp_server, "_build_checker", lambda ctx: fake)
    return fake


def _docs():
    good = _make_document("pmid-1", "Good paper", "1")
    bad = _make_document("pmid-2", "Bad paper", "2")
    return good, bad


def test_fact_check_excludes_retracted_and_lists_it(checker: FakeChecker) -> None:
    """Fact check excludes retracted and lists it."""
    good, bad = _docs()
    checker.by_id["pmid-2"] = RetractionStatus.RETRACTED
    ctx = _make_context()
    ctx.search_agent.search.return_value = (_make_session(), [good, bad])
    ctx.scoring_agent.score_documents.return_value = MagicMock(
        accepted=[_make_scored_document(good)], shortfall=None
    )
    ctx.citation_agent.extract_all_citations.return_value = MagicMock(
        citations=[_make_citation(good)], failed=[], shortfall=None
    )
    ctx.reporting_agent.generate_report.return_value = "REPORT"
    out = mcp_server._handle_fact_check({"claim": "c"}, ctx)
    scored = ctx.scoring_agent.score_documents.call_args.kwargs["documents"]
    assert [d.id for d in scored] == ["pmid-1"]
    assert out["retraction"]["left_out"] == [
        {
            "title": "Bad paper",
            "doi": "10.1234/test",
            "pmid": "2",
            "retraction_date": "2025-03-04",
            "notice_doi": NOTICE,
        }
    ]
    assert out["retraction"]["unchecked"] == 0
    assert out["retraction"]["checked_at"] == WHEN.isoformat()
    assert "Retracted papers left out" in out["report"]
    assert checker.closed


def test_fact_check_all_retracted_still_reports_it(checker: FakeChecker) -> None:
    """Fact check all retracted still reports it."""
    _, bad = _docs()
    checker.by_id["pmid-2"] = RetractionStatus.RETRACTED
    ctx = _make_context()
    ctx.search_agent.search.return_value = (_make_session(), [bad])
    out = mcp_server._handle_fact_check({"claim": "c"}, ctx)
    ctx.scoring_agent.score_documents.assert_not_called()
    assert len(out["retraction"]["left_out"]) == 1
    assert "Retracted papers left out" in out["report"]


def test_search_literature_excludes_and_lists(checker: FakeChecker) -> None:
    """Search literature excludes and lists."""
    good, bad = _docs()
    checker.by_id["pmid-2"] = RetractionStatus.RETRACTED
    ctx = _make_context()
    ctx.search_agent.search.return_value = (_make_session(), [good, bad])
    out = mcp_server._handle_search({"query": "q"}, ctx)
    assert [d["id"] for d in out["documents"]] == ["pmid-1"]
    assert out["retraction"]["left_out"][0]["pmid"] == "2"


def test_checker_failure_leaves_everything_unchecked(monkeypatch) -> None:
    """Checker failure leaves everything unchecked."""
    class Boom(FakeChecker):
        def check(self, documents, **_kw):
            raise RuntimeError("secret provider text")

    fake = Boom({})
    monkeypatch.setattr(mcp_server, "_build_checker", lambda ctx: fake)
    good, bad = _docs()
    ctx = _make_context()
    ctx.search_agent.search.return_value = (_make_session(), [good, bad])
    out = mcp_server._handle_search({"query": "q"}, ctx)
    assert len(out["documents"]) == 2
    assert out["retraction"]["unchecked"] == 2
    assert "secret" not in str(out)
    assert fake.closed


def test_fulltext_of_a_retracted_paper_is_returned_with_the_status_first(
    checker: FakeChecker,
) -> None:
    """Fulltext of a retracted paper is returned with the status first."""
    checker.by_id["pmid-7"] = RetractionStatus.RETRACTED
    ctx = _make_context()
    ctx.fulltext_discoverer.discover_fulltext.return_value = FulltextResult(
        success=True, source_type=FulltextSourceType.EUROPEPMC_XML, markdown_content="BODY"
    )
    ctx.interrogation_agent.load_document.return_value = "pmid-7"
    out = mcp_server._handle_fulltext({"pmid": "7"}, ctx)
    assert next(iter(out)) == "retraction_status"
    assert out["retraction_status"]["status"] == "retracted"
    assert out["content"].startswith(out["retraction_status"]["line"] + "\n\nBODY")
    assert out["success"] is True


def test_fulltext_clear_has_no_prefix(checker: FakeChecker) -> None:
    """Fulltext clear has no prefix."""
    ctx = _make_context()
    ctx.fulltext_discoverer.discover_fulltext.return_value = FulltextResult(
        success=True, source_type=FulltextSourceType.EUROPEPMC_XML, markdown_content="BODY"
    )
    ctx.interrogation_agent.load_document.return_value = "pmid-7"
    out = mcp_server._handle_fulltext({"pmid": "7"}, ctx)
    assert out["content"] == "BODY"
    assert out["retraction_status"]["status"] == "clear"


def test_ask_document_states_unchecked(checker: FakeChecker) -> None:
    """Ask document states unchecked."""
    ctx = _make_context()
    ctx.storage.get_document.return_value = None
    ctx.interrogation_agent.ask.return_value = ("ANSWER", [])
    out = mcp_server._handle_ask_document({"question": "q", "document_id": "x"}, ctx)
    assert next(iter(out)) == "retraction_status"
    assert out["retraction_status"]["status"] == "unchecked"
    assert out["answer"].startswith(out["retraction_status"]["line"] + "\n\nANSWER")
    assert checker.calls == 0


def test_ask_document_checks_a_stored_document(checker: FakeChecker) -> None:
    """Ask document checks a stored document."""
    checker.by_id["pmid-2"] = RetractionStatus.RETRACTED
    ctx = _make_context()
    ctx.storage.get_document.return_value = _docs()[1]
    ctx.interrogation_agent.ask.return_value = ("ANSWER", [])
    out = mcp_server._handle_ask_document({"question": "q", "document_id": "pmid-2"}, ctx)
    assert out["retraction_status"]["status"] == "retracted"
    assert out["answer"].endswith("\n\nANSWER")


def test_each_workflow_screens_once(checker: FakeChecker) -> None:
    """Each workflow screens once."""
    good, bad = _docs()
    ctx = _make_context()
    ctx.search_agent.search.return_value = (_make_session(), [good, bad])
    mcp_server._handle_search({"query": "q"}, ctx)
    assert checker.calls == 1
    ctx.scoring_agent.score_documents.return_value = MagicMock(accepted=[], shortfall=None)
    mcp_server._handle_fact_check({"claim": "c"}, ctx)
    assert checker.calls == 2
    ctx.fulltext_discoverer.discover_fulltext.return_value = FulltextResult(
        success=True, source_type=FulltextSourceType.EUROPEPMC_XML, markdown_content="B"
    )
    ctx.interrogation_agent.load_document.return_value = "pmid-7"
    mcp_server._handle_fulltext({"pmid": "7"}, ctx)
    assert checker.calls == 3
    ctx.storage.get_document.return_value = good
    ctx.interrogation_agent.ask.return_value = ("A", [])
    mcp_server._handle_ask_document({"question": "q", "document_id": "pmid-1"}, ctx)
    assert checker.calls == 4
