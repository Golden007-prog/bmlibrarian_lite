# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A reopened question says when it was checked, warns, and can check again.

Also: the Research Questions tab's passes (re-score, re-classify, benchmark,
transparency re-analysis) leave retracted papers out before they start.
Fake checkers only; workers are run headless (``run()`` called directly).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
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

from bmlibrarian_lite.audit_records import (  # noqa: E402
    checkpoint_metadata_with_retraction_snapshot,
    recorded_retraction_snapshot,
)
from bmlibrarian_lite.config import LiteConfig  # noqa: E402
from bmlibrarian_lite.data_models import (  # noqa: E402
    Citation,
    DocumentSource,
    LiteDocument,
    ScoredDocument,
)
from bmlibrarian_lite.gui import report_tab as report_tab_module  # noqa: E402
from bmlibrarian_lite.gui import research_questions_tab as questions_module  # noqa: E402
from bmlibrarian_lite.gui.report_tab import ReportTab  # noqa: E402
from bmlibrarian_lite.gui.workers import RetractionRecheckWorker  # noqa: E402
from bmlibrarian_lite.retraction_checks import paper_key  # noqa: E402
from bmlibrarian_lite.retraction_screen import (  # noqa: E402
    changed_since_snapshot,
    latest_checks,
    rechecked_snapshot,
    screen_documents,
)
from bmlibrarian_lite.retraction_store import PaperCheck  # noqa: E402
from bmlibrarian_lite.retraction_text import CHECK_AGAIN_NOT_SAVED  # noqa: E402
from bmlibrarian_lite.storage import LiteStorage  # noqa: E402

