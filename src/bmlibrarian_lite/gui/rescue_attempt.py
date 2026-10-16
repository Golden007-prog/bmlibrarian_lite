"""One visit of one walled PDF's address: hidden, or shown in the review queue window (#480, stages 2 and 3).

Driven only by signals and timers, so nothing here blocks the GUI thread.
The page is QtWebEngine's own, with its own User-Agent and no automation
protocol (no impersonation, decision 5 of
``docs/superpowers/specs/2026-10-12-browser-rescue-design.md``). The
profile's PDF viewer is off, so a PDF arrives as a download. It is written
as a partial file beside ``pdf_path`` in the PDF library, and renamed into
place only once it is whole, within the size limit, begins with ``%PDF``,
opens, and is not found to lack the article's title on its first page with
text (#532).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEngineLoadingInfo,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from shiboken6 import Shiboken

from ..browser_rescue import (
    HeldPdfKeep,
    RescueOutcome,
    RescueVerdict,
    download_verdict,
    http_status_or_none,
    page_verdict,
    penalise_host_for_answer,
    title_checked_verdict,
)
from ..constants import (
    BROWSER_PROFILE_CACHE_DIRNAME,
    BROWSER_PROFILE_STORAGE_NAME,
    CHALLENGE_BODY_PREFIX_BYTES,
    CHALLENGE_GONE_STATUSES,
    CONFIG_DIR_PERMISSIONS,
    RESCUE_PAGE_READ_GRACE_MS,
)
from ..pdf_download import discard_partial_download, held_download_path, partial_download_path
from ..pdf_library_check import mark_kept
from ..pdf_title_check import check_pdf_title

if TYPE_CHECKING:
    # Type only: the hidden pass never loads the widgets module
    from PySide6.QtWebEngineWidgets import QWebEngineView

logger = logging.getLogger(__name__)

_Domain = QWebEngineLoadingInfo.ErrorDomain
_LoadStatus = QWebEngineLoadingInfo.LoadStatus
_DownloadState = QWebEngineDownloadRequest.DownloadState

#: Load failures that are the network's: the visit learnt nothing.
_TRANSPORT_DOMAINS = frozenset(
    {
        _Domain.ConnectionErrorDomain,
        _Domain.DnsErrorDomain,
        _Domain.CertificateErrorDomain,
        _Domain.HttpErrorDomain,
        _Domain.FtpErrorDomain,
    }
)

#: Chromium's ERR_ABORTED, which a script-driven navigation produces: not a failure.
_ERR_ABORTED = -3

#: How header bytes become text for the challenge rules; no byte is lost.
_HEADER_ENCODING = "latin-1"


def rescue_profile(profile_dir: Path) -> QWebEngineProfile:
    """The browser's profile, configured as the spike that passed the walls.

    Persistent and owner-only, because its cookies are the reader's
    clearances and sessions with publishers. QtWebEngine's own User-Agent;
    the PDF viewer off, so a PDF arrives through ``downloadRequested``.
    Parentless: the caller connects ``downloadRequested`` and deletes the
    profile after every page made with it.

    Args:
        profile_dir: Where the profile lives.

    Returns:
        The profile.

    Raises:
        OSError: If the directory cannot be made private.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(profile_dir, CONFIG_DIR_PERMISSIONS)
    profile = QWebEngineProfile(BROWSER_PROFILE_STORAGE_NAME)
    profile.setPersistentStoragePath(str(profile_dir))
    profile.setCachePath(str(profile_dir / BROWSER_PROFILE_CACHE_DIRNAME))
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    )
    profile.settings().setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)
    return profile


def _header_text(raw: Any) -> str:
    """One header name or value, from a QByteArray or plain bytes."""
    data = raw.data() if hasattr(raw, "data") else raw
    return bytes(data).decode(_HEADER_ENCODING)


def response_headers(raw: Mapping[Any, Any]) -> dict[str, str]:
    """A loading info's response headers, as the challenge rules take them.

    Args:
        raw: QtWebEngine's multimap, ``{QByteArray: [QByteArray, ...]}``.

    Returns:
        Each name with its values joined as HTTP joins a repeated header.
    """
    return {
        _header_text(name): ", ".join(_header_text(value) for value in values)
        for name, values in raw.items()
    }


