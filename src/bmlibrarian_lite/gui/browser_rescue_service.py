"""The browser rescue's visits, hidden and shown, on the GUI thread (#480, stages 2 and 3).

One visit at a time: the hidden pass's, unattended in a page with no view,
or one the review queue window shows in its view. While the window has a
visit asked for, the hidden pass takes no entry. Driven by signals and
timers only: no thread waits on it, and a run never waits for it (decisions
1 and 4 of ``docs/superpowers/specs/2026-10-12-browser-rescue-design.md``).
Each visit takes its host's pacing slot first, from the limiter our requests
use. A rescue is announced with ``rescued``, and the main window re-analyses
that document when storage holds it.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer, Signal, SignalInstance
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEngineProfile
from shiboken6 import Shiboken

from ..browser_rescue import (
    OPEN_RESCUE_STATES,
    HeldPdfKeep,
    RescueEntry,
    RescueOutcome,
    RescueState,
    RescueVerdict,
    WalkEnd,
    WalkEndReason,
    rescue_delay_ms,
    rescue_pdf_path,
)
from ..constants import (
    MAX_PDF_SIZE,
    MS_PER_SECOND,
    RESCUE_DOWNLOAD_STALL_MS,
    RESCUE_HIDDEN_TIMEOUT_MS,
    RESCUE_POLL_INTERVAL_MS,
    RESCUE_RETRY_DELAY_SECONDS,
    RESCUE_VERDICT_SAVE_RETRY_SECONDS,
    RESCUE_VISIBLE_TIMEOUT_MS,
)
from ..exceptions import SQLiteError
from .rescue_attempt import RescueAttempt, rescue_profile

if TYPE_CHECKING:
    from PySide6.QtWebEngineWidgets import QWebEngineView

    from ..config import LiteConfig
    from ..storage import LiteStorage

logger = logging.getLogger(__name__)


def _default_pdf_path(entry: RescueEntry) -> Path:
    """Where a rescued PDF goes in the reader's library.

    Args:
        entry: The entry visited.

    Returns:
        The path the next discovery's cached-PDF tier looks at first.
    """
    return rescue_pdf_path(entry.request.doi)


@dataclass
class _ShownVisit:
    """A visit the review queue window asked for.

    Attributes:
        entry: The entry shown.
        view: The window's view.
        judge_pages: Whether its page is judged (a walk) or a person's (D2).
        token: Which request this is; a start scheduled for an earlier one
            does nothing.
        attempt: The visit, once it has started.
        scheduled: Whether its start has been scheduled.
    """

    entry: RescueEntry
    view: QWebEngineView
    judge_pages: bool
    token: int
    attempt: RescueAttempt | None = None
    scheduled: bool = False


@dataclass
class _Walk:
    """*Walk the queue* under way.

    Attributes:
        ids: The entries still to visit, in order.
        view: The window's view.
        failed: How many of its pages could not be opened so far.
    """

    ids: deque[int]
    view: QWebEngineView
    failed: int = 0


class BrowserRescueService(QObject):
    """Visits queued walled PDFs one at a time: hidden, or shown in the window.

    Signals:
        rescued: A :class:`RescueEntry` whose PDF arrived and was saved.
        entry_changed: A :class:`RescueEntry` a verdict changed, hidden or
            shown, as it now stands.
        shown_visit_started: The :class:`RescueEntry` whose page now opens in
            the window's view.
        shown_visit_ended: The window's visit ended: its entry, and its
            :class:`RescueVerdict`, or ``None`` when it was left without one.
            An entry settled before its page could open (re-read at its
            start) ends with ``None`` too, as it now stands.
        shown_visit_failed: The :class:`RescueEntry` whose page could not be
            opened (logged); ``shown_visit_ended(entry, None)`` precedes it.
        shown_download_started: The :class:`RescueEntry` of a person's visit
            whose download began; a PDF held for it is gone.
        shown_download_ignored: The :class:`RescueEntry` of a person's visit
            whose download was no PDF; the page stays open.
        shown_download_mismatched: The :class:`RescueEntry` of a person's
            visit whose PDF does not carry the article's title (#532); it is
            held for :meth:`keep_shown_pdf`, and the page stays open.
        shown_download_unreadable: The :class:`RescueEntry` of a person's
            visit whose PDF could not be opened (#532); the page stays open.
        walk_ended: *Walk the queue* is over: its :class:`WalkEnd`.
    """

    rescued = Signal(object)
    entry_changed = Signal(object)
    shown_visit_started = Signal(object)
    shown_visit_ended = Signal(object, object)
    shown_visit_failed = Signal(object)
    shown_download_started = Signal(object)
    shown_download_ignored = Signal(object)
    shown_download_mismatched = Signal(object)
    shown_download_unreadable = Signal(object)
    walk_ended = Signal(object)

    def __init__(
        self,
        storage: LiteStorage,
        config: LiteConfig,
        profile_dir: Path,
        *,
        pdf_path_for: Callable[[RescueEntry], Path] | None = None,
        timeout_ms: int = RESCUE_HIDDEN_TIMEOUT_MS,
        visible_timeout_ms: int = RESCUE_VISIBLE_TIMEOUT_MS,
        stall_ms: int = RESCUE_DOWNLOAD_STALL_MS,
        poll_ms: int = RESCUE_POLL_INTERVAL_MS,
        max_pdf_bytes: int = MAX_PDF_SIZE,
        clock: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
    ) -> None:
        """Prepare the pass; :meth:`start` begins it.

        Args:
            storage: The queue.
            config: The live configuration; its hidden-pass setting is read
                when an entry is taken and again when its visit starts, after
                the host's pacing slot.
            profile_dir: The browser's persistent profile.
            pdf_path_for: Where an entry's PDF is saved; the reader's library
                by default.
            timeout_ms: How long a hidden visit waits for a download.
            visible_timeout_ms: How long a walk's visit waits for a download.
            stall_ms: How long a download may receive nothing.
            poll_ms: How often the queue is read for entries written
                elsewhere.
            max_pdf_bytes: The largest PDF kept.
            clock: A monotonic clock in seconds; replaced in tests.
            parent: The owning object.
        """
        super().__init__(parent)
        self._storage = storage
        self._config = config
        self._profile_dir = profile_dir
        self._pdf_path_for = pdf_path_for or _default_pdf_path
        self._timeout_ms = timeout_ms
        self._visible_timeout_ms = visible_timeout_ms
        self._stall_ms = stall_ms
        self._max_pdf_bytes = max_pdf_bytes
        self._clock = clock
        # A verdict that could not be saved, kept until it is (#480): the
        # visit is never repeated for it. While one is held no entry is taken.
        self._unsaved: tuple[RescueEntry, RescueVerdict] | None = None
        self._save_retry_at = 0.0
        self._save_wait = float(RESCUE_VERDICT_SAVE_RETRY_SECONDS)
        self._save_retry = QTimer(self)
        self._save_retry.setSingleShot(True)
        # Precise: a coarse timer may fire up to 5% early, before the retry
        # is due, and the save would then wait for the next poll
        self._save_retry.setTimerType(Qt.TimerType.PreciseTimer)
        self._save_retry.timeout.connect(self.wake)
        self._profile: QWebEngineProfile | None = None
        self._attempt: RescueAttempt | None = None
        self._busy = False
        self._started = False
        self._stopped = False
        self._poll = QTimer(self)
        self._poll.setInterval(poll_ms)
        self._poll.timeout.connect(self.wake)
        # The window's visit, asked for or under way; while set, the hidden
        # pass takes no entry (#480, stage 3)
        self._shown: _ShownVisit | None = None
        self._shown_token = 0
        # *Walk the queue*, while it runs
        self._walk: _Walk | None = None

    @property
    def busy(self) -> bool:
        """Whether the hidden pass is looking for work or a hidden visit is scheduled or under way.

        Returns:
            ``True`` from :meth:`wake` until no entry is taken, a visit cannot
            start, or a visit's verdict has been recorded or held unsaved.
            A shown visit never sets it.
        """
        return self._busy

    @property
    def holds_unsaved_verdict(self) -> bool:
        """Whether a visit's verdict could not be saved yet (logged; retried).

        While one is held no visit starts, hidden or shown.

        Returns:
            ``True`` until the verdict is saved.
        """
        return self._unsaved is not None

    def start(self) -> None:
        """Begin: look at the queue now, then every poll interval."""
        if self._started or self._stopped:
            return
        self._started = True
        self._poll.start()
        self.wake()

    def wake(self, *_args: object) -> None:
        """Look for work soon, unless the hidden pass is busy already.

        Connected to signals with arguments (an analysis's outcome), which
        are ignored.

        Args:
            *_args: Ignored.
        """
        if not self._started or self._stopped or self._busy:
            return
        self._busy = True
        QTimer.singleShot(0, self, self._tick)

    def shutdown(self) -> None:
        """Stop, for quit.

        A visit under way ends with no verdict, so its entry stays pending. A
        verdict still unsaved gets one last try; if that fails too, its entry
        stays pending and is visited again next session. A rescue that last
        try records is not announced: re-analysing its document would start
        an analysis while the app quits, and the next session's analysis
        finds the PDF, saved already. The page goes before the profile, as
        QtWebEngine requires: pages let go earlier are deleted first, then
        the profile.
        """
        if self._stopped:
            return
        self._stopped = True
        self._shown = None
        self._walk = None
        self._poll.stop()
        self._save_retry.stop()
        if self._unsaved is not None:
            self._save(*self._unsaved, announce=False)
        attempt, self._attempt = self._attempt, None
        if attempt is not None:
            attempt.abort()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        profile, self._profile = self._profile, None
        if profile is not None:
            Shiboken.delete(profile)

    @property
    def shown_entry_id(self) -> int | None:
        """The entry the window has a visit asked for or under way.

        Returns:
            Its ID; ``None`` when the window shows none.
        """
        return self._shown.entry.id if self._shown is not None else None

    def visit_shown(self, entry: RescueEntry, view: QWebEngineView) -> None:
        """Show an entry's page to the reader, in the window's view.

        A person's visit (D2): judged only by what it downloads. It replaces
        any visit the window had. It starts once no other visit is under
        way, at its host's pacing slot.

        Args:
            entry: An open entry.
            view: The window's view.
        """
        if self._walk is not None:
            # The reader picking an entry takes the view over
            self.stop_walk()
        self._ask_shown(entry, view, judge_pages=False)

    @property
    def walking(self) -> bool:
        """Whether *Walk the queue* is running.

        Returns:
            ``True`` from :meth:`walk` until its last entry or :meth:`stop_walk`.
        """
        return self._walk is not None

    def walk(self, entry_ids: Sequence[int], view: QWebEngineView) -> None:
        """Visit each entry in turn in the window's view, unattended.

        Each page is judged as the hidden pass judges it, waiting up to
        ``visible_timeout_ms`` for a PDF: with the window shown this passed
        the Cloudflare publishers 9 times in 9 (spike), so mostly walls that
        need a person's tick are left. Each entry is read again before its
        visit, and one no longer open is skipped; a queue that cannot be
        read stops the walk. A page that cannot open is counted and passed.

        Args:
            entry_ids: The entries, in the order to visit them.
            view: The window's view.
        """
        if self._stopped:
            return
        self._leave_shown()
        self._walk = _Walk(deque(entry_ids), view)
        self._walk_next()

    def stop_walk(self) -> None:
        """Stop *Walk the queue*; the visit under way ends with no verdict."""
        walk, self._walk = self._walk, None
        if walk is None:
            return
        self._leave_shown()
        self.walk_ended.emit(WalkEnd(WalkEndReason.STOPPED, walk.failed))

    def _walk_next(self) -> None:
        """Visit the walk's next open entry, or end the walk."""
        walk = self._walk
        if walk is None:
            return
        end = WalkEndReason.FINISHED
        while walk.ids:
            entry_id = walk.ids.popleft()
            try:
                entry = self._storage.rescue_entry(entry_id)
            except (sqlite3.Error, SQLiteError) as e:
                # Unreadable is not settled: the walk stops and says so
                logger.error("Walled PDF %s could not be read for the walk, which stops: %s", entry_id, e)
                end = WalkEndReason.UNREADABLE
                break
            if entry is None or entry.state not in OPEN_RESCUE_STATES:
                continue
            self._ask_shown(entry, walk.view, judge_pages=True)
            return
        self._walk = None
        self.walk_ended.emit(WalkEnd(end, walk.failed))
        # The window no longer drives the browser: the hidden pass resumes
        self.wake()

    def end_shown_visit(self) -> None:
        """Leave the window's visit with no verdict: its entry stays as it was.

        During a walk this stops the walk: the reader acted on its entry
        (dismissed it, or chose a settled one), and a walk whose visit was
        ended from outside would otherwise wait for a verdict that never comes.
        """
        if self._walk is not None:
            self.stop_walk()
            return
        self._leave_shown()

    def keep_shown_pdf(self) -> HeldPdfKeep:
        """Keep the PDF the window's visit holds, as the article's (#532).

        The person has looked at a PDF that does not carry the title and
        says it is the article. Kept, the visit ends as a rescue; one that
        cannot be saved stays held, and the visit goes on.

        Returns:
            What came of it; ``NOTHING_HELD`` when there is no visit, or it
            has not started.
        """
        shown = self._shown
        if shown is None or shown.attempt is None:
            return HeldPdfKeep.NOTHING_HELD
        return shown.attempt.keep_held_pdf()

    def _leave_shown(self) -> None:
        """Leave the window's visit with no verdict, whether or not a walk runs."""
        shown, self._shown = self._shown, None
        if shown is None:
            return
        attempt = shown.attempt
        if attempt is not None:
            if self._attempt is attempt:
                self._attempt = None
            attempt.abort()
            attempt.deleteLater()
        self.shown_visit_ended.emit(shown.entry, None)
        self.wake()

    def _ask_shown(self, entry: RescueEntry, view: QWebEngineView, *, judge_pages: bool) -> None:
        """Replace the window's visit with one of ``entry``.

        Args:
            entry: The entry.
            view: The window's view.
            judge_pages: Whether its page is judged (a walk).
        """
        if self._stopped:
            return
        self._leave_shown()
        self._shown_token += 1
        self._shown = _ShownVisit(entry, view, judge_pages, self._shown_token)
        self._start_shown_when_free()

    def _start_shown_when_free(self) -> None:
        """Schedule the window's visit, once no visit is under way and no verdict waits to be saved."""
        shown = self._shown
        if shown is None or shown.scheduled or self._stopped:
            return
        if self._attempt is not None or self._unsaved is not None:
            return
        shown.scheduled = True
        token = shown.token
        try:
            delay = rescue_delay_ms(shown.entry.request.host)
        except Exception:
            # Never unpaced, least of all in an unattended walk
            logger.exception("No pacing slot for %s; its page is not opened", shown.entry.request.host)
            self._shown_failed(shown)
            return
        QTimer.singleShot(delay, self, lambda: self._begin_shown(token))

    def _begin_shown(self, token: int) -> None:
        """Start the window's visit, unless the reader has moved on or it settled meanwhile.

        The entry is read again first: the visit may have waited behind a
        hidden visit, an unsaved verdict, or its host's pacing slot, and one
        settled meanwhile (rescued, dismissed) is not shown. One that cannot
        be read is shown as last read: the reader asked for it, and a
        verdict never overwrites a settled entry. A visit that cannot start
        is logged and reported as failed, with no verdict: a person's visit
        counts no attempt.

        Args:
            token: Which request scheduled this start.
        """
        shown = self._shown
        if self._stopped or shown is None or shown.token != token or self._attempt is not None:
            return
        try:
            current = self._storage.rescue_entry(shown.entry.id)
        except (sqlite3.Error, SQLiteError) as e:
            logger.error("Walled PDF %s could not be read before its page opened: %s", shown.entry.id, e)
            current = shown.entry
        if current is None or current.state not in OPEN_RESCUE_STATES:
            logger.info("Walled PDF %s was settled before its page opened; not shown", shown.entry.id)
            self._shown = None
            self.shown_visit_ended.emit(current or shown.entry, None)
            self._after_shown()
            return
        shown.entry = current
        try:
            attempt = RescueAttempt(
                self._ensure_profile(), current.request.address, self._pdf_path_for(current),
                self._visible_timeout_ms, self._stall_ms, self._max_pdf_bytes, parent=self,
                title=current.request.title, view=shown.view, judge_pages=shown.judge_pages,
            )
            attempt.finished.connect(lambda verdict: self._on_shown_finished(token, verdict))
            attempt.download_started.connect(
                lambda: self._relay_shown(token, self.shown_download_started)
            )
            attempt.download_ignored.connect(lambda: self._on_shown_download_ignored(token))
            attempt.download_mismatched.connect(lambda: self._on_shown_download_mismatched(token))
            attempt.download_unreadable.connect(
                lambda: self._relay_shown(token, self.shown_download_unreadable)
            )
            shown.attempt = attempt
            self._attempt = attempt
            logger.info("Showing a walled PDF at %s in the review queue", current.request.host)
            attempt.start()
        except Exception:
            logger.exception("The review queue could not open the page at %s", current.request.host)
            self._shown_failed(shown)
            return
        self.shown_visit_started.emit(current)

    def _shown_failed(self, shown: _ShownVisit) -> None:
        """The window's visit could not start: say so, and let a walk go on.

        Args:
            shown: The visit.
        """
        self._leave_shown()
        self.shown_visit_failed.emit(shown.entry)
        if self._walk is not None:
            # A walk goes on past a page that could not open, and counts it
            self._walk.failed += 1
            self._walk_next()

    def _after_shown(self) -> None:
        """A shown visit is over: the walk's next entry, or the hidden pass."""
        if self._walk is not None:
            self._walk_next()
        else:
            self.wake()

    def _relay_shown(self, token: int, signal: SignalInstance) -> None:
        """Say what a person's visit met, with its entry, if it is still the window's.

        Args:
            token: Which request the visit was for.
            signal: The service's signal to emit.
        """
        shown = self._shown
        if shown is not None and shown.token == token:
            signal.emit(shown.entry)

    def _on_shown_download_ignored(self, token: int) -> None:
        """A person's download was no PDF; say so, if the visit is still the window's.

        Args:
            token: Which request the visit was for.
        """
        self._relay_shown(token, self.shown_download_ignored)

    def _on_shown_download_mismatched(self, token: int) -> None:
        """A person's PDF does not carry the title; say so, if the visit is still the window's.

        Args:
            token: Which request the visit was for.
        """
        self._relay_shown(token, self.shown_download_mismatched)

    def _on_shown_finished(self, token: int, verdict: RescueVerdict) -> None:
        """Record the window's visit's verdict, and say it ended.

        Args:
            token: Which request the visit was for.
            verdict: What it concluded.
        """
        shown = self._shown
        if shown is None or shown.token != token:
            return
        self._shown = None
        attempt = shown.attempt
        if attempt is not None:
            if self._attempt is attempt:
                self._attempt = None
            attempt.deleteLater()
        logger.info("The review queue's visit to %s: %s", shown.entry.request.host, verdict.outcome.value)
        try:
            self._save(shown.entry, verdict)
        finally:
            # The window reads holds_unsaved_verdict to tell a saved verdict
            # from one still waiting to be
            self.shown_visit_ended.emit(shown.entry, verdict)
            self._after_shown()

    def _tick(self) -> None:
        """Take the next due entry and schedule its visit for its host's slot.

        An unforeseen error is logged and the pass freed for the next poll:
        escaping this slot would leave it busy, and stopped, for the session
        (#525).
        """
        if self._stopped:
            return
        scheduled = False
        try:
            entry = self._next_entry()
            if entry is not None:
                delay = rescue_delay_ms(entry.request.host)
                QTimer.singleShot(delay, self, lambda: self._visit(entry))
                scheduled = True
        except Exception:
            logger.exception("The hidden browser could not look for a walled PDF to try")
        finally:
            if not scheduled:
                self._busy = False
        # A shown visit held back by an unsaved verdict starts once it is saved
        self._start_shown_when_free()

    def _next_entry(self) -> RescueEntry | None:
        """The next due entry, when the hidden pass is on.

        A verdict that could not be saved is saved first, when its retry is
        due, whatever the setting: it is bookkeeping for a visit already
        made. While it stays unsaved no entry is taken, as the next write
        would most likely fail too.

        Returns:
            The entry; ``None`` when off, when none is due, while a verdict
            is unsaved, or when the queue cannot be read (logged; the next
            poll tries again).
        """
        if self._unsaved is not None:
            if self._clock() < self._save_retry_at or not self._save(*self._unsaved):
                return None
        if self._shown is not None:
            # The window drives the browser: the hidden pass pauses
            return None
        if not self._config.discovery.browser_rescue_hidden:
            return None
        try:
            return self._storage.next_rescue_entry()
        except (sqlite3.Error, SQLiteError) as e:
            logger.error("The browser rescue queue could not be read: %s", e)
            return None

    def _visit(self, entry: RescueEntry) -> None:
        """Start one visit, if the hidden pass is still on and the entry still pending.

        The setting and the entry are read again here: the visit waited for
        its host's pacing slot, which a throttling host can push minutes
        away, and a reader who switched the pass off, or set the entry
        aside, meanwhile is not overridden. A
        visit that cannot start for want of a folder learnt nothing, so it is
        recorded as a transport failure: counted, and held back, rather than
        tried again at every poll. Any other error is logged and the pass
        freed for the next poll (#525).

        Args:
            entry: The entry to visit.
        """
        if self._stopped:
            return
        started = False
        # Only the attempt this call makes is its to free: a shown visit may
        # have started while this one waited for its slot
        attempt: RescueAttempt | None = None
        try:
            if self._shown is not None or not self._config.discovery.browser_rescue_hidden:
                # The window asked for a visit meanwhile, or the pass was switched off
                return
            # Read again: the window may have dismissed it, or rescued another
            # address of its document, while it waited for its slot. A
            # visit then could replace a PDF already rescued and analysed.
            current = self._storage.rescue_entry(entry.id)
            if current is None or current.state is not RescueState.PENDING:
                logger.info("Walled PDF %s was settled while it waited for its slot; not visited", entry.id)
                return
            entry = current
            try:
                profile = self._ensure_profile()
                pdf_path = self._pdf_path_for(entry)
            except OSError as e:
                logger.error("The hidden browser cannot start a visit: %s", e)
                self._save(entry, RescueVerdict(RescueOutcome.TRANSPORT))
                return
            attempt = RescueAttempt(
                profile, entry.request.address, pdf_path,
                self._timeout_ms, self._stall_ms, self._max_pdf_bytes, parent=self,
                title=entry.request.title,
            )
            attempt.finished.connect(lambda verdict: self._on_finished(entry, verdict))
            self._attempt = attempt
            logger.info("Trying a walled PDF at %s in the hidden browser", entry.request.host)
            attempt.start()
            started = True
        except Exception:
            logger.exception("The hidden browser could not start a visit to %s", entry.request.host)
        finally:
            if not started:
                # No visit under way: let the next poll look again, whatever
                # stopped this one
                if attempt is not None:
                    if self._attempt is attempt:
                        self._attempt = None
                    attempt.abort()
                    attempt.deleteLater()
                self._busy = False

    def _ensure_profile(self) -> QWebEngineProfile:
        """The profile, made on the first visit.

        Returns:
            The profile.

        Raises:
            OSError: If its directory cannot be made private.
        """
        if self._profile is None:
            profile = rescue_profile(self._profile_dir)
            profile.downloadRequested.connect(self._on_download_requested)
            self._profile = profile
        return self._profile

    def _on_download_requested(self, request: QWebEngineDownloadRequest) -> None:
        """Hand a download to the visit under way; refuse one with no visit.

        Args:
            request: The download.
        """
        if self._attempt is None:
            request.cancel()
            return
        self._attempt.accept_download(request)

    def _on_finished(self, entry: RescueEntry, verdict: RescueVerdict) -> None:
        """Record a visit's verdict, announce a rescue, and look for more.

        Args:
            entry: The entry visited.
            verdict: What the visit concluded.
        """
        attempt, self._attempt = self._attempt, None
        if attempt is not None:
            attempt.deleteLater()
        logger.info("The hidden browser's visit to %s: %s", entry.request.host, verdict.outcome.value)
        try:
            saved = self._save(entry, verdict)
        finally:
            # Whatever announcing a rescue raised, the pass is not left busy
            self._busy = False
        self._start_shown_when_free()
        if saved:
            self.wake()

    def _save(self, entry: RescueEntry, verdict: RescueVerdict, announce: bool = True) -> bool:
        """Record a visit's verdict, and announce a rescue.

        A verdict that cannot be recorded is kept and tried again, never the
        visit: the entry is still pending in the queue, and visiting it
        again would ask the publisher twice (and download a rescued PDF
        again). The retry waits RESCUE_VERDICT_SAVE_RETRY_SECONDS, doubling
        with each further failure up to RESCUE_RETRY_DELAY_SECONDS; a save
        resets the wait. Any error writing it is such a failure: the write
        is one call, and whatever it raised, the verdict is not recorded. A
        rescue is announced only when this verdict made it: one another
        process recorded first was announced there.

        Args:
            entry: The entry visited.
            verdict: What the visit concluded.
            announce: Whether a rescue recorded now is announced (not at quit).

        Returns:
            Whether the verdict was recorded.
        """
        try:
            recorded = self._storage.record_rescue_verdict(entry.id, verdict)
        except Exception as e:
            # The PDF, if any, is saved already; only the bookkeeping waits
            wait = self._save_wait
            self._unsaved = (entry, verdict)
            self._save_retry_at = self._clock() + wait
            self._save_wait = min(wait * 2, float(RESCUE_RETRY_DELAY_SECONDS))
            if self._stopped:
                logger.error(
                    "The verdict for a walled PDF at %s could not be recorded (%s: %s); "
                    "its entry stays pending and is visited again next session",
                    entry.request.host, type(e).__name__, e,
                )
            else:
                logger.error(
                    "The verdict for a walled PDF at %s could not be recorded (%s: %s); "
                    "trying again in %.0f seconds",
                    entry.request.host, type(e).__name__, e, wait,
                )
                self._save_retry.start(int(wait * MS_PER_SECOND))
            return False
        self._unsaved = None
        self._save_wait = float(RESCUE_VERDICT_SAVE_RETRY_SECONDS)
        updated = recorded.entry
        if recorded.applied and updated is not None:
            self.entry_changed.emit(updated)
        if (
            announce
            and recorded.applied
            and updated is not None
            and updated.state is RescueState.RESCUED
        ):
            self.rescued.emit(updated)
        return True