QUESTION = "Does aspirin prevent stroke?"
RUN = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
LATER_RUN = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
REPORT = "## Findings\n\nAspirin reduced stroke incidence."
NOTICE = RetractionEvidence(
    RetractionSource.RETRACTION_WATCH_EXPORT, RetractionKind.RETRACTION, "2026-03-09", "10.9/notice"
)
CONCERN = RetractionEvidence(
    RetractionSource.CROSSREF_PUBLISHER, RetractionKind.EXPRESSION_OF_CONCERN, "2026-04-01", "10.9/eoc"
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


CITED, UNCITED, LEFT_OUT = make_document("1"), make_document("2"), make_document("3")
DOCUMENTS = [CITED, UNCITED, LEFT_OUT]


def check(status: RetractionStatus, when: datetime) -> PaperCheck:
    """A check of one paper at a given time."""
    deciding = {RetractionStatus.RETRACTED: NOTICE, RetractionStatus.CONCERN: CONCERN}.get(status)
    unanswered = tuple(RetractionLookup) if status is RetractionStatus.UNCHECKED else ()
    return PaperCheck(RetractionVerdict(status, deciding, unanswered), when, None, None)


def run_checks() -> dict[str, PaperCheck]:
    """What the run saw: the cited and uncited clear, one retracted and left out."""
    return {
        CITED.id: check(RetractionStatus.CLEAR, RUN),
        UNCITED.id: check(RetractionStatus.CLEAR, LATER_RUN),
        LEFT_OUT.id: check(RetractionStatus.RETRACTED, LATER_RUN),
    }


def run_snapshot() -> dict[str, dict[str, Any]]:
    """The run's snapshot rows by document id, as a checkpoint keeps them."""
    metadata = checkpoint_metadata_with_retraction_snapshot({}, screen_documents(DOCUMENTS, run_checks()))
    return recorded_retraction_snapshot(json.loads(json.dumps(metadata)))


@pytest.fixture
def qapp() -> Any:
    """The QApplication the widget tests need."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def storage(tmp_path: Any) -> LiteStorage:
    """A fresh store holding the question's three documents."""
    config = LiteConfig()
    config.storage.data_dir = tmp_path / "store"
    store = LiteStorage(config)
    for document in DOCUMENTS:
        store.add_document(document)
    store.add_question_documents(QUESTION, [d.id for d in DOCUMENTS])
    return store


def store_rows(storage: LiteStorage, checks: dict[str, PaperCheck]) -> None:
    """Write shared rows, as a check would, for documents by id."""
    by_id = {d.id: d for d in DOCUMENTS}
    storage.store_retraction_checks({paper_key(by_id[i]): c for i, c in checks.items()})


def saved_checkpoint(storage: LiteStorage, snapshot_metadata: dict[str, Any]) -> str:
    """A finished run's checkpoint carrying the metadata; returns its id."""
    checkpoint = storage.create_checkpoint(QUESTION, metadata=snapshot_metadata)
    storage.update_checkpoint(checkpoint.id, step="complete", report=REPORT)
    return checkpoint.id


@pytest.fixture
def tab(qapp: Any, tmp_path: Any, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch) -> ReportTab:
    """A report tab over the store; no dialogs, no threads kept."""
    monkeypatch.setattr(report_tab_module, "REPORTS_DIR", tmp_path)
    dialogs = MagicMock()
    for kind in ("critical", "warning"):
        getattr(dialogs, kind).side_effect = lambda _parent, title, text: pytest.fail(
            f"The report tab showed a dialog: {title}: {text}"
        )
    monkeypatch.setattr(report_tab_module, "QMessageBox", dialogs)
    monkeypatch.setattr(report_tab_module, "keep_until_finished", MagicMock())
    return ReportTab(config=LiteConfig(), storage=storage)


def reopen(tab: ReportTab, snapshot: dict[str, dict[str, Any]] | None, checkpoint_id: str = "cp") -> None:
    """Show the saved report as the app does on reopening, citing CITED only."""
    tab.display_report(
        report=REPORT,
        question=QUESTION,
        citations=[Citation(document=CITED, passage="Aspirin reduced stroke.", relevance_score=4)],
        documents_found=list(DOCUMENTS),
        all_scored_documents=[
            ScoredDocument(document=CITED, score=4, explanation="On topic."),
            ScoredDocument(document=UNCITED, score=2, explanation="Off topic."),
        ],
        auto_save=False,
        retraction_snapshot=snapshot,
    )
    tab.show_saved_retraction(checkpoint_id)


class NoRequests:
    """A checker that fails the test if anything asks it."""

    def check(self, *_args: Any, **_kwargs: Any) -> None:
        """Fail the test."""
        pytest.fail("a reopen made a request")

    def close(self) -> None:
        """Nothing to close."""


def forbid_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make building a checker, or starting a worker, fail the test."""
    monkeypatch.setattr(
        report_tab_module, "build_retraction_checker", lambda *_a, **_k: pytest.fail("checker built")
    )


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


def test_changed_since_snapshot_names_used_papers_newly_warned() -> None:
    """Newer, used, and now retracted or concern; nothing else."""
    snapshot = run_snapshot()
    current = {
        CITED.id: check(RetractionStatus.RETRACTED, NOW),
        UNCITED.id: check(RetractionStatus.RETRACTED, NOW),
    }
    assert changed_since_snapshot(snapshot, current, {CITED.id}) == [(CITED.id, current[CITED.id])]
    # controls: an older row; a clear one; an unchanged status
    older = {CITED.id: check(RetractionStatus.RETRACTED, RUN - timedelta(days=1))}
    assert changed_since_snapshot(snapshot, older, {CITED.id}) == []
    clear = {CITED.id: check(RetractionStatus.CLEAR, NOW)}
    assert changed_since_snapshot(snapshot, clear, {CITED.id}) == []
    concern_run = {**snapshot, CITED.id: {**snapshot[CITED.id], "status": "concern"}}
    concern = {CITED.id: check(RetractionStatus.CONCERN, NOW)}
    assert changed_since_snapshot(concern_run, concern, {CITED.id}) == []
    # a run that never checked the paper: any warning is news
    assert changed_since_snapshot({}, concern, {CITED.id}) == [(CITED.id, concern[CITED.id])]


def test_rechecked_snapshot_keeps_what_the_run_did() -> None:
    """Status, time and notice rewritten; excluded and the run's status kept; a failed check changes nothing."""
    snapshot = run_snapshot()
    screen = screen_documents(
        DOCUMENTS,
        {CITED.id: check(RetractionStatus.RETRACTED, NOW), LEFT_OUT.id: check(RetractionStatus.CLEAR, NOW)},
    )
    rows = {r["document_id"]: r for r in rechecked_snapshot(snapshot, screen).to_snapshot()}
    assert rows[CITED.id]["status"] == "retracted"
    assert rows[CITED.id]["checked_at"] == NOW.isoformat()
    assert rows[CITED.id]["deciding"]["notice_doi"] == "10.9/notice"
    assert rows[CITED.id]["excluded"] is False and rows[CITED.id]["run_status"] == "clear"
    assert rows[LEFT_OUT.id]["status"] == "clear" and rows[LEFT_OUT.id]["excluded"] is True
    assert rows[LEFT_OUT.id]["deciding"] is None
    assert rows[LEFT_OUT.id]["run_deciding"]["notice_doi"] == "10.9/notice"
    assert rows[UNCITED.id] == snapshot[UNCITED.id]  # not reached: kept as it was
    # a second Check again never overwrites what the run saw
    again = rechecked_snapshot(rows, screen_documents(DOCUMENTS, {LEFT_OUT.id: check(RetractionStatus.CONCERN, NOW)}))
    row = {r["document_id"]: r for r in again.to_snapshot()}[LEFT_OUT.id]
    assert row["run_status"] == "retracted" and row["run_deciding"]["notice_doi"] == "10.9/notice"
    assert row["deciding"]["notice_doi"] == "10.9/eoc"
    failed = screen_documents(DOCUMENTS, {}, failed=True)
    assert {r["document_id"]: r for r in rechecked_snapshot(snapshot, failed).to_snapshot()} == snapshot


def test_an_outage_never_replaces_a_decided_row() -> None:
    """Unchecked answers keep the decided rows whole; an unchecked row may still be decided over."""
    snapshot = run_snapshot()
    outage = screen_documents(DOCUMENTS, {d.id: check(RetractionStatus.UNCHECKED, NOW) for d in DOCUMENTS})
    rows = {r["document_id"]: r for r in rechecked_snapshot(snapshot, outage).to_snapshot()}
    assert rows == snapshot
    # control: an unchecked row is replaced by an unchecked answer's newer time, and by a decided one
    unchecked_run = {**snapshot, CITED.id: {**snapshot[CITED.id], "status": "unchecked"}}
    served = screen_documents(DOCUMENTS, {CITED.id: check(RetractionStatus.CLEAR, NOW)})
    rows = {r["document_id"]: r for r in rechecked_snapshot(unchecked_run, served).to_snapshot()}
    assert rows[CITED.id]["status"] == "clear" and rows[CITED.id]["checked_at"] == NOW.isoformat()


def rechecked_retracted() -> dict[str, dict[str, Any]]:
    """The run's snapshot after a Check again found the cited paper retracted."""
    screen = screen_documents(DOCUMENTS, {CITED.id: check(RetractionStatus.RETRACTED, NOW)})
    return {r["document_id"]: r for r in rechecked_snapshot(run_snapshot(), screen).to_snapshot()}


def test_latest_checks_seeds_from_checked_again_rows() -> None:
    """A Check again row is a check; a store row no older replaces it, unless it is an outage."""
    rows = rechecked_retracted()
    seeded = latest_checks(rows, {})
    assert set(seeded) == {CITED.id} and seeded[CITED.id].verdict.status is RetractionStatus.RETRACTED
    newer_clear = check(RetractionStatus.CLEAR, NOW + timedelta(days=1))
    assert latest_checks(rows, {CITED.id: newer_clear})[CITED.id] == newer_clear
    older_clear = check(RetractionStatus.CLEAR, NOW - timedelta(days=1))
    assert latest_checks(rows, {CITED.id: older_clear})[CITED.id] == seeded[CITED.id]
    newer_outage = check(RetractionStatus.UNCHECKED, NOW + timedelta(days=1))
    assert latest_checks(rows, {CITED.id: newer_outage})[CITED.id] == seeded[CITED.id]
    # a row the run wrote itself is no news: only the store speaks for it
    assert latest_checks(run_snapshot(), {}) == {}


def test_check_again_writes_only_this_runs_documents() -> None:
    """A document another run linked is checked but not written into this run's snapshot."""
    other = make_document("9")
    screen = screen_documents([*DOCUMENTS, other], {d.id: check(RetractionStatus.CLEAR, NOW) for d in [*DOCUMENTS, other]})
    rows = {r["document_id"] for r in rechecked_snapshot(run_snapshot(), screen).to_snapshot()}
    assert rows == {d.id for d in DOCUMENTS}
    # a run from before the feature has no snapshot: every linked document gets a row
    rows = {r["document_id"] for r in rechecked_snapshot({}, screen).to_snapshot()}
    assert rows == {d.id for d in [*DOCUMENTS, other]}


def test_the_saved_work_line_dates_decided_rows_and_counts_the_unchecked() -> None:
    """Oldest decided time; the unchecked counted; all unchecked, none, as before."""
    from bmlibrarian_lite.retraction_text import saved_work_line

    snapshot = run_snapshot()
    assert saved_work_line(snapshot.values()) == "Retraction status checked 2026-10-01"
    mixed = {
        **snapshot,
        CITED.id: {**snapshot[CITED.id], "status": "unchecked", "checked_at": "2026-09-01T00:00:00+00:00"},
        UNCITED.id: {**snapshot[UNCITED.id], "status": "unchecked", "checked_at": None},
    }
    assert saved_work_line(mixed.values()) == (
        "Retraction status checked 2026-10-15; 2 papers could not be checked"
    )
    one = {**snapshot, CITED.id: {**snapshot[CITED.id], "status": "unchecked"}}
    assert saved_work_line(one.values()).endswith("; 1 paper could not be checked")
    unchecked = [{**row, "status": "unchecked"} for row in snapshot.values()]
    assert saved_work_line(unchecked) == "Retraction status could not be checked"
    assert saved_work_line([]) == "Retraction status never checked"


# ---------------------------------------------------------------------------
# The report tab
# ---------------------------------------------------------------------------


def test_reopen_shows_oldest_checked_on_date(tab: ReportTab, monkeypatch: pytest.MonkeyPatch) -> None:
    """The oldest snapshot time; Check again offered; no request made."""
    forbid_requests(monkeypatch)
    reopen(tab, run_snapshot())
    assert tab.retraction_line.text() == "Retraction status checked 2026-10-01"
    assert not tab.retraction_line.isHidden()
    assert not tab.check_again_btn.isHidden() and tab.check_again_btn.isEnabled()
    assert tab.retraction_warning.isHidden()
    # a run whose check failed says so, never a date
    failed = {
        doc_id: {**row, "checked_at": None, "status": "unchecked"} for doc_id, row in run_snapshot().items()
    }
    reopen(tab, failed)
    assert tab.retraction_line.text() == "Retraction status could not be checked"


def test_reopen_of_a_pre_feature_run_says_never_checked(
    tab: ReportTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No snapshot: never checked, and Check again is offered."""
    forbid_requests(monkeypatch)
    reopen(tab, None)
    assert tab.retraction_line.text() == "Retraction status never checked"
    assert not tab.check_again_btn.isHidden() and tab.check_again_btn.isEnabled()


def test_a_live_report_shows_no_saved_work_line(tab: ReportTab) -> None:
    """display_report alone (a live run) hides the reopened run's line and button."""
    reopen(tab, run_snapshot())
    tab.display_report(
        report=REPORT, question=QUESTION, citations=[], documents_found=[], all_scored_documents=[],
        auto_save=False,
    )
    assert tab.retraction_line.isHidden() and tab.check_again_btn.isHidden()
    assert tab.retraction_warning.isHidden()


def test_reopen_warns_at_once_from_a_newer_stored_row(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another run found the cited paper retracted: the warning shows with no request."""
    forbid_requests(monkeypatch)
    store_rows(storage, {CITED.id: check(RetractionStatus.RETRACTED, NOW)})
    reopen(tab, run_snapshot())
    warning = tab.retraction_warning.text()
    assert not tab.retraction_warning.isHidden()
    assert CITED.title in warning and "Retracted on 2026-03-09" in warning
    assert "re-run the question" in warning
    assert tab.report_view.toPlainText().startswith("Findings")  # the report is not rewritten
    # control: a stored row no newer than the snapshot warns of nothing
    store_rows(storage, {CITED.id: check(RetractionStatus.RETRACTED, RUN - timedelta(days=1))})
    reopen(tab, run_snapshot())
    assert tab.retraction_warning.isHidden()


def test_a_checked_again_snapshot_warns_with_an_empty_store(
    tab: ReportTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store has no row (another machine, a reset, a failed read): the snapshot alone warns."""
    forbid_requests(monkeypatch)
    reopen(tab, rechecked_retracted())
    assert CITED.title in tab.retraction_warning.text() and not tab.retraction_warning.isHidden()


def test_a_reloaded_file_that_records_a_cited_retraction_warns_with_an_empty_store(
    tab: ReportTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The saved snapshot already says retracted (run_status clear); nothing is stored: still warned."""
    report_path = save_report(tab, rechecked_retracted())
    tab._hide_saved_retraction()
    forbid_requests(monkeypatch)
    load(tab, report_path, monkeypatch)
    assert CITED.title in tab.retraction_warning.text() and not tab.retraction_warning.isHidden()


def test_an_outage_during_a_second_check_again_keeps_the_warning(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unchecked answers over a snapshot that records the cited paper retracted: still warned."""
    reopen(tab, rechecked_retracted())
    run_check_again(tab, storage, monkeypatch, {d.id: check(RetractionStatus.UNCHECKED, NOW) for d in DOCUMENTS})
    assert CITED.title in tab.retraction_warning.text() and not tab.retraction_warning.isHidden()


def test_an_uncited_change_raises_no_warning(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scored but uncited paper changed nothing the reader sees."""
    forbid_requests(monkeypatch)
    store_rows(storage, {UNCITED.id: check(RetractionStatus.RETRACTED, NOW)})
    reopen(tab, run_snapshot())
    assert tab.retraction_warning.isHidden()


class RecordingChecker:
    """Answers per document, stores what it decided, records how it was asked."""

    def __init__(self, storage: LiteStorage, answers: dict[str, PaperCheck]) -> None:
        """Keep the store and the answers."""
        self.storage, self.answers = storage, answers
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.closed = False

    def check(self, documents: Any, **kwargs: Any) -> dict[str, PaperCheck]:
        """Answer, store the answers, record the call."""
        self.calls.append((sorted(d.id for d in documents), kwargs))
        out = {d.id: self.answers[d.id] for d in documents if d.id in self.answers}
        store_rows(self.storage, out)
        return out

    def close(self) -> None:
        """Record the close."""
        self.closed = True


def run_check_again(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch, answers: dict[str, PaperCheck]
) -> tuple[RecordingChecker, list[dict[str, Any]]]:
    """Click Check again with the worker run in place; returns the checker and builder kwargs."""
    checker = RecordingChecker(storage, answers)
    built: list[dict[str, Any]] = []
    monkeypatch.setattr(
        report_tab_module,
        "build_retraction_checker",
        lambda *_a, **kw: built.append(kw) or checker,
    )
    monkeypatch.setattr(RetractionRecheckWorker, "start", RetractionRecheckWorker.run)
    tab.check_again_btn.click()
    return checker, built


def test_check_again_updates_rows_and_snapshot_and_warns(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every linked paper, refreshed; shared rows and snapshot updated; report text unchanged."""
    checkpoint_id = saved_checkpoint(
        storage,
        checkpoint_metadata_with_retraction_snapshot(
            {"other": "kept"}, screen_documents(DOCUMENTS, run_checks())
        ),
    )
    reopen(tab, recorded_retraction_snapshot(storage.get_checkpoint(checkpoint_id).metadata), checkpoint_id)
    shown_before = tab.report_view.toHtml()
    rechecked: list[dict[str, Any]] = []
    tab.retraction_rechecked.connect(rechecked.append)

    checker, built = run_check_again(
        tab,
        storage,
        monkeypatch,
        {
            CITED.id: check(RetractionStatus.RETRACTED, NOW),
            UNCITED.id: check(RetractionStatus.CLEAR, NOW),
            LEFT_OUT.id: check(RetractionStatus.RETRACTED, NOW),
        },
    )

    assert built == [{"refresh_export": True}]
    [(ids, kwargs)] = checker.calls
    assert ids == sorted(d.id for d in DOCUMENTS) and checker.closed and kwargs["refresh"] is True
    assert storage.retraction_checks([paper_key(CITED)])[paper_key(CITED)].verdict.status is (
        RetractionStatus.RETRACTED
    )
    saved = storage.get_checkpoint(checkpoint_id)
    assert saved.report == REPORT and saved.metadata["other"] == "kept"
    rows = recorded_retraction_snapshot(saved.metadata)
    assert rows[CITED.id]["status"] == "retracted"
    assert rows[CITED.id]["checked_at"] == NOW.isoformat()
    assert rows[LEFT_OUT.id]["excluded"] is True and rows[CITED.id]["excluded"] is False
    assert tab.report_view.toHtml() == shown_before
    assert tab.retraction_line.text() == "Retraction status checked 2026-10-19"
    assert tab.check_again_btn.isEnabled()
    warning = tab.retraction_warning.text()
    assert CITED.title in warning and UNCITED.title not in warning and LEFT_OUT.title not in warning
    assert rechecked and rechecked[-1][CITED.id]["status"] == "retracted"
    # reopened later, the warning is still there: the snapshot keeps what the run saw
    reopen(tab, rows, checkpoint_id)
    assert CITED.title in tab.retraction_warning.text()


def test_a_failed_check_again_changes_nothing_and_says_so(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checker that raises: the run's record stays, the line says so, a shown warning stays."""
    metadata = checkpoint_metadata_with_retraction_snapshot({}, screen_documents(DOCUMENTS, run_checks()))
    checkpoint_id = saved_checkpoint(storage, metadata)
    store_rows(storage, {CITED.id: check(RetractionStatus.RETRACTED, NOW)})
    reopen(tab, recorded_retraction_snapshot(metadata), checkpoint_id)
    assert CITED.title in tab.retraction_warning.text()

    class Down(RecordingChecker):
        def check(self, documents: Any, **kwargs: Any) -> dict[str, PaperCheck]:
            raise RuntimeError("down")

    monkeypatch.setattr(report_tab_module, "build_retraction_checker", lambda *_a, **_k: Down(storage, {}))
    monkeypatch.setattr(RetractionRecheckWorker, "start", RetractionRecheckWorker.run)
    tab.check_again_btn.click()

    assert storage.get_checkpoint(checkpoint_id).metadata == json.loads(json.dumps(metadata))
    assert "could not be checked" in tab.retraction_line.text()
    assert tab.check_again_btn.isEnabled()
    assert CITED.title in tab.retraction_warning.text() and not tab.retraction_warning.isHidden()


def test_a_cancelled_check_again_saves_nothing(qapp: Any) -> None:
    """Cancelled (at quit): the worker says so instead of a result."""
    worker = RetractionRecheckWorker(NoRequests(), [], "cp")
    got: list[Any] = []
    worker.rechecked.connect(lambda *a: got.append(("rechecked", a)))
    worker.cancelled.connect(lambda *a: got.append(("cancelled", a)))
    worker.cancel()
    worker.checker = RecordingChecker(MagicMock(), {})
    worker.run()
    assert got == [("cancelled", ("cp",))]


def test_an_outage_during_check_again_leaves_the_record_and_line(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every answer unchecked: the decided rows and the checked-on line stay as they were."""
    metadata = checkpoint_metadata_with_retraction_snapshot({}, screen_documents(DOCUMENTS, run_checks()))
    checkpoint_id = saved_checkpoint(storage, metadata)
    reopen(tab, recorded_retraction_snapshot(metadata), checkpoint_id)
    run_check_again(tab, storage, monkeypatch, {d.id: check(RetractionStatus.UNCHECKED, NOW) for d in DOCUMENTS})
    rows = recorded_retraction_snapshot(storage.get_checkpoint(checkpoint_id).metadata)
    assert rows == recorded_retraction_snapshot(json.loads(json.dumps(metadata)))
    assert tab.retraction_line.text() == (
        "Retraction status checked 2026-10-01; 3 papers could not be checked again"
    )


def test_an_outage_over_an_all_clear_snapshot_says_how_many_could_not_be_checked_again(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No source answered: the old date stays, and the line counts the papers not checked again."""
    clear = screen_documents(DOCUMENTS, {d.id: check(RetractionStatus.CLEAR, RUN) for d in DOCUMENTS})
    metadata = checkpoint_metadata_with_retraction_snapshot({}, clear)
    checkpoint_id = saved_checkpoint(storage, metadata)
    reopen(tab, recorded_retraction_snapshot(metadata), checkpoint_id)
    run_check_again(tab, storage, monkeypatch, {d.id: check(RetractionStatus.UNCHECKED, NOW) for d in DOCUMENTS})
    assert tab.retraction_line.text() == (
        "Retraction status checked 2026-10-01; 3 papers could not be checked again"
    )
    # control: one paper served, two not
    reopen(tab, recorded_retraction_snapshot(metadata), checkpoint_id)
    run_check_again(
        tab,
        storage,
        monkeypatch,
        {**{d.id: check(RetractionStatus.UNCHECKED, NOW) for d in DOCUMENTS}, CITED.id: check(RetractionStatus.CLEAR, NOW)},
    )
    assert tab.retraction_line.text().endswith("; 2 papers could not be checked again")


def test_a_late_result_for_another_question_changes_neither_view_nor_audit_trail(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reader opened another question: the late result is saved to its own run only."""
    metadata = checkpoint_metadata_with_retraction_snapshot({}, screen_documents(DOCUMENTS, run_checks()))
    checkpoint_id = saved_checkpoint(storage, metadata)
    reopen(tab, recorded_retraction_snapshot(metadata), checkpoint_id)
    rechecked: list[Any] = []
    tab.retraction_rechecked.connect(rechecked.append)
    reopen(tab, None, "another-checkpoint")
    line, warning = tab.retraction_line.text(), tab.retraction_warning.text()
    late = screen_documents(DOCUMENTS, {CITED.id: check(RetractionStatus.RETRACTED, NOW)})
    tab._on_rechecked(checkpoint_id, late)
    assert (tab.retraction_line.text(), tab.retraction_warning.text()) == (line, warning)
    assert rechecked == [] and tab._retraction_snapshot is None
    rows = recorded_retraction_snapshot(storage.get_checkpoint(checkpoint_id).metadata)
    assert rows[CITED.id]["status"] == "retracted"


def test_reopen_keeps_excluded_as_excluded(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The left-out paper is reinstated: the reopened audit trail still shows it excluded."""
    from bmlibrarian_lite.gui.app import LiteMainWindow
    from bmlibrarian_lite.gui.audit_trail_tab import AuditTrailTab

    metadata = checkpoint_metadata_with_retraction_snapshot({}, screen_documents(DOCUMENTS, run_checks()))
    checkpoint_id = saved_checkpoint(storage, metadata)
    reopen(tab, recorded_retraction_snapshot(metadata), checkpoint_id)
    run_check_again(
        tab,
        storage,
        monkeypatch,
        {d.id: check(RetractionStatus.CLEAR, NOW) for d in DOCUMENTS},
    )

    window = MagicMock()
    window.storage = storage
    window.audit_trail_tab = AuditTrailTab(config=MagicMock(), storage=MagicMock())
    LiteMainWindow._on_question_selected(window, QUESTION, "aspirin")

    card = window.audit_trail_tab.literature_tab.get_card(LEFT_OUT.id)
    run_line = "Excluded: retracted. Retracted on 2026-03-09 (notice 10.9/notice)"
    assert card._excluded_label.text() == f"{run_line}. Checked again: No retraction found"
    # control: a paper still retracted as the run saw it gets the run's line alone
    window.audit_trail_tab.show_retraction_snapshot(run_snapshot())
    assert card._excluded_label.text() == run_line
    window.report_tab.show_saved_retraction.assert_called_once_with(checkpoint_id)
    kwargs = window.report_tab.display_report.call_args.kwargs
    assert kwargs["retraction_snapshot"][LEFT_OUT.id]["excluded"] is True


# ---------------------------------------------------------------------------
# The Research Questions tab's passes leave retracted papers out
# ---------------------------------------------------------------------------


class ScreeningChecker:
    """Retracts LEFT_OUT, clears the rest."""

    def __init__(self) -> None:
        """Not yet closed."""
        self.closed = False

    def check(self, documents: Any, **_kwargs: Any) -> dict[str, PaperCheck]:
        """Retract LEFT_OUT, clear the rest."""
        return {
            d.id: check(RetractionStatus.RETRACTED if d.id == LEFT_OUT.id else RetractionStatus.CLEAR, NOW)
            for d in documents
        }

    def close(self) -> None:
        """Record the close."""
        self.closed = True


def watch(worker: Any) -> list[str]:
    """Collect what the worker's screen said."""
    said: list[str] = []
    worker.retractions_screened.connect(said.append)
    return said


def test_the_reclassify_worker_leaves_retracted_papers_out(monkeypatch: pytest.MonkeyPatch, qapp: Any) -> None:
    """The classifier never sees the retracted paper; the count is said."""
    from bmlibrarian_lite.gui.workers import ReclassifyWorker
    from bmlibrarian_lite.quality.data_models import StudyClassification, StudyDesign

    seen: list[str] = []

    class Classifier:
        def __init__(self, config: Any) -> None:
            pass

        def classify(self, document: LiteDocument) -> StudyClassification:
            seen.append(document.id)
            return StudyClassification(study_design=StudyDesign.RCT, confidence=0.9)

    monkeypatch.setattr("bmlibrarian_lite.quality.study_classifier.LiteStudyClassifier", Classifier)
    checker = ScreeningChecker()
    worker = ReclassifyWorker(LiteConfig(), MagicMock(), list(DOCUMENTS), retraction_checker=checker)
    said = watch(worker)
    worker.run()
    assert seen == [CITED.id, UNCITED.id] and checker.closed
    assert said == ["1 retracted paper left out"]


def test_the_rescore_worker_leaves_retracted_papers_out(monkeypatch: pytest.MonkeyPatch, qapp: Any) -> None:
    """The scorer never sees the retracted paper."""
    from bmlibrarian_lite.gui.workers import RescoreWorker

    seen: list[str] = []

    class Agent:
        def __init__(self, config: Any) -> None:
            pass

        def score_document(self, question: str, document: LiteDocument) -> ScoredDocument:
            seen.append(document.id)
            return ScoredDocument(document=document, score=3, explanation="ok")

    monkeypatch.setattr("bmlibrarian_lite.agents.scoring_agent.LiteScoringAgent", Agent)
    worker = RescoreWorker(
        LiteConfig(), MagicMock(), QUESTION, list(DOCUMENTS), retraction_checker=ScreeningChecker()
    )
    said = watch(worker)
    worker.run()
    assert seen == [CITED.id, UNCITED.id]
    assert said == ["1 retracted paper left out"]


def test_the_transparency_worker_leaves_retracted_papers_out(
    monkeypatch: pytest.MonkeyPatch, qapp: Any
) -> None:
    """The pending re-analysis never analyses the retracted paper."""
    from bmlibrarian_lite.gui.workers import TransparencyReanalysisWorker
    from bmlibrarian_lite.transparency import assessment

    seen: list[str] = []

    def assess(_analyzer: Any, *args: Any) -> Any:
        seen.append(args[2])
        raise RuntimeError("not needed")

    monkeypatch.setattr(assessment, "assess_document", assess)
    monkeypatch.setattr(assessment, "create_background_analyzer", lambda *_a, **_k: MagicMock())
    worker = TransparencyReanalysisWorker(
        LiteConfig(), MagicMock(), list(DOCUMENTS), retraction_checker=ScreeningChecker()
    )
    said = watch(worker)
    worker.run()
    assert seen == [CITED.id, UNCITED.id]
    assert said == ["1 retracted paper left out"]


def test_the_benchmark_worker_leaves_retracted_papers_out(monkeypatch: pytest.MonkeyPatch, qapp: Any) -> None:
    """The benchmark runner is given the kept papers only."""
    from bmlibrarian_lite import benchmarking
    from bmlibrarian_lite.gui.benchmark_dialog import BenchmarkWorker

    given: list[list[str]] = []

    class Runner:
        def __init__(self, *_a: Any) -> None:
            pass

        def run_quick_benchmark(self, **kwargs: Any) -> Any:
            given.append([d.id for d in kwargs["documents"]])
            return SimpleNamespace(cancellation=None)

    monkeypatch.setattr(benchmarking, "BenchmarkRunner", Runner)
    worker = BenchmarkWorker(
        LiteConfig(), MagicMock(), QUESTION, list(DOCUMENTS), ["m"], retraction_checker=ScreeningChecker()
    )
    said = watch(worker)
    worker.run()
    assert given == [[CITED.id, UNCITED.id]]
    assert said == ["1 retracted paper left out"]


def test_a_worker_without_a_checker_screens_nothing(monkeypatch: pytest.MonkeyPatch, qapp: Any) -> None:
    """The control: the review's own benchmark (already screened) is not screened again."""
    from bmlibrarian_lite import benchmarking
    from bmlibrarian_lite.gui.benchmark_dialog import BenchmarkWorker

    given: list[list[str]] = []

    class Runner:
        def __init__(self, *_a: Any) -> None:
            pass

        def run_quick_benchmark(self, **kwargs: Any) -> Any:
            given.append([d.id for d in kwargs["documents"]])
            return SimpleNamespace(cancellation=None)

    monkeypatch.setattr(benchmarking, "BenchmarkRunner", Runner)
    worker = BenchmarkWorker(LiteConfig(), MagicMock(), QUESTION, list(DOCUMENTS), ["m"])
    said = watch(worker)
    worker.run()
    assert given == [[d.id for d in DOCUMENTS]] and said == []


@pytest.fixture
def questions_tab(qapp: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The Research Questions tab over a stub store, one question selected, workers stood in."""
    monkeypatch.setattr(questions_module.QTimer, "singleShot", MagicMock())
    boxes = MagicMock()
    boxes.Yes = boxes.StandardButton.Yes = boxes.question.return_value = "yes"
    monkeypatch.setattr(questions_module, "QMessageBox", boxes)
    built: list[dict[str, Any]] = []
    monkeypatch.setattr(
        questions_module, "build_retraction_checker", lambda *_a, **kw: built.append(kw) or "checker"
    )
    for name in ("BenchmarkWorker", "ReclassifyWorker", "RescoreWorker", "TransparencyReanalysisWorker"):
        monkeypatch.setattr(questions_module, name, MagicMock())
    storage = MagicMock()
    storage.get_document_ids_for_question.return_value = {d.id for d in DOCUMENTS}
    storage.get_documents_pending_transparency.return_value = [d.id for d in DOCUMENTS]
    storage.get_documents.return_value = list(DOCUMENTS)
    config = LiteConfig()
    config.benchmark.models = [MagicMock(get_model_string=lambda: "m")]
    widget = questions_module.ResearchQuestionsTab(config=config, storage=storage)
    widget._get_selected_question = lambda: SimpleNamespace(question=QUESTION)
    widget.built = built
    return widget


@pytest.mark.parametrize(
    ("action", "worker_name"),
    [
        ("_on_benchmark_clicked", "BenchmarkWorker"),
        ("_on_reclassify_clicked", "ReclassifyWorker"),
        ("_on_rescore_clicked", "RescoreWorker"),
        ("_on_reanalyse_transparency_clicked", "TransparencyReanalysisWorker"),
    ],
)
def test_each_pass_is_given_the_tabs_checker(questions_tab: Any, action: str, worker_name: str) -> None:
    """A checker that never refreshes the export goes to every pass's worker."""
    getattr(questions_tab, action)()
    worker_class = getattr(questions_module, worker_name)
    worker_class.assert_called_once()
    assert worker_class.call_args.kwargs["retraction_checker"] == "checker"
    assert questions_tab.built == [{}]
    worker_class.return_value.retractions_screened.connect.assert_called()


def test_a_pass_dialog_and_line_say_what_was_left_out(questions_tab: Any) -> None:
    """Completion and error dialogs carry the count; it does not depend on connection order."""
    from bmlibrarian_lite.data_models import PassOutcome

    boxes = questions_module.QMessageBox
    questions_tab._on_retractions_screened("1 retracted paper left out")
    questions_tab._on_rescore_finished(PassOutcome(succeeded=2, total=2))
    assert questions_tab.progress_label.text().endswith("; 1 retracted paper left out.")
    assert boxes.information.call_args.args[2].endswith("Retraction check: 1 retracted paper left out.")
    questions_tab._on_reclassify_error("The model timed out.")
    assert questions_tab.progress_label.text().endswith("; 1 retracted paper left out.")
    assert boxes.warning.call_args.args[2].endswith("Retraction check: 1 retracted paper left out.")
    # control: a pass whose screen left nothing out says nothing of it
    questions_tab._on_retractions_screened("")
    questions_tab._on_rescore_finished(PassOutcome(succeeded=2, total=2))
    assert "Retraction check" not in boxes.information.call_args.args[2]


def test_the_transparency_offer_says_retracted_papers_are_left_out(questions_tab: Any) -> None:
    """The scope counts pending papers before the screen, so the offer says what the screen does."""
    questions_tab._on_reanalyse_transparency_clicked()
    text = questions_module.QMessageBox.question.call_args.args[2]
    assert "Retracted papers are left out before the work starts." in text


# ---------------------------------------------------------------------------
# A saved report file
# ---------------------------------------------------------------------------


def save_report(tab: ReportTab, snapshot: dict[str, dict[str, Any]] | None) -> Any:
    """Save the run as the tab does; returns the report's path."""
    reopen(tab, snapshot)
    tab._auto_save_report()
    return tab._current_report_path


def audit_of(report_path: Any) -> Any:
    """The saved audit JSON's path."""
    return report_path.with_name(report_path.name.replace("_report.md", "_audit.json"))


def load(tab: ReportTab, report_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Load a saved report through the tab's file dialog."""
    monkeypatch.setattr(
        report_tab_module.QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(report_path), "")
    )
    tab._load_report()


def test_audit_json_carries_the_snapshot(tab: ReportTab) -> None:
    """One copy of the snapshot, and the oldest check time, in the audit JSON."""
    report_path = save_report(tab, run_snapshot())
    saved = json.loads(audit_of(report_path).read_text(encoding="utf-8"))
    assert saved["retraction"]["snapshot"] == list(run_snapshot().values())
    assert saved["retraction"]["checked_at"] == RUN.isoformat()
    # the date is the saved-work line's: the oldest decided row, never an unchecked answer's time
    snapshot = {**run_snapshot(), CITED.id: {**run_snapshot()[CITED.id], "status": "unchecked"}}
    saved = json.loads(audit_of(save_report(tab, snapshot)).read_text(encoding="utf-8"))
    assert saved["retraction"]["checked_at"] == LATER_RUN.isoformat()
    unchecked = {doc_id: {**row, "status": "unchecked"} for doc_id, row in run_snapshot().items()}
    saved = json.loads(audit_of(save_report(tab, unchecked)).read_text(encoding="utf-8"))
    assert saved["retraction"]["checked_at"] is None
    # a run that never checked writes no snapshot at all
    report_path = save_report(tab, None)
    assert "retraction" not in json.loads(audit_of(report_path).read_text(encoding="utf-8"))


def test_loading_a_report_shows_its_checked_on_line(
    tab: ReportTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The saved-work line and Check again; no request."""
    report_path = save_report(tab, run_snapshot())
    tab._hide_saved_retraction()
    forbid_requests(monkeypatch)
    load(tab, report_path, monkeypatch)
    assert tab.retraction_line.text() == "Retraction status checked 2026-10-01"
    assert not tab.check_again_btn.isHidden() and tab.check_again_btn.isEnabled()
    # a live report afterwards hides it again
    tab.display_report(
        report=REPORT, question=QUESTION, citations=[], documents_found=[], all_scored_documents=[],
        auto_save=False,
    )
    assert tab.retraction_line.isHidden()


def test_loading_an_old_report_says_never_checked(tab: ReportTab, monkeypatch: pytest.MonkeyPatch) -> None:
    """No retraction key: never checked, Check again offered."""
    report_path = save_report(tab, None)
    load(tab, report_path, monkeypatch)
    assert tab.retraction_line.text() == "Retraction status never checked"
    assert not tab.check_again_btn.isHidden()


def test_check_again_on_a_loaded_report_rewrites_only_the_audit_json_snapshot(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cited and left-out papers checked; the .md byte-identical; the rest of the JSON kept."""
    report_path = save_report(tab, run_snapshot())
    audit_path = audit_of(report_path)
    md_before = report_path.read_bytes()
    before = json.loads(audit_path.read_text(encoding="utf-8"))
    load(tab, report_path, monkeypatch)

    checker, _ = run_check_again(
        tab,
        storage,
        monkeypatch,
        {
            CITED.id: check(RetractionStatus.RETRACTED, NOW),
            LEFT_OUT.id: check(RetractionStatus.CLEAR, NOW),
        },
    )

    [(ids, _kwargs)] = checker.calls
    assert ids == sorted(d.id for d in DOCUMENTS)  # every paper the snapshot holds
    assert report_path.read_bytes() == md_before
    after = json.loads(audit_path.read_text(encoding="utf-8"))
    assert {k: v for k, v in after.items() if k != "retraction"} == {
        k: v for k, v in before.items() if k != "retraction"
    }
    rows = {r["document_id"]: r for r in after["retraction"]["snapshot"]}
    assert rows[CITED.id]["status"] == "retracted" and rows[CITED.id]["excluded"] is False
    assert rows[LEFT_OUT.id]["status"] == "clear" and rows[LEFT_OUT.id]["excluded"] is True
    assert after["retraction"]["checked_at"] == min(r["checked_at"] for r in rows.values())
    assert not list(audit_path.parent.glob("*.tmp"))
    assert CITED.title in tab.retraction_warning.text()
    assert storage.retraction_checks([paper_key(CITED)])[paper_key(CITED)].verdict.status is (
        RetractionStatus.RETRACTED
    )


def test_a_served_check_again_on_a_file_advances_every_row(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every snapshot row is checked, not just the cited and left out: the line reaches today."""
    report_path = save_report(tab, run_snapshot())
    load(tab, report_path, monkeypatch)
    checker, _ = run_check_again(
        tab, storage, monkeypatch, {d.id: check(RetractionStatus.CLEAR, NOW) for d in DOCUMENTS}
    )
    [(ids, _kwargs)] = checker.calls
    assert ids == sorted(d.id for d in DOCUMENTS)
    rows = json.loads(audit_of(report_path).read_text(encoding="utf-8"))["retraction"]["snapshot"]
    assert {r["checked_at"] for r in rows} == {NOW.isoformat()}
    assert tab.retraction_line.text() == "Retraction status checked 2026-10-19"


def test_a_file_without_a_snapshot_checks_its_cited_and_left_out_papers(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback: no snapshot, so the citations and retracted_documents name the papers."""
    report_path = saved_with_left_out(tab, run_snapshot())
    audit_path = audit_of(report_path)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    del audit["retraction"]
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    load(tab, report_path, monkeypatch)
    checker, _ = run_check_again(tab, storage, monkeypatch, {})
    [(ids, _kwargs)] = checker.calls
    assert ids == sorted([CITED.id, LEFT_OUT.id])


def test_a_files_check_again_leaves_the_audit_trail_tab(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit trail tab shows another run: a loaded file's new snapshot is not pushed to it."""
    report_path = save_report(tab, run_snapshot())
    load(tab, report_path, monkeypatch)
    rechecked: list[Any] = []
    tab.retraction_rechecked.connect(rechecked.append)
    run_check_again(tab, storage, monkeypatch, {CITED.id: check(RetractionStatus.RETRACTED, NOW)})
    assert rechecked == []
    assert CITED.title in tab.retraction_warning.text()  # the file's own view did update


def test_a_failed_write_tells_the_reader_and_leaves_the_file(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The replace fails: not-saved wording, file untouched, no temp file left."""
    report_path = save_report(tab, run_snapshot())
    audit_path = audit_of(report_path)
    before = audit_path.read_bytes()
    load(tab, report_path, monkeypatch)

    def refuse(*_a: Any, **_k: Any) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(report_tab_module.os, "replace", refuse)
    run_check_again(tab, storage, monkeypatch, {CITED.id: check(RetractionStatus.RETRACTED, NOW)})

    assert audit_path.read_bytes() == before
    assert not list(audit_path.parent.glob("*.tmp"))
    assert CHECK_AGAIN_NOT_SAVED in tab.retraction_line.text()


def test_the_rewrite_never_touches_a_file_that_happens_to_share_the_temp_name(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The temp file is a fresh one, not a fixed name beside the audit JSON."""
    report_path = save_report(tab, run_snapshot())
    audit_path = audit_of(report_path)
    bystander = audit_path.with_name(audit_path.name + ".tmp")
    bystander.write_text("someone else's", encoding="utf-8")
    load(tab, report_path, monkeypatch)
    run_check_again(tab, storage, monkeypatch, {CITED.id: check(RetractionStatus.RETRACTED, NOW)})
    assert bystander.read_text(encoding="utf-8") == "someone else's"
    assert set(audit_path.parent.glob("*.tmp")) == {bystander}
    rows = {r["document_id"]: r for r in json.loads(audit_path.read_text(encoding="utf-8"))["retraction"]["snapshot"]}
    assert rows[CITED.id]["status"] == "retracted"


def saved_with_left_out(tab: ReportTab, snapshot: dict[str, dict[str, Any]] | None) -> Any:
    """Save a run that left LEFT_OUT out as retracted; returns the report path."""
    reopen(tab, snapshot)
    tab._documents_found = list(DOCUMENTS)
    tab._all_scored_documents = []
    tab._auto_save_report()
    return tab._current_report_path


def test_check_again_keeps_the_runs_record_of_a_left_out_paper(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A left-out paper reinstated since: the run's date, notice and counts stay, on disk and on screen."""
    report_path = saved_with_left_out(tab, run_snapshot())
    audit_path = audit_of(report_path)
    before = json.loads(audit_path.read_text(encoding="utf-8"))
    assert [e["id"] for e in before["retracted_documents"]] == [LEFT_OUT.id]
    load(tab, report_path, monkeypatch)
    run_check_again(tab, storage, monkeypatch, {d.id: check(RetractionStatus.CLEAR, NOW) for d in DOCUMENTS})
    after = json.loads(audit_path.read_text(encoding="utf-8"))
    assert after["retracted_documents"] == before["retracted_documents"]
    assert after["retracted_documents"][0]["retraction_date"] == "2026-03-09"
    assert after["retracted_documents"][0]["notice_doi"] == "10.9/notice"
    assert after["workflow_summary"] == before["workflow_summary"]
    row = {r["document_id"]: r for r in after["retraction"]["snapshot"]}[LEFT_OUT.id]
    assert row["status"] == "clear" and row["deciding"] is None  # the current status, kept apart
    assert row["run_status"] == "retracted" and row["run_deciding"]["notice_doi"] == "10.9/notice"
    assert tab._loaded_audit_data == after
    assert "10.9/notice" in tab._audit_trail_text()
    assert tab.retraction_line.text() == "Retraction status checked 2026-10-19"


def test_check_again_keeps_the_runs_counts_and_evidence_on_a_reopened_question(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run's unchecked count and left-out notice survive Check again, live and reopened."""
    checks = {**run_checks(), UNCITED.id: check(RetractionStatus.UNCHECKED, LATER_RUN)}
    metadata = checkpoint_metadata_with_retraction_snapshot({}, screen_documents(DOCUMENTS, checks))
    checkpoint_id = saved_checkpoint(storage, metadata)
    reopen(tab, recorded_retraction_snapshot(metadata), checkpoint_id)
    summary = tab._audit_trail_data()["workflow_summary"]
    assert (summary["documents_retraction_unchecked"], summary["documents_excluded_retracted"]) == (1, 1)
    run_check_again(tab, storage, monkeypatch, {d.id: check(RetractionStatus.CLEAR, NOW) for d in DOCUMENTS})
    rows = recorded_retraction_snapshot(storage.get_checkpoint(checkpoint_id).metadata)
    assert rows[UNCITED.id]["status"] == "clear" and rows[LEFT_OUT.id]["status"] == "clear"
    for reopened in (False, True):
        if reopened:
            reopen(tab, rows, checkpoint_id)
        data = tab._audit_trail_data()
        assert data["workflow_summary"]["documents_retraction_unchecked"] == 1
        assert data["workflow_summary"]["documents_excluded_retracted"] == 1
        [entry] = data["retracted_documents"]
        assert (entry["retraction_date"], entry["notice_doi"]) == ("2026-03-09", "10.9/notice")


def test_a_pre_feature_run_checked_again_reads_as_not_recorded(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run was never screened: its counts say not recorded, never 0 left out; the line dates the check."""
    checkpoint_id = saved_checkpoint(storage, {})
    reopen(tab, None, checkpoint_id)
    run_check_again(tab, storage, monkeypatch, {d.id: check(RetractionStatus.CLEAR, NOW) for d in DOCUMENTS})
    rows = recorded_retraction_snapshot(storage.get_checkpoint(checkpoint_id).metadata)
    assert {r["run_status"] for r in rows.values()} == {None}
    reopen(tab, rows, checkpoint_id)
    summary = tab._audit_trail_data()["workflow_summary"]
    assert summary["documents_excluded_retracted"] is None
    assert summary["documents_retraction_unchecked"] is None
    text = tab._audit_trail_text()
    assert "- Retracted papers left out: not recorded for this run" in text
    assert "- Papers whose retraction status could not be checked: not recorded for this run" in text
    assert tab.retraction_line.text() == "Retraction status checked 2026-10-19"


def test_loading_a_report_without_an_audit_file_says_its_status_is_unknown(
    tab: ReportTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A line, and no Check again (nothing to check)."""
    report_path = save_report(tab, run_snapshot())
    audit_of(report_path).unlink()
    load(tab, report_path, monkeypatch)
    assert tab.retraction_line.text() == "Retraction status not recorded for this report"
    assert not tab.retraction_line.isHidden() and tab.check_again_btn.isHidden()


def test_an_old_report_marks_its_left_out_papers_excluded_when_checked_again(
    tab: ReportTab, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No retraction key, but retracted_documents: those stay excluded, others do not."""
    report_path = saved_with_left_out(tab, run_snapshot())
    audit_path = audit_of(report_path)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    del audit["retraction"]
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    load(tab, report_path, monkeypatch)
    run_check_again(
        tab,
        storage,
        monkeypatch,
        {
            CITED.id: check(RetractionStatus.RETRACTED, NOW),
            LEFT_OUT.id: check(RetractionStatus.CLEAR, NOW),
        },
    )
    rows = {r["document_id"]: r for r in json.loads(audit_path.read_text(encoding="utf-8"))["retraction"]["snapshot"]}
    assert rows[LEFT_OUT.id]["excluded"] is True
    assert rows[CITED.id]["excluded"] is False


def test_a_late_file_result_saves_to_its_own_file_and_leaves_the_view(
    tab: ReportTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another report opened meanwhile: the first file is updated, the view is not."""
    saved = save_report(tab, run_snapshot())
    first = saved.with_name("first_report.md")
    saved.rename(first)
    audit_of(saved).rename(audit_of(first))
    other = save_report(tab, None)
    load(tab, first, monkeypatch)
    first_key = "file:" + str(first)
    load(tab, other, monkeypatch)
    line = tab.retraction_line.text()
    tab._on_rechecked(first_key, screen_documents(DOCUMENTS, {CITED.id: check(RetractionStatus.RETRACTED, NOW)}))
    rows = {r["document_id"]: r for r in json.loads(audit_of(first).read_text(encoding="utf-8"))["retraction"]["snapshot"]}
    assert rows[CITED.id]["status"] == "retracted"
    assert tab.retraction_line.text() == line and tab._retraction_snapshot == {}


def test_a_malformed_audit_json_is_not_saved_and_left_alone(
    tab: ReportTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Garbage on disk at rewrite time: not-saved wording, bytes unchanged."""
    report_path = save_report(tab, run_snapshot())
    load(tab, report_path, monkeypatch)
    audit_path = audit_of(report_path)
    audit_path.write_text("{not json", encoding="utf-8")
    tab._on_rechecked(
        "file:" + str(report_path),
        screen_documents(DOCUMENTS, {CITED.id: check(RetractionStatus.RETRACTED, NOW)}),
    )
    assert audit_path.read_text(encoding="utf-8") == "{not json"
    assert CHECK_AGAIN_NOT_SAVED in tab.retraction_line.text()
