# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The systematic review leaves retracted papers out, and the audit trail says so.

The check runs at the review's one choke point: after the documents are
gathered and before the quality filter and scoring. The worker is run
headless (``run()`` called directly, no thread, no event pumping).
"""

import json
import logging
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

from bmlib.publications.retraction_status import (  # noqa: E402
    RetractionEvidence,
    RetractionKind,
    RetractionLookup,
    RetractionSource,
    RetractionStatus,
    RetractionVerdict,
)

from bmlibrarian_lite.audit_records import recorded_retraction_snapshot  # noqa: E402
from bmlibrarian_lite.config import LiteConfig  # noqa: E402
from bmlibrarian_lite.data_models import (  # noqa: E402
    CitationOutcome,
    DocumentSource,
    LiteDocument,
    ReportMetadata,
    ScoredDocument,
)
from bmlibrarian_lite.gui import report_tab as report_tab_module  # noqa: E402
from bmlibrarian_lite.gui import systematic_review_tab  # noqa: E402
from bmlibrarian_lite.gui.report_tab import ReportTab  # noqa: E402
from bmlibrarian_lite.gui.systematic_review_tab import WorkflowWorker  # noqa: E402
from bmlibrarian_lite.retraction_screen import RetractionScreen, screen_documents  # noqa: E402
from bmlibrarian_lite.retraction_store import PaperCheck  # noqa: E402
from bmlibrarian_lite.storage import LiteStorage  # noqa: E402

QUESTION = "Does aspirin prevent stroke?"
MIN_SCORE = 3
WHEN = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)
EVIDENCE = RetractionEvidence(
    source=RetractionSource.RETRACTION_WATCH_EXPORT,
    kind=RetractionKind.RETRACTION,
    date="2026-03-09",
    notice_doi="10.9/notice",
)


def make_document(pmid: str) -> LiteDocument:
    """A minimal document with a DOI and a PMID."""
    return LiteDocument(
        id=f"pmid-{pmid}",
        title=f"Aspirin trial {pmid}",
        abstract="Aspirin and stroke.",
        doi=f"10.1/{pmid}",
        pmid=pmid,
        source=DocumentSource.PUBMED,
    )


KEPT, LEFT_OUT, UNKNOWN = make_document("1"), make_document("2"), make_document("3")
DOCUMENTS = [KEPT, LEFT_OUT, UNKNOWN]


def check(status: RetractionStatus, deciding: RetractionEvidence | None = None) -> PaperCheck:
    """A check of one paper."""
    unanswered = tuple(RetractionLookup) if status is RetractionStatus.UNCHECKED else ()
    return PaperCheck(RetractionVerdict(status, deciding, unanswered), WHEN, None, None)


CHECKS = {
    KEPT.id: check(RetractionStatus.CLEAR),
    LEFT_OUT.id: check(RetractionStatus.RETRACTED, EVIDENCE),
    UNKNOWN.id: check(RetractionStatus.UNCHECKED),
}


class FakeChecker:
    """A checker answering fixed verdicts, or raising, or cancelling."""

    def __init__(
        self,
        checks: dict[str, PaperCheck] | None = None,
        error: Exception | None = None,
        during: Callable[[], None] | None = None,
    ) -> None:
        """Keep what to answer."""
        self.checks = CHECKS if checks is None else checks
        self.error = error
        self.during = during
        self.asked: list[list[str]] = []
        self.closed = False

    def close(self) -> None:
        """Record that the run released the checker."""
        self.closed = True

    def check(
        self,
        documents: Sequence[LiteDocument],
        *,
        refresh: bool = False,
        should_cancel: Callable[[], bool] = lambda: False,
    ) -> dict[str, PaperCheck]:
        """Answer, after doing what the test asked."""
        self.asked.append([d.id for d in documents])
        if self.during is not None:
            self.during()
        if self.error is not None:
            raise self.error
        return {d.id: self.checks[d.id] for d in documents if d.id in self.checks}


@pytest.fixture
def storage(tmp_path: Any) -> LiteStorage:
    """A database this test owns."""
    config = LiteConfig()
    config.storage.data_dir = tmp_path
    return LiteStorage(config)


def run_review(
    monkeypatch: pytest.MonkeyPatch,
    storage: LiteStorage,
    checker: FakeChecker,
    cancel_in_check: bool = False,
    score_value: int = 1,
) -> tuple[WorkflowWorker, list[str], dict[str, list[tuple[Any, ...]]]]:
    """Run a review over preloaded documents.

    By default every document scores below the threshold, so the run ends
    before any citation or report call; a higher ``score_value`` takes it
    through extraction and the report, both stood in for.

    Returns:
        The worker, the ids the scorer saw, and the signals emitted.
    """
    scored: list[str] = []

    def score(question: str, doc: LiteDocument) -> ScoredDocument:
        scored.append(doc.id)
        return ScoredDocument(document=doc, score=score_value, explanation="Scored.")

    scoring_agent = MagicMock()
    scoring_agent.score_document.side_effect = score
    monkeypatch.setattr(systematic_review_tab, "LiteScoringAgent", lambda **_: scoring_agent)
    citation_agent = MagicMock()
    citation_agent.extract_all_citations.return_value = CitationOutcome(
        citations=[], documents_attempted=0, failed=()
    )
    monkeypatch.setattr(systematic_review_tab, "LiteCitationAgent", lambda **_: citation_agent)
    reporting_agent = MagicMock()
    reporting_agent.generate_report.return_value = "## Findings"
    monkeypatch.setattr(systematic_review_tab, "LiteReportingAgent", lambda **_: reporting_agent)
    config = LiteConfig()
    monkeypatch.setattr(config.parallel, "get_scoring_workers", lambda provider: 1)
    monkeypatch.setattr(config.parallel, "get_citation_workers", lambda provider: 1)
    monkeypatch.setattr(config.transparency, "enabled", False)
    worker = WorkflowWorker(
        question=QUESTION,
        config=config,
        storage=storage,
        min_score=MIN_SCORE,
        preloaded_documents=list(DOCUMENTS),
        retraction_checker=checker,  # type: ignore[arg-type]
    )
    if cancel_in_check:
        checker.during = worker.cancel
    seen: dict[str, list[tuple[Any, ...]]] = {}
    for name in ("finished", "error", "retraction_screened"):
        getattr(worker, name).connect(
            lambda *args, name=name: seen.setdefault(name, []).append(args)
        )
    worker.run()
    return worker, scored, seen


def snapshot_of(storage: LiteStorage) -> dict[str, dict[str, Any]]:
    """The retraction snapshot the run's checkpoint keeps."""
    checkpoint = storage.get_checkpoint_for_question(QUESTION)
    assert checkpoint is not None
    return recorded_retraction_snapshot(checkpoint.metadata)


