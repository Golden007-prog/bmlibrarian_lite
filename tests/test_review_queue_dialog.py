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

from bmlibrarian_lite.browser_rescue import (
    RescueOutcome,
    RescueRequest,
    RescueState,
    RescueVerdict,
    WalkEnd,
    WalkEndReason,
)
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
    """The service's surface the window uses, recording calls.

    Coupled as the real service is: choosing an entry or ending the visit
    stops a walk, stopping one says it ended, and a walk with nothing still
    open ends before ``walk`` returns (``walk_ends_at_once``).
    """

    shown_visit_started = Signal(object)
    shown_visit_ended = Signal(object, object)
    shown_visit_failed = Signal(object)
    shown_download_ignored = Signal(object)
    shown_download_mismatched = Signal(object)
    entry_changed = Signal(object)
    walk_ended = Signal(object)
    rescued = Signal(object)

    def __init__(self) -> None:
        """Start with nothing called."""
        super().__init__()
        self.calls: list[tuple[str, Any]] = []
        self.walking = False
        self.shown_entry_id: int | None = None
        self.holds_unsaved_verdict = False
        self.walk_ends_at_once: WalkEnd | None = None

    def visit_shown(self, entry: Any, view: Any) -> None:
        """Record a person's visit; it stops a walk."""
        if self.walking:
            self.stop_walk()
        self.calls.append(("visit", entry.id))
        self.shown_entry_id = entry.id

    def end_shown_visit(self) -> None:
        """Record leaving the visit; it stops a walk."""
        if self.walking:
            self.stop_walk()
        self.calls.append(("end", self.shown_entry_id))
        self.shown_entry_id = None

    def walk(self, entry_ids: Any, view: Any) -> None:
        """Record a walk, which may end at once."""
        self.calls.append(("walk", list(entry_ids)))
        if self.walk_ends_at_once is not None:
            self.walk_ended.emit(self.walk_ends_at_once)
            return
        self.walking = True

    def keep_shown_pdf(self) -> bool:
        """Record keeping the held PDF."""
        self.calls.append(("keep", self.shown_entry_id))
        return True

    def stop_walk(self) -> None:
        """Record stopping the walk, which says it ended."""
        self.calls.append(("stop_walk", None))
        self.walking = False
        self.walk_ended.emit(WalkEnd(WalkEndReason.STOPPED))


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
    a = storage.rescue_entries_for_document("doc-a")[0]
    assert service.calls[-1] == ("visit", a.id)
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
    """Dismiss: the entry is set aside, its visit ends, and the queue says it changed."""
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


