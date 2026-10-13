"""One visit of one walled PDF's address, in a web page with no view (#480, stage 2).

Driven only by signals and timers, so nothing here blocks the GUI thread.
The page is QtWebEngine's own, with its own User-Agent and no automation
protocol (no impersonation, spec decision 5). The profile's PDF viewer is
off, so a PDF arrives as a download. It is written beside its cache path
as a partial file, and renamed into place only once it is whole, within
the size limit, and begins with ``%PDF``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEngineLoadingInfo,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from shiboken6 import Shiboken

from ..browser_rescue import RescueOutcome, RescueVerdict, download_verdict, page_verdict
from ..constants import (
    BROWSER_PROFILE_CACHE_DIRNAME,
    BROWSER_PROFILE_STORAGE_NAME,
    CHALLENGE_GONE_STATUSES,
    CONFIG_DIR_PERMISSIONS,
    PDF_MAGIC_BYTES,
    RESCUE_PAGE_READ_GRACE_MS,
)
from ..pdf_download import discard_partial_download, partial_download_path

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


class RescueAttempt(QObject):
    """One hidden visit of one address, ending in exactly one verdict.

    Signals:
        finished: The visit's :class:`RescueVerdict`, emitted once, and never
            after :meth:`abort`.
    """

    finished = Signal(object)

    def __init__(
        self,
        profile: QWebEngineProfile,
        address: str,
        pdf_path: Path,
        timeout_ms: int,
        stall_ms: int,
        max_pdf_bytes: int,
        parent: QObject | None = None,
    ) -> None:
        """Prepare the visit; :meth:`start` makes it.

        Args:
            profile: The rescue profile (:func:`rescue_profile`).
            address: The PDF's address.
            pdf_path: Where a PDF that arrives is saved.
            timeout_ms: How long to wait for a download before reading the page.
            stall_ms: How long a started download may receive nothing.
            max_pdf_bytes: The largest PDF kept.
            parent: The owning object.
        """
        super().__init__(parent)
        self._profile = profile
        self._address = address
        self._pdf_path = pdf_path
        self._timeout_ms = timeout_ms
        self._max_pdf_bytes = max_pdf_bytes
        self._page: QWebEnginePage | None = None
        self._download: QWebEngineDownloadRequest | None = None
        self._status: int | None = None
        self._headers: dict[str, str] = {}
        self._too_large = False
        self._reading_page = False
        self._done = False
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)
        self._stall = QTimer(self)
        self._stall.setSingleShot(True)
        self._stall.setInterval(stall_ms)
        self._stall.timeout.connect(self._on_stall)

    def start(self) -> None:
        """Load the address in a page with no view, and start waiting."""
        # Parentless, so it is deleted when this visit says, always before
        # the profile, as QtWebEngine requires (_release_page)
        page = QWebEnginePage(self._profile)
        page.loadingChanged.connect(self._on_loading_changed)
        page.renderProcessTerminated.connect(self._on_render_process_terminated)
        self._page = page
        self._timeout.start(self._timeout_ms)
        page.load(QUrl(self._address))

    def accept_download(self, request: QWebEngineDownloadRequest) -> None:
        """Take the download this visit started, into the partial file.

        Only the first: a page that starts a second is not believed twice.
        A size announced over the limit is refused before a byte is written.

        Args:
            request: The profile's download request.
        """
        if self._done or self._download is not None:
            request.cancel()
            return
        self._timeout.stop()
        self._download = request
        if request.totalBytes() > self._max_pdf_bytes:
            request.cancel()
            self._finish(RescueVerdict(RescueOutcome.TOO_LARGE))
            return
        partial = partial_download_path(self._pdf_path)
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

    def abort(self) -> None:
        """End the visit with no verdict, for quit: its entry stays as it was."""
        if self._done:
            return
        self._done = True
        self._timeout.stop()
        self._stall.stop()
        if self._download is not None and not self._download.isFinished():
            self._download.cancel()
            discard_partial_download(partial_download_path(self._pdf_path))
        self._release_page(at_once=True)

    def _on_loading_changed(self, info: QWebEngineLoadingInfo) -> None:
        """Note each main-frame answer; end the visit on a gone or a network failure.

        Args:
            info: The load's state.
        """
        if self._done or info.isDownload() or info.status() == _LoadStatus.LoadStartedStatus:
            return
        if self._download is not None:
            # A download is under way: what the page does now is not the answer
            return
        domain = info.errorDomain()
        if domain == _Domain.HttpStatusCodeDomain:
            # Every main-frame answer reports its status here, a 200 included
            self._status = info.errorCode()
            self._headers = response_headers(info.responseHeaders())
            if self._status in CHALLENGE_GONE_STATUSES:
                self._finish(page_verdict(self._status, self._headers, ""))
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
        if self._done or self._download is not None or self._page is None:
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

        Args:
            html: The page's HTML.
        """
        if self._done or self._download is not None:
            return
        if self._status is None:
            # No server ever answered: the page read is the empty initial
            # document, and the visit learnt nothing
            self._finish(RescueVerdict(RescueOutcome.TRANSPORT))
            return
        self._finish(page_verdict(self._status, self._headers, html))

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
        """Keep a whole PDF; anything else ends the visit without one."""
        request = self._download
        if self._done or request is None or not request.isFinished():
            return
        written = Path(request.downloadDirectory()) / request.downloadFileName()
        if request.state() != _DownloadState.DownloadCompleted:
            discard_partial_download(written)
            outcome = RescueOutcome.TOO_LARGE if self._too_large else RescueOutcome.TRANSPORT
            self._finish(RescueVerdict(outcome))
            return
        self._finish(self._settle(written))

    def _settle(self, written: Path) -> RescueVerdict:
        """Rename a finished download into place if it is a PDF within the limit.

        Args:
            written: Where QtWebEngine wrote it.

        Returns:
            The verdict; the partial file is gone either way.
        """
        try:
            if written.stat().st_size > self._max_pdf_bytes:
                return RescueVerdict(RescueOutcome.TOO_LARGE)
            with open(written, "rb") as f:
                verdict = download_verdict(f.read(len(PDF_MAGIC_BYTES)))
            if verdict.outcome is RescueOutcome.PDF:
                written.replace(self._pdf_path)
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
