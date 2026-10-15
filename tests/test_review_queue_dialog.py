"""The review queue window (#480, stage 3).

Built in the suite's QApplication with a fake service and a plain widget for
its view; slots are called directly and the application is never pumped
(#516, #517). The real view runs in the harness.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from bmlibrarian_lite.browser_rescue import RescueOutcome, RescueRequest, RescueState, RescueVerdict
from bmlibrarian_lite.browser_rescue_text import REVIEW_QUEUE_NO_BROWSER
from bmlibrarian_lite.config import LiteConfig
from bmlibrarian_lite.constants import SERVICE_UNPAYWALL_PDF
from bmlibrarian_lite.gui import review_queue_dialog as dialog_module
from bmlibrarian_lite.gui.review_queue_dialog import QueueFilter, ReviewQueueDialog
from bmlibrarian_lite.storage import LiteStorage

CHALLENGE = RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare",))


@pytest.fixture
def qapp():
    """The suite's QApplication; never pumped."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class FakeService(QObject):
    """The service's surface the window uses, recording calls."""

    shown_visit_started = Signal(object)
    shown_visit_ended = Signal(object, object)
    shown_visit_failed = Signal(object)
    entry_changed = Signal(object)
    walk_ended = Signal()
    rescued = Signal(object)

    def __init__(self) -> None:
        """Start with nothing called."""
        super().__init__()
        self.calls: list[tuple[str, Any]] = []
        self.walking = False
        self.shown_entry_id: int | None = None

    def visit_shown(self, entry: Any, view: Any) -> None:
        """Record a person's visit."""
        self.calls.append(("visit", entry.id))
        self.shown_entry_id = entry.id

    def end_shown_visit(self) -> None:
        """Record leaving the visit."""
        self.calls.append(("end", self.shown_entry_id))
        self.shown_entry_id = None

    def walk(self, entry_ids: Any, view: Any) -> None:
        """Record a walk."""
        self.calls.append(("walk", list(entry_ids)))
        self.walking = True

    def stop_walk(self) -> None:
        """Record stopping the walk."""
        self.calls.append(("stop_walk", None))
        self.walking = False


def _request(doc: str, n: int) -> RescueRequest:
    return RescueRequest(doc, f"10.1/{doc}", None, f"Title {doc}", f"https://h{n}.example/{n}.pdf",
                         f"h{n}.example", SERVICE_UNPAYWALL_PDF)


@pytest.fixture
def storage(tmp_path: Path) -> LiteStorage:
    """A queue: doc-a needs a person, doc-b is pending, doc-c has no PDF."""
    config = LiteConfig()
    config.storage.data_dir = tmp_path
    storage = LiteStorage(config)
    storage.enqueue_rescues([_request("doc-a", 1), _request("doc-b", 2), _request("doc-c", 3)])
    a, _b, c = storage.rescue_entries([RescueState.PENDING])
    storage.record_rescue_verdict(a.id, CHALLENGE)
    storage.record_rescue_verdict(c.id, RescueVerdict(RescueOutcome.NO_PDF, 200))
    return storage


def _dialog(storage: LiteStorage, service: Any, hidden: bool = True) -> ReviewQueueDialog:
    make_view = (lambda parent: QWidget(parent)) if service is not None else None
    return ReviewQueueDialog(storage, service, make_view, lambda: hidden)


def _titles(dialog: ReviewQueueDialog) -> list[str]:
    return [dialog.table.item(r, 0).text() for r in range(dialog.table.rowCount())]


def test_the_window_never_imports_qtwebengine() -> None:
    """Importing it in a fresh interpreter loads no QtWebEngine module."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys, bmlibrarian_lite.gui.review_queue_dialog; "
         "print(any(m.startswith('PySide6.QtWebEngine') for m in sys.modules))"],
        capture_output=True, text=True, timeout=120,
        env={**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False"


def test_by_default_it_lists_what_waits_for_the_reader(qapp, storage: LiteStorage) -> None:
    """With the hidden pass on, only needs_person (D4)."""
    dialog = _dialog(storage, FakeService())
    assert _titles(dialog) == ["Title doc-a"]
    dialog.deleteLater()


def test_with_the_hidden_pass_off_pending_entries_wait_for_the_reader_too(qapp, storage: LiteStorage) -> None:
    """No hidden pass: pending is the reader's too, labelled so."""
    dialog = _dialog(storage, FakeService(), hidden=False)
    assert _titles(dialog) == ["Title doc-a", "Title doc-b"]
    assert dialog.table.item(1, 3).text() == "Waiting (the hidden browser is off)"
    dialog.deleteLater()