def test_a_dismissal_of_an_entry_settled_elsewhere_is_not_called_a_failed_write(
    qapp, storage: LiteStorage
) -> None:
    """Rescued by another process meanwhile: said as such, never "could not be dismissed"."""
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.table.selectRow(0)
    a = dialog.selected_entry_id()
    assert a is not None
    storage.record_rescue_verdict(a, RescueVerdict(RescueOutcome.PDF))
    dialog._dismiss_selected()
    assert dialog.status_label.text() == dialog_module.REVIEW_QUEUE_NOT_OPEN
    assert storage.rescue_entry(a).state is RescueState.RESCUED
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
    """Nothing keeps running behind a closed window."""
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
    before = dialog.status_label.text()
    service.shown_visit_ended.emit(entry, None)
    assert dialog.status_label.text() == before
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
    """A dismissal that cannot be written is said, and leaves the visit as it was (golden rule 8)."""
    import sqlite3

    dialog = _dialog(storage, FakeService())
    dialog.table.selectRow(0)
    changed: list[bool] = []
    dialog.queue_changed.connect(lambda: changed.append(True))

    def locked(*_a: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    service = dialog._service
    assert isinstance(service, FakeService)
    service.calls.clear()
    monkeypatch.setattr(storage, "dismiss_rescue", locked)
    dialog._dismiss_selected()
    assert dialog.status_label.text() == dialog_module.REVIEW_QUEUE_UNWRITABLE
    # Not dismissed: the queue did not change, and the reader's page stays open
    assert changed == [] and service.calls == []
    dialog.deleteLater()


# -- the PR review's findings (#480, stage 3) --------------------------------------


def test_a_walk_that_ends_before_it_returns_leaves_no_stop_button(qapp, storage: LiteStorage) -> None:
    """Every listed entry settled elsewhere: the walk ends inside walk(); the button and line say so."""
    service = FakeService()
    service.walk_ends_at_once = WalkEnd(WalkEndReason.FINISHED)
    dialog = _dialog(storage, service)
    dialog._toggle_walk()
    assert dialog.walk_button.text() == "Walk the queue"
    assert dialog.status_label.text() == "The walk is over."
    dialog.deleteLater()


def test_a_walk_stopped_by_an_unreadable_queue_says_so(qapp, storage: LiteStorage) -> None:
    """Unreadable is not "the walk is over"."""
    service = FakeService()
    service.walk_ends_at_once = WalkEnd(WalkEndReason.UNREADABLE)
    dialog = _dialog(storage, service)
    dialog._toggle_walk()
    assert "could not be read" in dialog.status_label.text()
    dialog.deleteLater()


def test_walk_with_nothing_waiting_says_so(qapp, storage: LiteStorage) -> None:
    """No walk is asked for, and the reader is told why nothing happened."""
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.set_filter(QueueFilter.ALL)
    for entry in storage.rescue_entries(list(RescueState)):
        if entry.state is RescueState.NEEDS_PERSON:
            storage.dismiss_rescue(entry.id)
    dialog.refresh()
    service.calls.clear()
    dialog._toggle_walk()
    assert service.calls == []
    assert dialog.status_label.text() == dialog_module.WALK_NOTHING_WAITING
    assert dialog.walk_button.text() == "Walk the queue"
    dialog.deleteLater()


def test_a_refresh_does_not_hand_the_selection_to_another_entry(qapp, storage: LiteStorage) -> None:
    """The selected entry left the list: nothing is selected, so Dismiss cannot reach the wrong article."""
    b = storage.rescue_entries_for_document("doc-b")[0]
    storage.record_rescue_verdict(b.id, CHALLENGE)
    dialog = _dialog(storage, FakeService())
    dialog.table.selectRow(0)
    first = dialog.selected_entry_id()
    assert first is not None
    storage.record_rescue_verdict(first, RescueVerdict(RescueOutcome.PDF))
    dialog.refresh()
    assert dialog.selected_entry_id() is None
    dialog.deleteLater()


def test_the_selection_follows_its_entry_when_its_row_moves(qapp, storage: LiteStorage) -> None:
    """Control: an entry still listed stays selected wherever its row now is."""
    dialog = _dialog(storage, FakeService(), hidden=False)
    assert _titles(dialog) == ["Title doc-a", "Title doc-b"]
    b = storage.rescue_entries_for_document("doc-b")[0]
    dialog.focus_entry(b.id)
    storage.dismiss_rescue(storage.rescue_entries_for_document("doc-a")[0].id)
    dialog.refresh()
    assert _titles(dialog) == ["Title doc-b"]
    assert dialog.selected_entry_id() == b.id
    dialog.deleteLater()


def test_reopening_the_window_on_its_entry_shows_it_again(qapp, storage: LiteStorage) -> None:
    """Closed and reopened by the tab's offer: the entry's page is shown again, not an empty view."""
    service = FakeService()
    dialog = _dialog(storage, service)
    a = storage.rescue_entries_for_document("doc-a")[0]
    dialog.focus_entry(a.id)
    dialog.done(0)
    service.calls.clear()
    dialog.refresh()
    dialog.focus_entry(a.id)
    assert service.calls == [("visit", a.id)]
    dialog.deleteLater()


def test_focusing_the_entry_already_shown_does_not_reload_it(qapp, storage: LiteStorage) -> None:
    """Control: the window's visit already under way is left alone."""
    service = FakeService()
    dialog = _dialog(storage, service)
    a = storage.rescue_entries_for_document("doc-a")[0]
    dialog.focus_entry(a.id)
    service.calls.clear()
    dialog.focus_entry(a.id)
    assert service.calls == []
    dialog.deleteLater()


def test_a_rescue_during_a_walk_does_not_move_on_by_itself(qapp, storage: LiteStorage) -> None:
    """The walk visits the next entry; selecting one would stop it after its first PDF."""
    b = storage.rescue_entries_for_document("doc-b")[0]
    storage.record_rescue_verdict(b.id, CHALLENGE)
    service = FakeService()
    dialog = _dialog(storage, service)
    service.walking = True
    a = storage.rescue_entries_for_document("doc-a")[0]
    storage.record_rescue_verdict(a.id, RescueVerdict(RescueOutcome.PDF))
    service.calls.clear()
    service.shown_visit_ended.emit(storage.rescue_entry(a.id), RescueVerdict(RescueOutcome.PDF))
    assert service.calls == [] and service.walking
    dialog.deleteLater()


def test_a_verdict_not_yet_saved_is_said_and_nothing_else_opens(qapp, storage: LiteStorage) -> None:
    """Never "the PDF arrived" and "opening…" while nothing can open until the save."""
    b = storage.rescue_entries_for_document("doc-b")[0]
    storage.record_rescue_verdict(b.id, CHALLENGE)
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.table.selectRow(0)
    first = dialog.selected_entry_id()
    service.holds_unsaved_verdict = True
    service.calls.clear()
    service.shown_visit_ended.emit(storage.rescue_entry(first), RescueVerdict(RescueOutcome.PDF))
    assert dialog.status_label.text() == dialog_module.VERDICT_UNSAVED
    assert service.calls == []
    dialog.deleteLater()


def test_an_entry_settled_before_its_page_opened_is_said(qapp, storage: LiteStorage) -> None:
    """Rescued while its visit waited: the reader is told, not left with "opening…"."""
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.table.selectRow(0)
    a = dialog.selected_entry_id()
    storage.record_rescue_verdict(a, RescueVerdict(RescueOutcome.PDF))
    service.shown_visit_ended.emit(storage.rescue_entry(a), None)
    assert dialog.status_label.text().startswith(dialog_module.SHOWN_VISIT_SETTLED_MEANWHILE)
    assert "Fetched" in dialog.status_label.text()
    dialog.deleteLater()


def test_a_download_that_was_no_pdf_is_said(qapp, storage: LiteStorage) -> None:
    """The page stays open; the reader is told to download the article's own PDF."""
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.table.selectRow(0)
    service.shown_download_ignored.emit(storage.rescue_entry(dialog.selected_entry_id()))
    assert dialog.status_label.text() == dialog_module.SHOWN_VISIT_NOT_A_PDF
    dialog.deleteLater()


@pytest.mark.parametrize(
    ("address", "opens", "text"),
    [
        ("javascript:alert(1)", True, dialog_module.SYSTEM_BROWSER_REFUSED),
        ("https://h1.example/1.pdf", False, dialog_module.SYSTEM_BROWSER_FAILED),
    ],
)
def test_an_address_the_system_browser_does_not_open_is_said(
    qapp, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch, address: str, opens: bool, text: str
) -> None:
    """Refused as no web address, or refused by the system: the reader is told either way."""
    monkeypatch.setattr(dialog_module.QDesktopServices, "openUrl", lambda _url: opens)
    dialog = _dialog(storage, FakeService())
    dialog._open_in_system_browser(address)
    assert dialog.status_label.text() == text
    dialog.deleteLater()


def test_a_queue_read_again_clears_the_unreadable_line(
    qapp, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fault is over once a read succeeds: its sentence goes too."""
    import sqlite3

    dialog = _dialog(storage, FakeService())
    read = storage.rescue_entries

    def fail(_states: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(storage, "rescue_entries", fail)
    dialog.refresh()
    monkeypatch.setattr(storage, "rescue_entries", read)
    dialog.refresh()
    assert dialog.status_label.text() == ""
    dialog.deleteLater()


def test_closing_the_window_leaves_nothing_selected(qapp, storage: LiteStorage) -> None:
    """Reopened, the window shows no page: a selection kept would claim one."""
    dialog = _dialog(storage, FakeService())
    dialog.table.selectRow(0)
    dialog.done(0)
    assert dialog.selected_entry_id() is None
    dialog.deleteLater()


def test_a_pdf_that_is_not_the_article_s_is_said_and_can_be_kept(qapp, storage: LiteStorage) -> None:
    """Said, with a button to keep it anyway (#532); keeping hides the button."""
    service = FakeService()
    dialog = _dialog(storage, service)
    assert dialog.keep_pdf_button.isHidden()
    dialog.table.selectRow(0)
    service.shown_download_mismatched.emit(storage.rescue_entry(dialog.selected_entry_id()))
    assert dialog.status_label.text() == dialog_module.SHOWN_VISIT_WRONG_PDF
    assert not dialog.keep_pdf_button.isHidden()
    dialog.keep_pdf_button.click()
    assert ("keep", dialog.selected_entry_id()) in service.calls
    assert dialog.keep_pdf_button.isHidden()
    dialog.deleteLater()


@pytest.mark.parametrize("how", ["ended", "ignored", "started"])
def test_the_keep_button_goes_when_the_held_pdf_does(qapp, storage: LiteStorage, how: str) -> None:
    """A visit's end, a next download that was no PDF, or another visit: nothing is held."""
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.table.selectRow(0)
    entry = storage.rescue_entry(dialog.selected_entry_id())
    service.shown_download_mismatched.emit(entry)
    if how == "ended":
        service.shown_visit_ended.emit(entry, None)
    elif how == "ignored":
        service.shown_download_ignored.emit(entry)
    else:
        service.shown_visit_started.emit(entry)
    assert dialog.keep_pdf_button.isHidden()
    dialog.deleteLater()