class _ShownPage(QWebEnginePage):
    """The page of a visit a person sees.

    A link that opens a new window (``target=_blank``, the usual "Download
    PDF") opens here instead: QtWebEngine would otherwise drop it, and a PDF
    it leads to must be this visit's own download (``accept_download``).
    Scripts still cannot open windows without the person's click
    (``JavascriptCanOpenWindows`` stays off).
    """

    def createWindow(self, _type: QWebEnginePage.WebWindowType) -> QWebEnginePage:  # noqa: N802
        """Open the new window's page in this one.

        Args:
            _type: The kind of window asked for; every kind opens here.

        Returns:
            This page.
        """
        return self


class RescueAttempt(QObject):
    """One visit of one address, ending in exactly one verdict.

    A hidden visit (no view) judges the page it stays on. A person's visit
    (``judge_pages=False``, in the review queue window's view) ends only
    with a download or a renderer crash; leaving it is :meth:`abort`. A file
    a person downloads that is neither a PDF nor a wall (a citation export,
    a supplement) is not the article's answer: it is discarded, said with
    ``download_ignored``, and the page stays open for the PDF.

    A PDF whose first page with text does not carry the title is not kept
    (#532). A judged visit ends with ``WRONG_PDF``. A person's is held, said
    with ``download_mismatched``, and the page stays open:
    :meth:`keep_held_pdf` keeps it (the check misreads scans with garbled
    text and retitled preprints), and the next download, leaving or the
    visit's end discards it. A PDF that cannot be opened ends a judged visit
    with ``UNREADABLE_PDF``; a person's is discarded, said with
    ``download_unreadable``, and the page stays open.

    Signals:
        finished: The visit's :class:`RescueVerdict`, emitted once, and never
            after :meth:`abort`.
        download_started: A person's download began; a PDF held for them
            is gone.
        download_ignored: A person's download was not a PDF; the visit goes on.
        download_mismatched: A person's PDF does not carry the title; it is
            held, and the visit goes on.
        download_unreadable: A person's PDF could not be opened; the visit
            goes on.
    """

    finished = Signal(object)
    download_started = Signal()
    download_ignored = Signal()
    download_mismatched = Signal()
    download_unreadable = Signal()

    def __init__(
        self,
        profile: QWebEngineProfile,
        address: str,
        pdf_path: Path,
        timeout_ms: int,
        stall_ms: int,
        max_pdf_bytes: int,
        parent: QObject | None = None,
        *,
        title: str,
        view: QWebEngineView | None = None,
        judge_pages: bool = True,
    ) -> None:
        """Prepare the visit; :meth:`start` makes it.

        Args:
            profile: The rescue profile (:func:`rescue_profile`).
            address: The PDF's address.
            pdf_path: Where a PDF that arrives is saved.
            title: The article's title, which a PDF's first page must
                carry; a title with no words checks nothing.
            timeout_ms: How long to wait for a download before reading the page.
            stall_ms: How long a started download may receive nothing.
            max_pdf_bytes: The largest PDF kept.
            parent: The owning object.
            view: The review queue window's view to show the page in;
                ``None`` for a hidden visit.
            judge_pages: Whether the page the visit stays on is judged after
                ``timeout_ms`` (hidden, and a walk); ``False`` for a person's
                visit, which only a download or a renderer crash ends.
        """
        super().__init__(parent)
        self._view = view
        self._judge_pages = judge_pages
        self._title = title
        self._profile = profile
        self._address = address
        self._pdf_path = pdf_path
        self._timeout_ms = timeout_ms
        self._max_pdf_bytes = max_pdf_bytes
        self._page: QWebEnginePage | None = None
        self._download: QWebEngineDownloadRequest | None = None
        # A person's PDF that does not carry the title, moved beside the PDF
        # path until they keep it or move on (#532)
        self._held: Path | None = None
        self._status: int | None = None
        self._headers: dict[str, str] = {}
        self._too_large = False
        self._reading_page = False
        # A main-frame navigation has started and not yet ended: the page in
        # view is the one it is leaving
        self._navigating = False
        self._done = False
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)
        self._stall = QTimer(self)
        self._stall.setSingleShot(True)
        self._stall.setInterval(stall_ms)
        self._stall.timeout.connect(self._on_stall)

    def start(self) -> None:
        """Load the address, in the view when there is one, and start waiting."""
        # Parentless, so it is deleted when this visit says, always before
        # the profile, as QtWebEngine requires (_release_page). A view
        # showing it falls back to a page of its own when it goes.
        page = QWebEnginePage(self._profile) if self._view is None else _ShownPage(self._profile)
        page.setAudioMuted(self._view is None)
        page.loadingChanged.connect(self._on_loading_changed)
        page.renderProcessTerminated.connect(self._on_render_process_terminated)
        self._page = page
        if self._view is not None:
            self._view.setPage(page)
        if self._judge_pages:
            self._timeout.start(self._timeout_ms)
        page.load(QUrl(self._address))

    def accept_download(self, request: QWebEngineDownloadRequest) -> None:
        """Take the download this visit started, into the partial file.

        Only one this visit's own page started: the profile is shared, and a
        page an earlier visit let go of may still start one, which is
        another article's PDF. Only one at a time: a page that starts a
        second while the first runs is not believed twice (a person's visit
        takes another once a file that was no PDF is discarded). A size
        announced over the limit is refused before a byte is written.

        Args:
            request: The profile's download request.
        """
        if (
            self._done
            or self._download is not None
            or self._page is None
            or request.page() is not self._page
        ):
            request.cancel()
            return
        self._timeout.stop()
        self._download = request
        if request.totalBytes() > self._max_pdf_bytes:
            request.cancel()
            self._finish(RescueVerdict(RescueOutcome.TOO_LARGE))
            return
        partial = partial_download_path(self._pdf_path)
        # The next download is the person's answer: a PDF held for them goes
        self._drop_held()
        try:
            partial.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error("No folder for the browser's download of %s: %s", self._pdf_path.name, e)
            request.cancel()
            self._finish(RescueVerdict(RescueOutcome.TRANSPORT))
            return
        discard_partial_download(partial)
        request.setDownloadDirectory(str(partial.parent))
        request.setDownloadFileName(partial.name)
        request.receivedBytesChanged.connect(self._on_progress)
        request.isFinishedChanged.connect(self._on_download_finished)
        request.accept()
        self._stall.start()
        if not self._judge_pages:
            self.download_started.emit()

    def abort(self) -> None:
        """End the visit with no verdict (quit, or the reader leaving a shown visit): its entry stays as it was."""
        if self._done:
            return
        self._done = True
        self._timeout.stop()
        self._stall.stop()
        if self._download is not None and not self._download.isFinished():
            self._download.cancel()
            discard_partial_download(partial_download_path(self._pdf_path))
        self._drop_held()
        self._release_page(at_once=True)

    def keep_held_pdf(self) -> HeldPdfKeep:
        """Keep the PDF held for the person, which does not carry the title, as the article's.

        The person has seen it and says it is the article: the check misreads
        scans with garbled text and preprints retitled between versions.

        Returns:
            ``KEPT`` when it was renamed into place, and the visit ended with
            ``PDF``; ``NOTHING_HELD`` when none was held; ``NOT_SAVED`` when
            it could not be renamed (logged): it is still held, and the
            visit goes on, so the person's choice is not lost.
        """
        held = self._held
        if held is None:
            # Nothing held: none arrived, a newer download replaced it, or
            # the visit is over (it drops one)
            return HeldPdfKeep.NOTHING_HELD
        try:
            # Marked first: kept without its marker, the cached-PDF tier
            # would set it aside as another article's (#541). A marker left
            # by a failed rename names these bytes alone, so it vouches for
            # no other PDF.
            mark_kept(self._pdf_path, contents=held)
            held.replace(self._pdf_path)
        except OSError as e:
            logger.error("The PDF kept for %s could not be saved: %s", self._pdf_path.name, e)
            return HeldPdfKeep.NOT_SAVED
        self._held = None
        logger.info(
            "Kept a PDF from %s that does not carry its article's title, as the reader asked",
            self._address,
        )
        self._finish(RescueVerdict(RescueOutcome.PDF))
        return HeldPdfKeep.KEPT

    def _drop_held(self) -> None:
        """Discard the PDF held for the person, if any."""
        held, self._held = self._held, None
        if held is not None:
            discard_partial_download(held)

    def _on_loading_changed(self, info: QWebEngineLoadingInfo) -> None:
        """Note each main-frame answer; end the visit on a gone or a network failure.

        Args:
            info: The load's state.
        """
        if self._done or info.isDownload():
            return
        if self._download is not None:
            # A download is under way: what the page does now is not the answer
            return
        if info.status() == _LoadStatus.LoadStartedStatus:
            self._navigating = True
            return
        self._navigating = False
        domain = info.errorDomain()
        if domain == _Domain.HttpStatusCodeDomain:
            # Every main-frame answer reports its status here, a 200 included.
            # A code that is no HTTP status is as good as none: nothing learnt
            self._status = http_status_or_none(info.errorCode())
            self._headers = response_headers(info.responseHeaders())
            penalise_host_for_answer(self._address, self._status, self._headers)
            if self._judge_pages and self._status in CHALLENGE_GONE_STATUSES:
                self._finish(page_verdict(self._status, self._headers, ""))
            return
        if not self._judge_pages:
            # A person's page: shown, never settled (D2)
            return
        if info.status() == _LoadStatus.LoadFailedStatus and (
            domain in _TRANSPORT_DOMAINS
            or (domain == _Domain.InternalErrorDomain and info.errorCode() != _ERR_ABORTED)
        ):
            self._finish(RescueVerdict(RescueOutcome.TRANSPORT, self._status))

    def _on_render_process_terminated(self, *_args: object) -> None:
        """The page's renderer died: nothing was learnt."""
        self._finish(RescueVerdict(RescueOutcome.TRANSPORT, self._status))

    def _on_timeout(self) -> None:
        """No download began in time: read the page the visit stayed on."""
        if self._done or self._download is not None or self._page is None or not self._judge_pages:
            return
        if self._reading_page:
            # The page never handed its HTML over: nothing was learnt
            self._finish(RescueVerdict(RescueOutcome.TRANSPORT, self._status))
            return
        self._reading_page = True
        self._timeout.start(RESCUE_PAGE_READ_GRACE_MS)
        self._page.toHtml(self._on_html)

    def _on_html(self, html: str) -> None:
        """Judge the page by its serialised DOM.

        With no status, no server ever answered and the page read is the
        empty initial document (``page_verdict``: transport). While a
        navigation the page started is still open, the page read is the one
        it is leaving, which says nothing of where it goes: that is no
        ``NO_PDF`` either, but a transport failure. A challenge it shows
        still waits for a person, an open state.

        Args:
            html: The page's HTML.
        """
        if self._done or self._download is not None:
            return
        verdict = page_verdict(self._status, self._headers, html)
        if self._navigating and verdict.outcome is RescueOutcome.NO_PDF:
            verdict = RescueVerdict(RescueOutcome.TRANSPORT, self._status)
        self._finish(verdict)

    def _on_progress(self) -> None:
        """A download moved: refuse it past the limit, else wait on."""
        if self._done or self._download is None:
            return
        if self._download.receivedBytes() > self._max_pdf_bytes:
            self._too_large = True
            self._download.cancel()
            return
        self._stall.start()

    def _on_stall(self) -> None:
        """A download received nothing for too long: give it up."""
        if not self._done and self._download is not None:
            self._download.cancel()

    def _on_download_finished(self) -> None:
        """Keep a whole PDF; anything else ends the visit without one.

        A person's visit goes on past a file that is no PDF, a PDF that
        does not carry the title (held) and one that cannot be opened.
        """
        request = self._download
        if self._done or request is None or not request.isFinished():
            return
        written = Path(request.downloadDirectory()) / request.downloadFileName()
        if request.state() != _DownloadState.DownloadCompleted:
            discard_partial_download(written)
            outcome = RescueOutcome.TOO_LARGE if self._too_large else RescueOutcome.TRANSPORT
            self._finish(RescueVerdict(outcome))
            return
        verdict = self._settle(written)
        if not self._judge_pages and verdict.outcome is RescueOutcome.NO_PDF:
            # A person's visit (D2): a file that is no PDF is not the
            # article's answer, and settling on it would close the entry for
            # good. The page stays open, and another download is taken.
            logger.info("A download from %s was no PDF; the page stays open", self._address)
            self._stall.stop()
            self._download = None
            self.download_ignored.emit()
            return
        if not self._judge_pages and verdict.outcome is RescueOutcome.WRONG_PDF:
            # A person's PDF that does not carry the title: theirs to keep
            # or to replace with the article's own (#532)
            logger.info("A PDF from %s does not carry its article's title; held for the reader", self._address)
            self._stall.stop()
            self._download = None
            self.download_mismatched.emit()
            return
        if not self._judge_pages and verdict.outcome is RescueOutcome.UNREADABLE_PDF:
            # A person's PDF that cannot be opened: the download worked, and
            # another copy on the page may open (#532)
            logger.info("A PDF from %s could not be opened; the page stays open", self._address)
            self._stall.stop()
            self._download = None
            self.download_unreadable.emit()
            return
        self._finish(verdict)

    def _settle(self, written: Path) -> RescueVerdict:
        """Rename a finished download into place if it is the article's PDF, within the limit.

        A PDF whose first page with text does not carry the title is not
        renamed into place: a judged visit discards it (``WRONG_PDF``), and a
        person's is moved beside it, held for them (``_held``). One that
        cannot be opened is discarded (``UNREADABLE_PDF``).

        Args:
            written: Where QtWebEngine wrote it.

        Returns:
            The verdict; the partial file is gone either way.
        """
        try:
            if written.stat().st_size > self._max_pdf_bytes:
                return RescueVerdict(RescueOutcome.TOO_LARGE)
            with open(written, "rb") as f:
                # A bounded read of what was served, for the challenge rules;
                # a PDF is kept whole by the rename below
                verdict = download_verdict(f.read(CHALLENGE_BODY_PREFIX_BYTES))
            if verdict.outcome is not RescueOutcome.PDF:
                return verdict
            check = check_pdf_title(written, self._title)
            # The share and the outcome, so a PDF refused, or kept unchecked,
            # can be explained later
            logger.info(
                "The title check of a PDF from %s: %s (share %s)",
                self._address, check.outcome.value, check.share,
            )
            verdict = title_checked_verdict(check)
            if verdict.outcome is RescueOutcome.PDF:
                written.replace(self._pdf_path)
            elif verdict.outcome is RescueOutcome.WRONG_PDF and not self._judge_pages:
                # Out of the partial file's way: discovery writes it too
                held = held_download_path(self._pdf_path)
                written.replace(held)
                self._held = held
            return verdict
        except OSError as e:
            logger.error("The browser's download of %s could not be kept: %s", self._pdf_path.name, e)
            return RescueVerdict(RescueOutcome.TRANSPORT)
        finally:
            discard_partial_download(written)

    def _finish(self, verdict: RescueVerdict) -> None:
        """End the visit with its one verdict.

        Args:
            verdict: What it concluded.
        """
        if self._done:
            return
        self._done = True
        self._timeout.stop()
        self._stall.stop()
        download = self._download
        if download is not None and not download.isFinished():
            # A visit that ends mid-download must not leave its partial file
            download.cancel()
            discard_partial_download(partial_download_path(self._pdf_path))
        self._drop_held()
        self._release_page(at_once=False)
        self.finished.emit(verdict)

    def _release_page(self, at_once: bool) -> None:
        """Let the page go: later when inside its own signal, at once at quit.

        Args:
            at_once: Delete it now (quit), rather than from the event loop.
        """
        page, self._page = self._page, None
        if page is None:
            return
        page.loadingChanged.disconnect(self._on_loading_changed)
        page.renderProcessTerminated.disconnect(self._on_render_process_terminated)
        if at_once:
            Shiboken.delete(page)
        else:
            page.deleteLater()
