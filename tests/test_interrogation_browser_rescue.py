"""The interrogation tab puts a walled PDF the reader asked for first (#480, stage 3).

The worker is stood in for, the queue is a real LiteStorage, the
service's signal is called directly, and the QApplication is never pumped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QObject, Signal

from bmlibrarian_lite.browser_rescue import RescueOutcome, RescueState, RescueVerdict
from bmlibrarian_lite.config import LiteConfig
from bmlibrarian_lite.constants import RESCUE_PRIORITY_WAITING, SERVICE_UNPAYWALL_PDF
from bmlibrarian_lite.data_models import (
    Citation,
    DocumentSource,
    LiteDocument,
    LookupRecord,
    RequestFailure,
    RequestFailureKind,
    SourceLookupFailure,
)
from bmlibrarian_lite.fulltext_discovery import FulltextResult, FulltextSourceType
from bmlibrarian_lite.gui import document_interrogation_tab as tab_module
from bmlibrarian_lite.gui.document_interrogation_tab import (
    BROWSER_PDF_LABEL,
    FULLTEXT_CANCELLED,
    FULLTEXT_UNAVAILABLE,
    FULLTEXT_WALLED,
    DocumentInterrogationTab,
)
from bmlibrarian_lite.gui.workers import FulltextDiscoveryWorker
from bmlibrarian_lite.storage import LiteStorage

DOI = "10.1000/walled"
ADDRESS = "https://www.sciencedirect.com/science/article/pii/S1/pdfft"
OTHER_ADDRESS = "https://europepmc.org/articles/PMC1/pdf"


def _walled(*addresses: str) -> LookupRecord:
    return LookupRecord(failures=tuple(
        SourceLookupFailure(SERVICE_UNPAYWALL_PDF, RequestFailure(RequestFailureKind.CHALLENGED, 403), a)
        for a in addresses
    ))


def test_the_worker_keeps_the_record_of_a_failed_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """D10: the record is on the worker before its terminal signal."""
    worker = FulltextDiscoveryWorker({"doi": DOI})
    result = FulltextResult(success=False, source_type=FulltextSourceType.NOT_FOUND,
                            error="no", lookups=_walled(ADDRESS))
    monkeypatch.setattr(worker, "_build_discoverer",
                        lambda: MagicMock(discover_fulltext=lambda doc_dict: result))
    seen: list[LookupRecord] = []
    worker.error.connect(lambda _e: seen.append(worker.lookups))
    worker._discover()
    assert seen == [_walled(ADDRESS)]


class StubFulltextWorker(QObject):
    """Full-text discovery that answers only when the test emits for it."""

    progress = Signal(str, str)
    finished = Signal(str, str, str)
    paywall_detected = Signal(str, str)
    error = Signal(str)
    cancelled = Signal(str)

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        """Take the real worker's arguments and ignore them."""
        super().__init__()
        self.lookups = LookupRecord()

    def start(self) -> None:
        """Nothing runs until the test emits."""

    def cancel(self) -> None:
        """Ignored."""


@pytest.fixture
def qapp() -> Any:
    """The QApplication the tab needs; never pumped."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _citation(doi: str | None = DOI, doc_id: str = "doc-1") -> Citation:
    document = LiteDocument(id=doc_id, title="Walled trial", abstract="An abstract.", authors=["Smith J"],
                            year=2024, journal="J", pmid="1", doi=doi, source=DocumentSource.EUROPEPMC)
    return Citation(document=document, passage="An abstract.", relevance_score=4)


class Harness:
    """The tab, its queue, and what it did."""

    def __init__(self, tab: DocumentInterrogationTab, storage: LiteStorage) -> None:
        """Record nothing yet."""
        self.tab = tab
        self.storage = storage
        self.abstracts: list[str | None] = []
        self.pdfs: list[tuple[Path, str]] = []
        self.offers: list[bool] = []
        self.queued: list[bool] = []
        self.requested: list[int] = []

    def fail_with(self, record: LookupRecord) -> None:
        """End the citation's discovery with no full text and this record."""
        worker = self.tab._fulltext_worker
        worker.lookups = record
        worker.error.emit("no full text")

    def entries(self) -> list[Any]:
        """The document's queue entries."""
        return self.storage.rescue_entries_for_document("doc-1")

    def verdict(self, address: str, verdict: RescueVerdict) -> None:
        """Record a verdict as the service would, and tell the tab."""
        entry = next(e for e in self.entries() if e.request.address == address)
        recorded = self.storage.record_rescue_verdict(entry.id, verdict)
        self.tab.on_rescue_entry_changed(recorded.entry)