def test_the_filter_shows_the_rest(qapp, storage: LiteStorage) -> None:
    """All entries, with host, who named them, state and last verdict."""
    dialog = _dialog(storage, FakeService())
    dialog.set_filter(QueueFilter.ALL)
    assert len(_titles(dialog)) == 3
    row = _titles(dialog).index("Title doc-c")
    assert [dialog.table.item(row, c).text() for c in range(1, 5)] == [
        "h3.example", "Unpaywall", "No PDF on the page", "The page served no PDF (HTTP 200)"
    ]
    dialog.deleteLater()


def test_selecting_an_open_entry_shows_its_page(qapp, storage: LiteStorage) -> None:
    """A person's visit of the selected entry, in the window's view."""
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.table.selectRow(0)
    assert service.calls[-1][0] == "visit"
    dialog.deleteLater()


def test_selecting_a_settled_entry_visits_nothing(qapp, storage: LiteStorage) -> None:
    """D6: its verdict is shown, and any visit ends."""
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.set_filter(QueueFilter.ALL)
    service.calls.clear()
    dialog.table.selectRow(_titles(dialog).index("Title doc-c"))
    assert all(name != "visit" for name, _ in service.calls)
    assert service.calls[-1][0] == "end"
    assert not dialog.dismiss_button.isEnabled()
    dialog.deleteLater()


def test_dismissing_ends_the_visit_and_stores_it(qapp, storage: LiteStorage) -> None:
    """Dismiss: the entry's visit ends first, then it is set aside and the queue says it changed."""
    service = FakeService()
    dialog = _dialog(storage, service)
    changed: list[bool] = []
    dialog.queue_changed.connect(lambda: changed.append(True))
    dialog.table.selectRow(0)
    dialog._dismiss_selected()
    assert service.calls[-1][0] == "end"
    assert storage.count_rescue_entries([RescueState.DISMISSED]) == 1
    assert changed == [True] and _titles(dialog) == []
    dialog.deleteLater()


