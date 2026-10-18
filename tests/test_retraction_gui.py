# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Retraction badges on cards, the audit trail's exclusions, the interrogation banner."""

from datetime import UTC, date, datetime
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

from bmlibrarian_lite.data_models import LiteDocument  # noqa: E402
from bmlibrarian_lite.gui import document_interrogation_tab as tab_module  # noqa: E402
from bmlibrarian_lite.gui.audit_trail_tab import AuditTrailTab  # noqa: E402
from bmlibrarian_lite.gui.document_card import DocumentCard  # noqa: E402
from bmlibrarian_lite.gui.retraction_badge import RetractionBadge  # noqa: E402
from bmlibrarian_lite.gui.workers import RetractionCheckWorker  # noqa: E402
from bmlibrarian_lite.retraction_screen import (  # noqa: E402
    RetractionScreen,
    screen_documents,
    unchecked_check,
)
from bmlibrarian_lite.retraction_store import PaperCheck  # noqa: E402
from bmlibrarian_lite.retraction_text import paper_status_line  # noqa: E402
from tests.test_interrogation_degradation import make_citation, make_tab  # noqa: E402,F401


@pytest.fixture
def qapp() -> Any:
    """The QApplication the widget tests need."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def make_check(status: RetractionStatus) -> PaperCheck:
    """A check with the given status."""
    evidence = (
        RetractionEvidence(
            RetractionSource.CROSSREF_PUBLISHER,
            RetractionKind.RETRACTION
            if status is RetractionStatus.RETRACTED
            else RetractionKind.EXPRESSION_OF_CONCERN,
            "2024-05-01",
            "10.1/notice",
        )
        if status in (RetractionStatus.RETRACTED, RetractionStatus.CONCERN)
        else None
    )
    unanswered = tuple(RetractionLookup) if status is RetractionStatus.UNCHECKED else ()
    return PaperCheck(
        RetractionVerdict(status, evidence, unanswered), datetime.now(UTC), date(2026, 1, 1), None
    )


def make_doc(n: int = 1) -> LiteDocument:
    """A minimal document."""
    return LiteDocument(id=f"doc-{n}", title=f"Trial {n}", abstract="A.", authors=["S J"], year=2024)


def test_badge_text_per_status(qapp: Any) -> None:
    """Words per status; clear shows nothing; the tooltip is the status line."""
    expected = {
        RetractionStatus.RETRACTED: "RETRACTED",
        RetractionStatus.CONCERN: "CONCERN",
        RetractionStatus.UNCHECKED: "NOT CHECKED",
    }
    for status, text in expected.items():
        check = make_check(status)
        badge = RetractionBadge(check)
        assert badge.text() == text
        assert not badge.isHidden()
        assert badge.toolTip() == paper_status_line(check)
    assert RetractionBadge(make_check(RetractionStatus.CLEAR)).isHidden()


def test_card_shows_badge_for_retracted_and_concern(qapp: Any) -> None:
    """A card carries the badge, replaces it on a later check, and drops it for clear."""
    card = DocumentCard(make_doc())
    card.set_retraction_check(make_check(RetractionStatus.RETRACTED))
    assert card._retraction_badge.text() == "RETRACTED"
    card.set_retraction_check(make_check(RetractionStatus.CONCERN))
    assert card._retraction_badge.text() == "CONCERN"
    card.set_retraction_check(make_check(RetractionStatus.CLEAR))
    assert card._retraction_badge is None


def test_audit_trail_labels_exclusions_and_orders_them_last(qapp: Any) -> None:
    """An excluded paper reads RETRACTED and 'Excluded: retracted'; a kept one its concern."""
    trail = AuditTrailTab(config=MagicMock(), storage=MagicMock())
    retracted, concern = make_doc(1), make_doc(2)
    trail.on_documents_found([retracted, concern])
    screen = screen_documents(
        [retracted, concern],
        {retracted.id: make_check(RetractionStatus.RETRACTED), concern.id: make_check(RetractionStatus.CONCERN)},
    )
    trail.on_retraction_screened(screen)
    cards = trail.literature_tab
    assert cards.get_card("doc-1")._retraction_badge.text() == "RETRACTED"
    assert cards.get_card("doc-1")._excluded_label.text() == "Excluded: retracted"
    assert cards.get_card("doc-2")._retraction_badge.text() == "CONCERN"
    assert cards.get_card("doc-2")._excluded_label is None
    cards.resort_by_score()
    widgets = [cards.cards_layout.itemAt(i).widget() for i in range(cards.cards_layout.count())]
    order = [w.doc_id for w in widgets if isinstance(w, DocumentCard)]
    assert order == ["doc-2", "doc-1"]


def test_a_reopened_question_shows_the_snapshot(qapp: Any) -> None:
    """The stored snapshot gives the same badges and exclusion, even before the card exists."""
    trail = AuditTrailTab(config=MagicMock(), storage=MagicMock())
    retracted, kept = make_doc(1), make_doc(2)
    screen = screen_documents(
        [retracted, kept], {retracted.id: make_check(RetractionStatus.RETRACTED), kept.id: unchecked_check()}
    )
    rows = RetractionScreen.from_snapshot(screen.to_snapshot())
    trail.show_retraction_snapshot(rows)
    trail.on_documents_found([retracted, kept])
    cards = trail.literature_tab
    assert cards.get_card("doc-1")._excluded_label.text() == "Excluded: retracted"
    assert cards.get_card("doc-1")._retraction_badge.text() == "RETRACTED"
    assert cards.get_card("doc-2")._retraction_badge.text() == "NOT CHECKED"


def test_banner_levels(qapp: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Retracted and concern get banners in different colours; unchecked a line; clear nothing."""
    tab = make_tab(tmp_path, monkeypatch)
    styles = {}
    for status in (RetractionStatus.RETRACTED, RetractionStatus.CONCERN, RetractionStatus.UNCHECKED):
        check = make_check(status)
        tab.show_retraction_check(check)
        assert not tab.retraction_banner.isHidden()
        assert tab.retraction_banner.text() == paper_status_line(check)
        styles[status] = tab.retraction_banner.styleSheet()
    assert len(set(styles.values())) == 3
    assert "Retracted on 2024-05-01 (notice 10.1/notice)" in paper_status_line(make_check(RetractionStatus.RETRACTED))
    for nothing in (make_check(RetractionStatus.CLEAR), None):
        tab.show_retraction_check(make_check(RetractionStatus.RETRACTED))
        tab.show_retraction_check(nothing)
        assert tab.retraction_banner.isHidden()