@pytest.fixture
def make(qapp: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Make a harness: the hidden pass on or off, the offer accepted or not."""

    def build(hidden: bool = True, accept_offer: bool = False, citation: Citation | None = None) -> Harness:
        monkeypatch.setattr(tab_module, "LiteInterrogationAgent", MagicMock())
        monkeypatch.setattr(tab_module, "QMessageBox", MagicMock())
        monkeypatch.setattr(tab_module, "find_existing_fulltext", lambda _m: None)
        monkeypatch.setattr(tab_module, "find_existing_pdf", lambda _m: None)
        monkeypatch.setattr(tab_module, "FulltextDiscoveryWorker", StubFulltextWorker)
        monkeypatch.setattr(tab_module, "rescue_pdf_path", lambda doi: tmp_path / "rescued.pdf")
        config = LiteConfig()
        config.storage.data_dir = tmp_path
        storage = LiteStorage(config)
        tab = DocumentInterrogationTab(config=config, storage=storage)
        tab.document_view = MagicMock()
        harness = Harness(tab, storage)
        monkeypatch.setattr(tab, "_load_citation_abstract",
                            lambda _c, degradation=None: harness.abstracts.append(degradation))
        monkeypatch.setattr(tab, "_load_citation_pdf",
                            lambda path, _c, label: harness.pdfs.append((path, label)))
        monkeypatch.setattr(tab_module, "ask_to_open_review_queue",
                            lambda _parent: harness.offers.append(True) or accept_offer)
        tab.rescue_queued.connect(lambda: harness.queued.append(True))
        tab.review_queue_requested.connect(harness.requested.append)
        tab.set_hidden_pass_check(lambda: hidden)
        tab.load_from_citation(citation or _citation())
        return harness

    return build


def test_a_walled_pdf_is_queued_first_and_the_reader_waits(make: Any) -> None:
    """Queued at the waiting priority; the service is told; nothing is loaded yet."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    (entry,) = h.entries()
    assert entry.priority == RESCUE_PRIORITY_WAITING and entry.state is RescueState.PENDING
    assert h.queued == [True]
    assert h.abstracts == [] and h.pdfs == []
    assert h.tab._pdf_progress_dialog is not None
    assert h.tab._pdf_progress_dialog.labelText() == tab_module.BROWSER_RESCUE_WAITING


def test_a_rescue_loads_the_pdf_into_the_session(make: Any, tmp_path: Path) -> None:
    """The PDF the browser saved is loaded, labelled as fetched in the built-in browser."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    (tmp_path / "rescued.pdf").write_bytes(b"%PDF-1.4\n")
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.PDF))
    assert h.pdfs == [(tmp_path / "rescued.pdf", BROWSER_PDF_LABEL)]
    assert h.abstracts == [] and h.tab._pdf_progress_dialog is None


def test_a_rescued_pdf_missing_from_disk_falls_back(make: Any) -> None:
    """Never load what is not there."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.PDF))
    assert h.pdfs == [] and h.abstracts == [FULLTEXT_UNAVAILABLE]


def test_a_wall_the_hidden_pass_could_not_pass_offers_the_queue(make: Any) -> None:
    """needs_person: the abstract, named as walled, and the offer; accepted, it opens on that entry."""
    h = make(accept_offer=True)
    h.fail_with(_walled(ADDRESS))
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare",)))
    assert h.abstracts == [FULLTEXT_WALLED]
    assert h.offers == [True]
    assert h.requested == [h.entries()[0].id]


def test_a_declined_offer_opens_nothing(make: Any) -> None:
    """Not now: the abstract stays, the entry stays queued."""
    h = make(accept_offer=False)
    h.fail_with(_walled(ADDRESS))
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.CHALLENGE, 403, ()))
    assert h.offers == [True] and h.requested == []
    assert h.entries()[0].state is RescueState.NEEDS_PERSON


def test_no_pdf_behind_the_wall_keeps_today_s_fallback(make: Any) -> None:
    """no_pdf: "could not be retrieved", and no offer."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.NO_PDF, 200))
    assert h.abstracts == [FULLTEXT_UNAVAILABLE] and h.offers == []


def test_a_transport_failure_ends_the_wait_with_the_offer(make: Any) -> None:
    """D8: the retry is 15 minutes away; the reader is not kept waiting."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.TRANSPORT))
    assert h.abstracts == [FULLTEXT_WALLED] and h.offers == [True]