def test_a_dismissal_that_cannot_be_stored_is_said(qapp, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    """Golden rule 8: the reader is told, not only the log."""
    dialog = _dialog(storage, FakeService())
    dialog.table.selectRow(0)
    monkeypatch.setattr(dialog_module, "dismiss_entry", lambda _q, _i: None)
    monkeypatch.setattr(storage, "rescue_entry", lambda _i: None)
    dialog._dismiss_selected()
    assert dialog.status_label.text() == dialog_module.REVIEW_QUEUE_UNWRITABLE
    dialog.deleteLater()


@pytest.mark.parametrize("address, opened", [("https://h1.example/1.pdf", True), ("javascript:alert(1)", False)])
def test_only_a_web_address_opens_in_the_system_browser(
    qapp, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch, address: str, opened: bool
) -> None:
    """A stored address is untrusted (golden rule 1)."""
    urls: list[str] = []
    monkeypatch.setattr(dialog_module.QDesktopServices, "openUrl", lambda url: urls.append(url.toString()))
    dialog = _dialog(storage, FakeService())
    dialog._open_in_system_browser(address)
    assert bool(urls) is opened
    dialog.deleteLater()


def test_a_rescue_moves_on_to_the_next_waiting_entry(qapp, storage: LiteStorage) -> None:
    """When the person's PDF arrives, the next entry opens."""
    b = storage.rescue_entries_for_document("doc-b")[0]
    storage.record_rescue_verdict(b.id, CHALLENGE)  # two entries now need the reader
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.table.selectRow(0)
    first = service.calls[-1][1]
    storage.record_rescue_verdict(first, RescueVerdict(RescueOutcome.PDF))
    entry = storage.rescue_entry(first)
    service.shown_visit_ended.emit(entry, RescueVerdict(RescueOutcome.PDF))
    assert dialog.selected_entry_id() not in (None, first)
    assert service.calls[-1] == ("visit", dialog.selected_entry_id())
    dialog.deleteLater()


def test_walk_the_queue_passes_the_waiting_entries_in_order(qapp, storage: LiteStorage) -> None:
    """The listed waiting entries, top to bottom; the button stops it; walk_ended resets it."""
    service = FakeService()
    dialog = _dialog(storage, service, hidden=False)
    dialog._toggle_walk()
    ids = [e.id for e in storage.rescue_entries([RescueState.NEEDS_PERSON, RescueState.PENDING])]
    assert service.calls[-1] == ("walk", ids)
    assert dialog.walk_button.text() == "Stop walking"
    dialog._toggle_walk()
    assert service.calls[-1] == ("stop_walk", None)
    service.walk_ended.emit()
    assert dialog.walk_button.text() == "Walk the queue"
    dialog.deleteLater()


def test_without_a_browser_the_window_still_lists_and_dismisses(qapp, storage: LiteStorage) -> None:
    """D12: a note instead of the view; dismiss works; walking is off."""
    dialog = _dialog(storage, None)
    assert dialog.view is None
    assert REVIEW_QUEUE_NO_BROWSER in dialog.view_note.text()
    assert not dialog.walk_button.isEnabled()
    dialog.table.selectRow(0)
    dialog._dismiss_selected()
    assert storage.count_rescue_entries([RescueState.DISMISSED]) == 1
    dialog.deleteLater()


def test_closing_the_window_ends_the_visit_and_the_walk(qapp, storage: LiteStorage) -> None:
    """Review focus 1: nothing keeps running behind a closed window."""
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.table.selectRow(0)
    service.walking = True
    dialog.done(0)
    assert ("stop_walk", None) in service.calls
    assert service.calls[-1][0] == "end"
    # The close box goes through reject(), which reaches done()
    service.calls.clear()
    dialog.reject()
    assert service.calls[-1][0] == "end"
    dialog.deleteLater()


def test_focus_entry_selects_it_even_when_filtered_out(qapp, storage: LiteStorage) -> None:
    """The interrogation tab's offer opens the window on its entry."""
    dialog = _dialog(storage, FakeService())
    c = storage.rescue_entries_for_document("doc-c")[0]
    dialog.focus_entry(c.id)
    assert dialog.current_filter is QueueFilter.ALL
    assert dialog.selected_entry_id() == c.id
    dialog.deleteLater()


def test_a_page_that_cannot_open_is_said_but_leaving_one_is_not(qapp, storage: LiteStorage) -> None:
    """shown_visit_failed says so; shown_visit_ended(None) alone (the reader left) says nothing."""
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.table.selectRow(0)
    entry = storage.rescue_entry(dialog.selected_entry_id())
    service.shown_visit_ended.emit(entry, None)
    assert dialog.status_label.text() != dialog_module.SHOWN_VISIT_NOT_STARTED
    service.shown_visit_failed.emit(entry)
    assert dialog.status_label.text() == dialog_module.SHOWN_VISIT_NOT_STARTED
    dialog.deleteLater()


def test_a_queue_that_cannot_be_read_is_said(qapp, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    """The list is left as it was and the reader is told."""
    import sqlite3

    dialog = _dialog(storage, FakeService())

    def fail(_states: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(storage, "rescue_entries", fail)
    dialog.refresh()
    assert dialog.status_label.text() == dialog_module.REVIEW_QUEUE_UNREADABLE
    assert _titles(dialog) == ["Title doc-a"]
    dialog.deleteLater()


def test_a_verdict_elsewhere_refreshes_the_list(qapp, storage: LiteStorage) -> None:
    """entry_changed from the hidden pass updates what the window shows."""
    service = FakeService()
    dialog = _dialog(storage, service)
    b = storage.rescue_entries_for_document("doc-b")[0]
    storage.record_rescue_verdict(b.id, CHALLENGE)
    service.entry_changed.emit(storage.rescue_entry(b.id))
    assert "Title doc-b" in _titles(dialog)
    dialog.deleteLater()


def test_a_locked_queue_on_dismiss_is_said_not_raised(
    qapp, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The follow-up read after a failed dismissal is guarded too (golden rule 8)."""
    import sqlite3

    dialog = _dialog(storage, FakeService())
    dialog.table.selectRow(0)
    changed: list[bool] = []
    dialog.queue_changed.connect(lambda: changed.append(True))

    def locked(*_a: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(storage, "dismiss_rescue", locked)
    monkeypatch.setattr(storage, "rescue_entry", locked)
    dialog._dismiss_selected()
    assert dialog.status_label.text() == dialog_module.REVIEW_QUEUE_UNWRITABLE
    assert changed == [True]
    dialog.deleteLater()