class TestTheChokePoint:
    """Retracted papers leave the list before anything is scored."""

    def test_a_retracted_document_never_reaches_scoring(
        self, monkeypatch: pytest.MonkeyPatch, storage: LiteStorage
    ) -> None:
        """The scorer sees only what the screen kept."""
        _, scored, seen = run_review(monkeypatch, storage, FakeChecker())

        assert scored == [KEPT.id, UNKNOWN.id]
        assert "error" not in seen

    def test_excluded_documents_are_linked_to_the_question(
        self, monkeypatch: pytest.MonkeyPatch, storage: LiteStorage
    ) -> None:
        """Reopening and *Check again* can find them."""
        run_review(monkeypatch, storage, FakeChecker())

        assert LEFT_OUT.id in storage.get_document_ids_for_question(QUESTION)
        assert storage.get_document(LEFT_OUT.id) is not None

    def test_snapshot_is_in_checkpoint_metadata(
        self, monkeypatch: pytest.MonkeyPatch, storage: LiteStorage
    ) -> None:
        """One row per document held, the excluded one included."""
        run_review(monkeypatch, storage, FakeChecker())

        snapshot = snapshot_of(storage)

        assert set(snapshot) == {d.id for d in DOCUMENTS}
        assert snapshot[LEFT_OUT.id]["status"] == "retracted"
        assert snapshot[UNKNOWN.id]["status"] == "unchecked"
        assert {k for k, row in snapshot.items() if row["excluded"]} == {LEFT_OUT.id}

    def test_the_snapshot_survives_the_extraction_record(
        self, monkeypatch: pytest.MonkeyPatch, storage: LiteStorage
    ) -> None:
        """Recording extraction failures rewrites the metadata whole; the snapshot stays."""
        _, _, seen = run_review(monkeypatch, storage, FakeChecker(), score_value=4)

        assert seen["finished"][0][0] == "## Findings"
        checkpoint = storage.get_checkpoint_for_question(QUESTION)
        assert checkpoint is not None
        assert "citation_extraction_failed" in checkpoint.metadata
        assert set(snapshot_of(storage)) == {d.id for d in DOCUMENTS}

    @pytest.mark.parametrize("outcome", ["answered", "raised", "cancelled"])
    def test_the_checker_is_closed_when_the_check_is_done(
        self, monkeypatch: pytest.MonkeyPatch, storage: LiteStorage, outcome: str
    ) -> None:
        """Its session is built for one run and released after it."""
        checker = FakeChecker(
            error=sqlite3.OperationalError("locked") if outcome == "raised" else None
        )
        run_review(monkeypatch, storage, checker, cancel_in_check=outcome == "cancelled")

        assert checker.closed

    def test_unchecked_document_is_scored_and_counted(
        self, monkeypatch: pytest.MonkeyPatch, storage: LiteStorage
    ) -> None:
        """Not knowing is no reason to leave a paper out, nor to stay quiet."""
        worker, scored, seen = run_review(monkeypatch, storage, FakeChecker())

        assert UNKNOWN.id in scored
        [(screen,)] = seen["retraction_screened"]
        assert screen is worker.retraction_screen
        assert screen.unchecked_count == 1
        assert [d.id for d in screen.excluded] == [LEFT_OUT.id]

    def test_cancel_during_the_check_ends_the_run(
        self, monkeypatch: pytest.MonkeyPatch, storage: LiteStorage
    ) -> None:
        """Cancelled, never an error; no checkpoint, no scoring."""
        _, scored, seen = run_review(monkeypatch, storage, FakeChecker(), cancel_in_check=True)

        assert seen["finished"][0][0] == "Workflow cancelled."
        assert "error" not in seen
        assert scored == []
        assert storage.get_checkpoint_for_question(QUESTION) is None

    def test_a_failing_checker_does_not_stop_the_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        storage: LiteStorage,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Every document is unchecked, the run goes on, and the summary says so."""
        failing = FakeChecker(error=sqlite3.OperationalError("secret provider text"))
        with caplog.at_level(logging.WARNING):
            worker, scored, seen = run_review(monkeypatch, storage, failing)

        assert scored == [d.id for d in DOCUMENTS]
        assert "error" not in seen
        screen = worker.retraction_screen
        assert screen is not None
        assert screen.unchecked_count == len(DOCUMENTS)
        assert all(
            c.verdict.status is RetractionStatus.UNCHECKED and c.verdict.unanswered
            for c in screen.checks.values()
        )
        from bmlibrarian_lite.retraction_text import run_summary_clause

        assert run_summary_clause(screen) == "3 papers' retraction status could not be checked"
        assert "OperationalError" in caplog.text
        assert "secret provider text" not in caplog.text
        assert {row["status"] for row in snapshot_of(storage).values()} == {"unchecked"}

    def test_every_paper_left_out_ends_the_run_without_scoring(
        self, monkeypatch: pytest.MonkeyPatch, storage: LiteStorage
    ) -> None:
        """Nothing left to score is said, not reported as an empty literature."""
        retracted = {d.id: check(RetractionStatus.RETRACTED, EVIDENCE) for d in DOCUMENTS}
        _, scored, seen = run_review(monkeypatch, storage, FakeChecker(retracted))

        assert scored == []
        message = seen["finished"][0][0]
        assert message.startswith("> **Retracted papers left out:**")
        assert message.count("3 retracted papers left out") == 1
        assert message.endswith("No documents left to score.")

    def test_without_a_checker_the_review_is_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, storage: LiteStorage
    ) -> None:
        """Control: no checker, no screen, no snapshot."""
        worker, scored, seen = run_review(monkeypatch, storage, None)  # type: ignore[arg-type]

        assert scored == [d.id for d in DOCUMENTS]
        assert worker.retraction_screen is None
        assert "retraction_screened" not in seen
        assert snapshot_of(storage) == {}


@pytest.fixture
def qapp() -> Any:
    """The QApplication the tab tests need."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class TestTheTab:
    """The tab builds the checker, shows the summary and hands the snapshot on."""

    def review_tab(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
        """A Systematic Review tab whose worker never runs."""
        from bmlibrarian_lite.gui.systematic_review_tab import SystematicReviewTab

        self.worker_class = MagicMock()
        monkeypatch.setattr(systematic_review_tab, "WorkflowWorker", self.worker_class)
        config = LiteConfig()
        config.storage.data_dir = tmp_path
        return SystematicReviewTab(config=config, storage=MagicMock())

    def test_the_tab_passes_a_real_checker(
        self, qapp: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """Every review is checked."""
        from bmlibrarian_lite.retraction_checks import RetractionChecker

        tab = self.review_tab(monkeypatch, tmp_path)
        tab.question_input.setPlainText(QUESTION)
        tab._run_workflow()

        kwargs = self.worker_class.call_args.kwargs
        assert isinstance(kwargs["retraction_checker"], RetractionChecker)

    def test_the_summary_is_shown_and_cleared_on_a_new_run(
        self, qapp: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """As the incomplete-search notice is."""
        tab = self.review_tab(monkeypatch, tmp_path)
        tab._on_retraction_screened(screen_documents(DOCUMENTS, CHECKS))

        text = tab.retraction_notice_label.text()
        assert "1 retracted paper left out" in text
        assert "1 paper's retraction status could not be checked" in text
        assert not tab.retraction_notice_label.isHidden()

        tab.question_input.setPlainText(QUESTION)
        tab._run_workflow()

        assert tab.retraction_notice_label.text() == ""
        assert tab.retraction_notice_label.isHidden()

    def test_the_report_signal_carries_the_snapshot(
        self, qapp: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """The Report tab's audit record needs it."""
        tab = self.review_tab(monkeypatch, tmp_path)
        emitted: list[Any] = []
        tab.report_generated.connect(lambda *args: emitted.append(args))
        tab._on_retraction_screened(screen_documents(DOCUMENTS, CHECKS))

        tab._on_finished("# Evidence Report", ReportMetadata(research_question=QUESTION))

        snapshot = emitted[0][-1]
        assert snapshot[LEFT_OUT.id]["status"] == "retracted"


@pytest.fixture
def report_tab(qapp: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> ReportTab:
    """A report tab auto-saving into a directory this test owns."""
    monkeypatch.setattr(report_tab_module, "REPORTS_DIR", tmp_path)
    dialogs = MagicMock()
    for kind in ("critical", "warning"):
        getattr(dialogs, kind).side_effect = lambda _parent, title, text: pytest.fail(
            f"The report tab showed a dialog: {title}: {text}"
        )
    monkeypatch.setattr(report_tab_module, "QMessageBox", dialogs)
    return ReportTab(config=MagicMock(), storage=MagicMock())


def show(tab: ReportTab, snapshot: dict[str, dict[str, Any]] | None) -> None:
    """Show a report over the three documents, one retracted and left out."""
    tab.display_report(
        report="## Findings\n\nAspirin reduced stroke incidence.",
        question=QUESTION,
        citations=[],
        documents_found=list(DOCUMENTS),
        all_scored_documents=[
            ScoredDocument(document=KEPT, score=4, explanation="On topic."),
            ScoredDocument(document=UNKNOWN, score=1, explanation="Off topic."),
        ],
        report_metadata=ReportMetadata(research_question=QUESTION, min_score_threshold=MIN_SCORE),
        citation_extraction_failures=[],
        retraction_snapshot=snapshot,
    )


def screened_snapshot() -> dict[str, dict[str, Any]]:
    """The snapshot a run over the three documents keeps."""
    return RetractionScreen.from_snapshot(screen_documents(DOCUMENTS, CHECKS).to_snapshot())


class TestTheAuditRecord:
    """The saved record and the dialog name each paper left out."""

    def test_audit_json_lists_retracted_documents(
        self, report_tab: ReportTab, tmp_path: Any
    ) -> None:
        """With the retraction's date and its notice's DOI; not as unscored."""
        show(report_tab, screened_snapshot())

        [path] = sorted(tmp_path.glob("*_audit.json"))
        audit = json.loads(path.read_text(encoding="utf-8"))

        assert audit["retracted_documents"] == [
            {
                "id": LEFT_OUT.id,
                "title": LEFT_OUT.title,
                "doi": LEFT_OUT.doi,
                "pmid": LEFT_OUT.pmid,
                "retraction_date": "2026-03-09",
                "notice_doi": "10.9/notice",
            }
        ]
        assert audit["unscored_documents"] == []
        assert audit["workflow_summary"]["documents_excluded_retracted"] == 1
        assert audit["workflow_summary"]["documents_retraction_unchecked"] == 1

    def test_the_dialog_names_them(self, report_tab: ReportTab) -> None:
        """Under their own heading, with date and notice."""
        show(report_tab, screened_snapshot())

        text = report_tab._audit_trail_text()

        assert "## Excluded: Retracted" in text
        assert f"- {LEFT_OUT.title} — retracted 2026-03-09; notice 10.9/notice" in text
        assert "- Retracted papers left out: 1" in text
        assert "- Papers whose retraction status could not be checked: 1" in text

    def test_a_run_without_a_snapshot_says_not_recorded(self, report_tab: ReportTab) -> None:
        """Never "0 left out" for a run that was not checked."""
        show(report_tab, None)

        text = report_tab._audit_trail_text()

        assert "- Retracted papers left out: not recorded for this run" in text
        assert "## Excluded: Retracted" not in text


class TestAReopenedQuestion:
    """A restored report reads the snapshot its checkpoint kept."""

    def reopened(self, checkpoint_metadata: dict[str, Any]) -> Any:
        """Reopen a question whose checkpoint carries the metadata."""
        from bmlibrarian_lite.data_models import ReviewCheckpoint
        from bmlibrarian_lite.gui.app import LiteMainWindow

        window = MagicMock()
        window.storage.get_checkpoint_for_question.return_value = ReviewCheckpoint(
            id="checkpoint-1",
            research_question=QUESTION,
            created_at=WHEN,
            updated_at=WHEN,
            step="complete",
            report="## Findings",
            metadata=checkpoint_metadata,
        )
        by_id = {d.id: d for d in DOCUMENTS}
        window.storage.get_document_ids_for_question.return_value = list(by_id)
        window.storage.get_document.side_effect = by_id.get
        window.storage.get_scored_documents_for_question.return_value = []
        window.storage.get_citations_for_question.return_value = []
        window.storage.get_quality_assessments_for_question.return_value = {}
        LiteMainWindow._on_question_selected(window, QUESTION, "aspirin")
        return window.report_tab.display_report.call_args.kwargs

    def test_the_left_out_papers_stay_left_out(self) -> None:
        """Linked to the question, they would otherwise read as never scored."""
        from bmlibrarian_lite.audit_records import checkpoint_metadata_with_retraction_snapshot

        metadata = checkpoint_metadata_with_retraction_snapshot(
            {}, screen_documents(DOCUMENTS, CHECKS)
        )

        kwargs = self.reopened(json.loads(json.dumps(metadata)))

        assert kwargs["retraction_snapshot"][LEFT_OUT.id]["status"] == "retracted"

    def test_a_run_from_before_the_check_passes_none(self) -> None:
        """Not recorded is None, never an empty snapshot."""
        assert self.reopened({})["retraction_snapshot"] is None