def test_two_addresses_wait_for_both_or_one_rescue(make: Any, tmp_path: Path) -> None:
    """One wall answered, the other still to visit: still waiting; then its rescue loads."""
    h = make()
    h.fail_with(_walled(ADDRESS, OTHER_ADDRESS))
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.CHALLENGE, 403, ()))
    assert h.abstracts == [] and h.pdfs == []
    (tmp_path / "rescued.pdf").write_bytes(b"%PDF-1.4\n")
    h.verdict(OTHER_ADDRESS, RescueVerdict(RescueOutcome.PDF))
    assert len(h.pdfs) == 1


def test_with_the_hidden_pass_off_the_tab_offers_the_queue_at_once(make: Any) -> None:
    """Review focus 3: no pass will come, so no wait."""
    h = make(hidden=False)
    h.fail_with(_walled(ADDRESS))
    assert h.tab._pdf_progress_dialog is None
    assert h.abstracts == [FULLTEXT_WALLED] and h.offers == [True]
    assert h.entries()[0].priority == RESCUE_PRIORITY_WAITING


def test_the_wait_ends_at_its_limit_with_the_offer(make: Any) -> None:
    """Review focus 2: a slow or stuck pass never keeps the reader waiting past the limit."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    h.tab._on_rescue_wait_limit()
    assert h.abstracts == [FULLTEXT_WALLED] and h.offers == [True]
    assert h.tab._pdf_progress_dialog is None


def test_cancelling_the_wait_falls_back_and_keeps_the_entry(make: Any, tmp_path: Path) -> None:
    """Cancel: "cancelled", the entry stays queued, a later rescue is not loaded here (D9)."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    h.tab._cancel_rescue_wait()
    assert h.abstracts == [FULLTEXT_CANCELLED]
    (tmp_path / "rescued.pdf").write_bytes(b"%PDF-1.4\n")
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.PDF))
    assert h.pdfs == []


def test_another_document_s_verdict_is_ignored(make: Any) -> None:
    """Only this document's entries end its wait, even at the same address."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    other = h.storage
    other.enqueue_rescues(list(tab_module.rescue_requests(
        tab_module.RescueSubject("doc-2", "10.1/o", None, "O"), _walled(ADDRESS))))
    entry = other.rescue_entries_for_document("doc-2")[0]
    h.tab.on_rescue_entry_changed(other.record_rescue_verdict(entry.id, RescueVerdict(RescueOutcome.PDF)).entry)
    assert h.pdfs == [] and h.abstracts == []


def test_loading_another_citation_drops_the_wait(make: Any) -> None:
    """The reader moved on: a later verdict for the first document does nothing."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    h.tab.load_from_citation(_citation(doc_id="doc-2", doi="10.1/other"))
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.CHALLENGE, 403, ()))
    assert h.offers == [] and h.abstracts == []


def test_a_failure_with_nothing_walled_is_today_s_fallback(make: Any) -> None:
    """Control: no challenged copy, nothing queued, the abstract as before."""
    h = make()
    h.fail_with(LookupRecord())
    assert h.entries() == [] and h.queued == []
    assert h.abstracts == [FULLTEXT_UNAVAILABLE]


def test_a_document_without_a_doi_is_not_queued(make: Any) -> None:
    """No DOI, no file name for the PDF: today's fallback."""
    h = make(citation=_citation(doi=None))
    h.fail_with(_walled(ADDRESS))
    assert h.entries() == [] and h.abstracts == [FULLTEXT_UNAVAILABLE]


def test_a_queue_that_cannot_be_written_is_today_s_fallback(make: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Golden rule 8: logged; the reader gets the abstract, not a hang."""
    import sqlite3

    h = make()

    def fail(*_a: Any, **_k: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(h.storage, "rescue_entries_for_document", fail)
    h.fail_with(_walled(ADDRESS))
    assert h.abstracts == [FULLTEXT_UNAVAILABLE]
    assert h.tab._pdf_progress_dialog is None


@pytest.mark.parametrize("choose_open", [True, False])
def test_the_offer_says_whether_the_reader_chose_the_queue(
    qapp: Any, monkeypatch: pytest.MonkeyPatch, choose_open: bool
) -> None:
    """The offer is True only for its "Open the review queue" button."""
    from PySide6.QtWidgets import QMessageBox, QWidget

    def click(box: QMessageBox) -> int:
        wanted = tab_module.REVIEW_QUEUE_OFFER_OPEN if choose_open else tab_module.REVIEW_QUEUE_OFFER_LATER
        button = next(b for b in box.buttons() if b.text() == wanted)
        button.click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", click)
    parent = QWidget()
    assert tab_module.ask_to_open_review_queue(parent) is choose_open
    parent.deleteLater()
