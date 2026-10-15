"""The browser rescue's review queue window (#480, stage 3).

Lists the walled PDFs (title, host, who named the address, state, last
verdict) and shows the selected entry's page in the built-in browser, where
the reader can pass a robot check the hidden pass could not. Every visit is
the service's (``BrowserRescueService``), one at a time: this window only
asks. It never imports QtWebEngine: its view comes from ``make_view``.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum, IntEnum
from typing import TYPE_CHECKING, assert_never

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..browser_rescue import (
    OPEN_RESCUE_STATES,
    DismissOutcome,
    HeldPdfKeep,
    RescueEntry,
    RescueOutcome,
    RescueState,
    RescueVerdict,
    WalkEnd,
    awaiting_reader_states,
    dismiss_entry,
    fetchable_host,
)
from ..browser_rescue_text import (
    KEEP_HELD_PDF_LABEL,
    REVIEW_QUEUE_HELP,
    REVIEW_QUEUE_NO_BROWSER,
    REVIEW_QUEUE_NOT_OPEN,
    REVIEW_QUEUE_UNREADABLE,
    REVIEW_QUEUE_UNWRITABLE,
    SHOWN_VISIT_DOWNLOADING,
    SHOWN_VISIT_HELD_NOT_SAVED,
    SHOWN_VISIT_LOADING,
    SHOWN_VISIT_NOT_A_PDF,
    SHOWN_VISIT_NOT_STARTED,
    SHOWN_VISIT_NOTHING_HELD,
    SHOWN_VISIT_OPENING,
    SHOWN_VISIT_SETTLED_MEANWHILE,
    SHOWN_VISIT_UNREADABLE_PDF,
    SHOWN_VISIT_WRONG_PDF,
    SYSTEM_BROWSER_FAILED,
    SYSTEM_BROWSER_REFUSED,
    VERDICT_UNSAVED,
    WALK_NOTHING_WAITING,
    WALK_RUNNING,
    last_visit_text,
    retry_text,
    service_label,
    state_label,
    verdict_text,
    walk_end_text,
)
from ..constants import REVIEW_QUEUE_HEIGHT_LINES, REVIEW_QUEUE_WIDTH_CHARS
from ..exceptions import SQLiteError

if TYPE_CHECKING:
    # Type only: importing this module never loads QtWebEngine
    from PySide6.QtWebEngineWidgets import QWebEngineView

    from ..storage import LiteStorage
    from .browser_rescue_service import BrowserRescueService

logger = logging.getLogger(__name__)

WINDOW_TITLE = "Walled PDFs"
WALK_LABEL = "Walk the queue"
STOP_WALK_LABEL = "Stop walking"


class QueueFilter(Enum):
    """Which entries the list shows."""

    WAITING = "Waiting for you"
    OPEN = "Not yet settled"
    ALL = "All"


class _Column(IntEnum):
    """The list's columns."""

    TITLE = 0
    HOST = 1
    NAMED_BY = 2
    STATE = 3
    VERDICT = 4


_HEADERS = {
    _Column.TITLE: "Title",
    _Column.HOST: "Host",
    _Column.NAMED_BY: "Named by",
    _Column.STATE: "State",
    _Column.VERDICT: "Last visit",
}