def test_clearing_the_document_hides_the_banner(qapp: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear empties the banner and forgets the document a late result belongs to."""
    tab = make_tab(tmp_path, monkeypatch)
    tab._retraction_doc_id = "doc-1"
    tab.show_retraction_check(make_check(RetractionStatus.RETRACTED))
    tab._clear_document()
    assert tab.retraction_banner.isHidden()
    tab._on_retraction_checked("doc-1", make_check(RetractionStatus.RETRACTED))
    assert tab.retraction_banner.isHidden()


def test_loading_a_citation_checks_the_paper_and_ignores_a_stale_result(
    qapp: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check runs in a worker; a result for an earlier document is dropped."""
    tab = make_tab(tmp_path, monkeypatch)
    worker_class = MagicMock()
    monkeypatch.setattr(tab_module, "RetractionCheckWorker", worker_class)
    monkeypatch.setattr(tab_module, "build_retraction_checker", lambda *_: "checker")
    tab.load_from_citation(make_citation())
    worker_class.assert_called_once()
    assert worker_class.call_args.args[0] == "checker"
    worker_class.return_value.start.assert_called_once()
    tab._on_retraction_checked("other-doc", make_check(RetractionStatus.RETRACTED))
    assert tab.retraction_banner.isHidden()
    tab._on_retraction_checked("doc-1", make_check(RetractionStatus.RETRACTED))
    assert not tab.retraction_banner.isHidden()


def test_a_checker_that_cannot_be_built_shows_the_unchecked_line(
    qapp: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No dialog, no raise: the unchecked line."""
    tab = make_tab(tmp_path, monkeypatch)

    def broken(*_args: Any) -> None:
        raise OSError("disk")

    monkeypatch.setattr(tab_module, "build_retraction_checker", broken)
    tab._start_retraction_check(make_doc())
    assert tab.retraction_banner.text().startswith("Retraction status not checked")


class _Checker:
    """A checker that answers or raises."""

    def __init__(self, check: PaperCheck | None) -> None:
        self.answer = check
        self.closed = False

    def check(self, documents: Any, **_kw: Any) -> dict[str, PaperCheck]:
        if self.answer is None:
            raise RuntimeError("down")
        return {d.id: self.answer for d in documents}

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("answer", [make_check(RetractionStatus.RETRACTED), None])
def test_the_worker_always_emits_one_check(qapp: Any, answer: PaperCheck | None) -> None:
    """run() called directly: the answer, or unchecked when the checker fails."""
    checker = _Checker(answer)
    worker = RetractionCheckWorker(checker, make_doc())
    got: list[tuple[str, PaperCheck]] = []
    worker.checked.connect(lambda doc_id, check: got.append((doc_id, check)))
    worker.run()
    assert len(got) == 1 and got[0][0] == "doc-1" and checker.closed
    expected = RetractionStatus.RETRACTED if answer else RetractionStatus.UNCHECKED
    assert got[0][1].verdict.status is expected


def test_the_review_tab_forwards_its_workers_screen(qapp: Any) -> None:
    """app.py connects the tab's signal; the tab must emit it when the worker's arrives."""
    from bmlibrarian_lite.gui.systematic_review_tab import SystematicReviewTab

    tab = SystematicReviewTab.__new__(SystematicReviewTab)
    got: list[Any] = []
    screen = screen_documents([make_doc()], {})
    tab.retraction_notice_label = MagicMock()
    tab.retraction_screened = MagicMock(emit=got.append)
    SystematicReviewTab._on_retraction_screened(tab, screen)
    assert got == [screen]
