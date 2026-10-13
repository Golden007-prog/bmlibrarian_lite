"""The browser rescue's hidden pass: works the queue on the GUI thread (#480, stage 2).

One visit at a time, unattended, in a page with no view. Driven by signals
and timers only: no thread waits on it, and a run never waits for it (spec
decisions 1 and 4). Each visit takes its host's pacing slot first, from the
limiter our requests use. A rescue is announced with ``rescued`` and the
main window re-analyses that document.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, Signal
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEngineProfile
from shiboken6 import Shiboken

from ..browser_rescue import (
    RescueEntry,
    RescueState,
    RescueVerdict,
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
)
from ..exceptions import SQLiteError
from .rescue_attempt import RescueAttempt, rescue_profile

if TYPE_CHECKING:
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


class BrowserRescueService(QObject):
    """Visits queued walled PDFs one at a time, hidden and unattended.

    Signals:
        rescued: A :class:`RescueEntry` whose PDF arrived and was saved.
    """

    rescued = Signal(object)

    def __init__(
        self,
        storage: LiteStorage,
        config: LiteConfig,
        profile_dir: Path,
        *,
        pdf_path_for: Callable[[RescueEntry], Path] | None = None,
        timeout_ms: int = RESCUE_HIDDEN_TIMEOUT_MS,
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
                before every visit.
            profile_dir: The browser's persistent profile.
            pdf_path_for: Where an entry's PDF is saved; the reader's library
                by default.
            timeout_ms: How long a visit waits for a download.
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
        self._save_retry.timeout.connect(self.wake)
        self._profile: QWebEngineProfile | None = None
        self._attempt: RescueAttempt | None = None
        self._busy = False
        self._started = False
        self._stopped = False
        self._poll = QTimer(self)
        self._poll.setInterval(poll_ms)
        self._poll.timeout.connect(self.wake)

    @property
    def busy(self) -> bool:
        """Whether a visit is scheduled or under way.

        Returns:
            ``True`` from taking an entry until its verdict is recorded.
        """
        return self._busy

    def start(self) -> None:
        """Begin: look at the queue now, then every poll interval."""
        if self._started or self._stopped:
            return
        self._started = True
        self._poll.start()
        self.wake()

    def wake(self, *_args: object) -> None:
        """Look for work soon, unless a visit is under way.

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
        stays pending and is visited again next session. The page goes before
        the profile, as QtWebEngine requires: pages let go earlier are
        deleted first, then the profile.
        """
        if self._stopped:
            return
        self._stopped = True
        self._poll.stop()
        self._save_retry.stop()
        if self._unsaved is not None:
            self._save(*self._unsaved)
        attempt, self._attempt = self._attempt, None
        if attempt is not None:
            attempt.abort()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        profile, self._profile = self._profile, None
        if profile is not None:
            Shiboken.delete(profile)

    def _tick(self) -> None:
        """Take the next due entry and schedule its visit for its host's slot."""
        if self._stopped:
            return
        entry = self._next_entry()
        if entry is None:
            self._busy = False
            return
        delay = rescue_delay_ms(entry.request.host)
        QTimer.singleShot(delay, self, lambda: self._visit(entry))

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
        if not self._config.discovery.browser_rescue_hidden:
            return None
        try:
            return self._storage.next_rescue_entry()
        except (sqlite3.Error, SQLiteError) as e:
            logger.error("The browser rescue queue could not be read: %s", e)
            return None

    def _visit(self, entry: RescueEntry) -> None:
        """Start one visit.

        Args:
            entry: The entry to visit.
        """
        if self._stopped:
            return
        try:
            profile = self._ensure_profile()
            pdf_path = self._pdf_path_for(entry)
        except OSError as e:
            logger.error("The hidden browser cannot start a visit: %s", e)
            self._busy = False
            return
        attempt = RescueAttempt(
            profile, entry.request.address, pdf_path,
            self._timeout_ms, self._stall_ms, self._max_pdf_bytes, parent=self,
        )
        attempt.finished.connect(lambda verdict: self._on_finished(entry, verdict))
        self._attempt = attempt
        logger.info("Trying a walled PDF at %s in the hidden browser", entry.request.host)
        attempt.start()

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
        saved = self._save(entry, verdict)
        self._busy = False
        if saved:
            self.wake()

    def _save(self, entry: RescueEntry, verdict: RescueVerdict) -> bool:
        """Record a visit's verdict, and announce a rescue.

        A verdict that cannot be recorded is kept and tried again, never the
        visit: the entry is still pending in the queue, and visiting it
        again would ask the publisher twice (and download a rescued PDF
        again). The retry waits RESCUE_VERDICT_SAVE_RETRY_SECONDS, doubling
        with each further failure up to RESCUE_RETRY_DELAY_SECONDS; a save
        resets the wait.

        Args:
            entry: The entry visited.
            verdict: What the visit concluded.

        Returns:
            Whether the verdict was recorded.
        """
        try:
            updated = self._storage.record_rescue_verdict(entry.id, verdict)
        except (sqlite3.Error, SQLiteError) as e:
            # The PDF, if any, is saved already; only the bookkeeping waits
            wait = self._save_wait
            self._unsaved = (entry, verdict)
            self._save_retry_at = self._clock() + wait
            self._save_wait = min(wait * 2, float(RESCUE_RETRY_DELAY_SECONDS))
            logger.error(
                "The verdict for a walled PDF at %s could not be recorded (%s); "
                "trying again in %.0f seconds",
                entry.request.host, e, wait,
            )
            if not self._stopped:
                self._save_retry.start(int(wait * MS_PER_SECOND))
            return False
        self._unsaved = None
        self._save_wait = float(RESCUE_VERDICT_SAVE_RETRY_SECONDS)
        if updated is not None and updated.state is RescueState.RESCUED:
            self.rescued.emit(updated)
        return True