class ReviewQueueDialog(QDialog):
    """The review queue window: non-modal, one per main window.

    Signals:
        queue_changed: The reader changed the queue here (a dismissal);
            verdicts arrive through the service's ``entry_changed``.
    """

    queue_changed = Signal()

    def __init__(
        self,
        storage: LiteStorage,
        service: BrowserRescueService | None,
        make_view: Callable[[QWidget], QWebEngineView] | None,
        hidden_pass_runs: Callable[[], bool],
        parent: QWidget | None = None,
    ) -> None:
        """Build the window and list the waiting entries.

        Args:
            storage: The queue.
            service: The browser rescue; ``None`` when QtWebEngine is
                unavailable (D12).
            make_view: Makes the view a visit is shown in; ``None`` with no
                service.
            hidden_pass_runs: Whether a hidden pass will visit pending entries.
            parent: The main window.
        """
        super().__init__(parent)
        self._storage = storage
        self._service = service
        self._hidden_pass_runs = hidden_pass_runs
        self._entries: list[RescueEntry] = []
        self._filter = QueueFilter.WAITING
        self.setWindowTitle(WINDOW_TITLE)
        self.setModal(False)
        metrics = self.fontMetrics()
        self.resize(
            metrics.averageCharWidth() * REVIEW_QUEUE_WIDTH_CHARS,
            metrics.height() * REVIEW_QUEUE_HEIGHT_LINES,
        )
        self._build(make_view if service is not None else None)
        if service is not None:
            service.shown_visit_started.connect(self._on_visit_started)
            service.shown_visit_ended.connect(self._on_visit_ended)
            service.shown_visit_failed.connect(self._on_visit_failed)
            service.shown_download_started.connect(self._on_download_started)
            service.shown_download_ignored.connect(self._on_download_ignored)
            service.shown_download_mismatched.connect(self._on_download_mismatched)
            service.shown_download_unreadable.connect(self._on_download_unreadable)
            service.entry_changed.connect(self.refresh)
            service.walk_ended.connect(self._on_walk_ended)
        self.refresh()

    # -- building --------------------------------------------------------------

    def _build(self, make_view: Callable[[QWidget], QWebEngineView] | None) -> None:
        """Lay the window out.

        Args:
            make_view: Makes the view; ``None`` shows a note instead.
        """
        layout = QVBoxLayout(self)
        help_label = QLabel(REVIEW_QUEUE_HELP)
        help_label.setObjectName("reviewQueueHelp")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.filter_box = QComboBox()
        for option in QueueFilter:
            self.filter_box.addItem(option.value, option)
        self.filter_box.currentIndexChanged.connect(
            lambda _i: self.set_filter(self.filter_box.currentData())
        )
        left_layout.addWidget(self.filter_box)
        self.table = QTableWidget(0, len(_Column))
        self.table.setHorizontalHeaderLabels([_HEADERS[c] for c in _Column])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(_Column.TITLE, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self.table)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel("")
        self.status_label.setObjectName("reviewQueueStatus")
        self.status_label.setWordWrap(True)
        right_layout.addWidget(self.status_label)
        self.view: QWebEngineView | None = make_view(right) if make_view is not None else None
        self.view_note: QLabel | None = None
        if self.view is not None:
            right_layout.addWidget(self.view, 1)
        else:
            self.view_note = QLabel(REVIEW_QUEUE_NO_BROWSER)
            self.view_note.setObjectName("reviewQueueHelp")
            self.view_note.setWordWrap(True)
            right_layout.addWidget(self.view_note, 1)
        buttons = QHBoxLayout()
        self.reload_button = QPushButton("Reload")
        self.reload_button.clicked.connect(self._reload)
        self.system_browser_button = QPushButton("Open in system browser")
        self.system_browser_button.clicked.connect(self._open_selected_in_system_browser)
        self.dismiss_button = QPushButton("Dismiss")
        self.dismiss_button.clicked.connect(self._dismiss_selected)
        self.walk_button = QPushButton(WALK_LABEL)
        self.walk_button.clicked.connect(self._toggle_walk)
        self.walk_button.setEnabled(self._service is not None and self.view is not None)
        # Shown from a held PDF's notice (a PDF that does not carry the
        # title) until the visit's next download starts, or it ends
        self.keep_pdf_button = QPushButton(KEEP_HELD_PDF_LABEL)
        self.keep_pdf_button.clicked.connect(self._keep_held_pdf)
        self.keep_pdf_button.setVisible(False)
        for button in (
            self.reload_button, self.system_browser_button, self.dismiss_button, self.keep_pdf_button
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.walk_button)
        right_layout.addLayout(buttons)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

    # -- the list ------------------------------------------------------------------

    @property
    def current_filter(self) -> QueueFilter:
        """The list's filter.

        Returns:
            It.
        """
        return self._filter

    def _states(self) -> frozenset[RescueState]:
        """The states the current filter lists."""
        if self._filter is QueueFilter.WAITING:
            return awaiting_reader_states(self._hidden_pass_runs())
        if self._filter is QueueFilter.OPEN:
            return OPEN_RESCUE_STATES
        return frozenset(RescueState)

    def set_filter(self, queue_filter: QueueFilter) -> None:
        """Show another set of entries.

        Args:
            queue_filter: The filter.
        """
        self._filter = queue_filter
        index = self.filter_box.findData(queue_filter)
        if index != self.filter_box.currentIndex():
            self.filter_box.blockSignals(True)
            self.filter_box.setCurrentIndex(index)
            self.filter_box.blockSignals(False)
        self.refresh()

    def refresh(self, *_args: object) -> None:
        """Read the queue again, keeping the selection when it is still listed.

        Connected to ``entry_changed``, whose entry is ignored. A queue that
        cannot be read leaves the list as it was and says so.

        Args:
            *_args: Ignored.
        """
        try:
            entries = self._storage.rescue_entries(self._states())
        except (sqlite3.Error, SQLiteError) as e:
            logger.error("The browser rescue queue could not be read for its window: %s", e)
            self.status_label.setText(REVIEW_QUEUE_UNREADABLE)
            return
        if self.status_label.text() == REVIEW_QUEUE_UNREADABLE:
            # Read again: the fault the line spoke of is over
            self.status_label.setText("")
        selected = self.selected_entry_id()
        self._entries = entries
        hidden = self._hidden_pass_runs()
        now = datetime.now(UTC)
        self.table.blockSignals(True)
        # Rows are reused: a row left selected would hand its selection to
        # whichever entry now fills it, with no selection change to say so
        self.table.clearSelection()
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            state = state_label(entry.state, hidden)
            retry = retry_text(entry, now)
            cells = {
                _Column.TITLE: entry.request.title or entry.request.doi,
                _Column.HOST: entry.request.host,
                _Column.NAMED_BY: service_label(entry.request.service),
                _Column.STATE: f"{state}; {retry}" if retry else state,
                _Column.VERDICT: last_visit_text(entry),
            }
            for column, text in cells.items():
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, entry.id)
                self.table.setItem(row, column, item)
            if entry.id == selected:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        self._update_buttons()

    def selected_entry_id(self) -> int | None:
        """The selected entry's ID.

        Returns:
            It; ``None`` when nothing is selected.
        """
        entry = self._selected_entry()
        return entry.id if entry is not None else None

    def _selected_entry(self) -> RescueEntry | None:
        """The selected entry, as last read: found by the ID its row holds, not its position."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), _Column.TITLE)
        entry_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return next((e for e in self._entries if e.id == entry_id), None)

    def focus_entry(self, entry_id: int) -> None:
        """Select an entry, switching to *All* when the filter hides it, and show it.

        An entry already selected, but not shown (the window was closed, or
        its page could not open), is shown again: selecting it once more
        changes no selection, so nothing else would.

        Args:
            entry_id: The entry.
        """
        if entry_id not in {e.id for e in self._entries}:
            self.set_filter(QueueFilter.ALL)
        for row, entry in enumerate(self._entries):
            if entry.id == entry_id:
                if self.selected_entry_id() == entry_id:
                    self._on_selection_changed()
                else:
                    self.table.selectRow(row)
                return

    # -- visits ----------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        """Show the selected open entry's page; for a settled one, its verdict only (D6)."""
        entry = self._selected_entry()
        self._update_buttons()
        if self._service is None or entry is None:
            return
        if entry.state not in OPEN_RESCUE_STATES:
            self._service.end_shown_visit()
            self.status_label.setText(last_visit_text(entry))
            return
        if self._service.shown_entry_id == entry.id:
            return
        if self.view is None:
            return
        self.status_label.setText(SHOWN_VISIT_OPENING)
        self._service.visit_shown(entry, self.view)

    def _on_visit_started(self, _entry: RescueEntry) -> None:
        """A visit's page is opening in the view."""
        self.keep_pdf_button.setVisible(False)
        walking = self._service is not None and self._service.walking
        self.status_label.setText(WALK_RUNNING if walking else SHOWN_VISIT_LOADING)

    def _on_visit_ended(self, entry: RescueEntry, verdict: RescueVerdict | None) -> None:
        """Say how a visit ended; after a person's saved rescue, open the next waiting entry.

        Args:
            entry: The entry visited, as it now stands when it settled
                before its page opened.
            verdict: Its verdict; ``None`` when it was left without one.
        """
        self.keep_pdf_button.setVisible(False)
        walking = self._service is not None and self._service.walking
        unsaved = self._service is not None and self._service.holds_unsaved_verdict
        self.refresh()
        if verdict is None:
            if entry.state not in OPEN_RESCUE_STATES:
                # Settled before its page opened (rescued, dismissed meanwhile)
                hidden = self._hidden_pass_runs()
                self.status_label.setText(
                    f"{SHOWN_VISIT_SETTLED_MEANWHILE}: {state_label(entry.state, hidden)}"
                )
            # Otherwise left without a verdict: the reader moved on, or it could not open
            return
        if unsaved:
            # Nothing else opens until it is saved: saying "opening" would be untrue
            self.status_label.setText(VERDICT_UNSAVED)
            return
        self.status_label.setText(verdict_text(verdict))
        if verdict.outcome is RescueOutcome.PDF and not walking:
            self._select_next_waiting(after=entry.id)

    def _on_visit_failed(self, entry: RescueEntry) -> None:
        """A page could not be opened here (logged by the service).

        Args:
            entry: The entry.
        """
        if entry.id == self.selected_entry_id():
            self.status_label.setText(SHOWN_VISIT_NOT_STARTED)

    def _on_download_started(self, _entry: RescueEntry) -> None:
        """A person's download began: a PDF held for them is gone."""
        self.keep_pdf_button.setVisible(False)
        self.status_label.setText(SHOWN_VISIT_DOWNLOADING)

    def _on_download_ignored(self, _entry: RescueEntry) -> None:
        """A person's download was no PDF; the page stays open."""
        self.keep_pdf_button.setVisible(False)
        self.status_label.setText(SHOWN_VISIT_NOT_A_PDF)

    def _on_download_mismatched(self, _entry: RescueEntry) -> None:
        """A person's PDF does not carry the title: held, theirs to keep (#532)."""
        self.status_label.setText(SHOWN_VISIT_WRONG_PDF)
        self.keep_pdf_button.setVisible(True)

    def _on_download_unreadable(self, _entry: RescueEntry) -> None:
        """A person's PDF could not be opened; the page stays open (#532)."""
        self.keep_pdf_button.setVisible(False)
        self.status_label.setText(SHOWN_VISIT_UNREADABLE_PDF)

    def _keep_held_pdf(self) -> None:
        """Keep the held PDF as the article's; the visit then ends as a rescue.

        One no longer held, or that could not be saved, is said; the latter
        stays held, and the button with it.
        """
        self.keep_pdf_button.setVisible(False)
        if self._service is None:
            return
        match self._service.keep_shown_pdf():
            case HeldPdfKeep.KEPT:
                # The visit's end says the rest (_on_visit_ended)
                pass
            case HeldPdfKeep.NOTHING_HELD:
                self.status_label.setText(SHOWN_VISIT_NOTHING_HELD)
            case HeldPdfKeep.NOT_SAVED:
                self.status_label.setText(SHOWN_VISIT_HELD_NOT_SAVED)
                self.keep_pdf_button.setVisible(True)
            case unknown:
                assert_never(unknown)

    def _select_next_waiting(self, after: int) -> None:
        """Select the first listed open entry other than ``after``, from the top of the list.

        Args:
            after: The entry just rescued.
        """
        for row, entry in enumerate(self._entries):
            if entry.id != after and entry.state in OPEN_RESCUE_STATES:
                self.table.selectRow(row)
                return

    def _reload(self) -> None:
        """Reload the page shown."""
        if self.view is not None:
            self.view.reload()

    def _open_selected_in_system_browser(self) -> None:
        """Open the selected entry's address in the system's browser."""
        entry = self._selected_entry()
        if entry is not None:
            self._open_in_system_browser(entry.request.address)

    def _open_in_system_browser(self, address: str) -> None:
        """Open an address in the system's browser; nothing is saved from it.

        Args:
            address: A stored address, untrusted: only ``http(s)`` opens.
        """
        if fetchable_host(address) is None:
            logger.warning("Not opening a walled PDF's address that is no web address: %r", address)
            self.status_label.setText(SYSTEM_BROWSER_REFUSED)
            return
        if not QDesktopServices.openUrl(QUrl(address)):
            logger.error("The system's browser could not open %s", address)
            self.status_label.setText(SYSTEM_BROWSER_FAILED)

    def _dismiss_selected(self) -> None:
        """Set the selected open entry aside, then end its visit.

        The visit ends only once the entry is no longer open: a dismissal
        that could not be written leaves the reader's page as it was.
        """
        entry = self._selected_entry()
        if entry is None or entry.state not in OPEN_RESCUE_STATES:
            return
        result = dismiss_entry(self._storage, entry.id)
        if result.outcome is DismissOutcome.FAILED:
            self.status_label.setText(REVIEW_QUEUE_UNWRITABLE)
            return
        if self._service is not None and self._service.shown_entry_id == entry.id:
            self._service.end_shown_visit()
        if result.outcome is DismissOutcome.NOT_OPEN:
            self.status_label.setText(REVIEW_QUEUE_NOT_OPEN)
        self.refresh()
        self.queue_changed.emit()

    def _toggle_walk(self) -> None:
        """Start *Walk the queue* over the listed waiting entries, or stop it."""
        if self._service is None or self.view is None:
            return
        if self._service.walking:
            self._service.stop_walk()
            return
        waiting = awaiting_reader_states(self._hidden_pass_runs())
        ids = [e.id for e in self._entries if e.state in waiting]
        if not ids:
            self.status_label.setText(WALK_NOTHING_WAITING)
            return
        # Said before the walk starts: it can end before walk() returns (every
        # entry settled elsewhere), and walk_ended then says so
        self.walk_button.setText(STOP_WALK_LABEL)
        self.status_label.setText(WALK_RUNNING)
        self._service.walk(ids, self.view)

    def _on_walk_ended(self, end: WalkEnd) -> None:
        """The walk is over: say how, unless the reader's own act says it.

        Args:
            end: How it ended.
        """
        self.walk_button.setText(WALK_LABEL)
        self.refresh()
        text = walk_end_text(end)
        if text:
            self.status_label.setText(text)

    def _update_buttons(self) -> None:
        """Enable what applies to the selection."""
        entry = self._selected_entry()
        is_open = entry is not None and entry.state in OPEN_RESCUE_STATES
        self.dismiss_button.setEnabled(is_open)
        self.system_browser_button.setEnabled(entry is not None)
        self.reload_button.setEnabled(self.view is not None and is_open)

    # -- closing ---------------------------------------------------------------------

    def done(self, result: int) -> None:
        """Every way out ends the walk and the visit: nothing runs behind a closed window.

        Args:
            result: The dialog's result.
        """
        if self._service is not None:
            if self._service.walking:
                self._service.stop_walk()
            self._service.end_shown_visit()
        # Reopened, the window shows no page: a selection kept would show none
        # either, and selecting that row again would change nothing
        self.table.clearSelection()
        super().done(result)
