"""RescueAttempt's handlers, driven directly with stand-ins (#480, stage 2).

The engine itself runs in ``browser_rescue_harness.py``, in its own process
(#516, #517). Here each handler is called as QtWebEngine would call it, with
stand-in loading infos, pages and download requests, so the rules that turn
what the browser saw into a verdict are pinned without pumping the suite's
shared QApplication.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PySide6.QtWebEngineCore import QWebEngineLoadingInfo

from bmlibrarian_lite.browser_rescue import RescueOutcome, RescueVerdict
from bmlibrarian_lite.gui import rescue_attempt
from bmlibrarian_lite.gui.rescue_attempt import RescueAttempt
from bmlibrarian_lite.pdf_download import partial_download_path

Domain = QWebEngineLoadingInfo.ErrorDomain
Status = QWebEngineLoadingInfo.LoadStatus
ADDRESS = "https://publisher.example/article.pdf"
TIMEOUT_MS = 1_000
STALL_MS = 1_000
MAX_PDF_BYTES = 1_024
ERR_ABORTED = -3
ERR_UNEXPECTED = -9


@pytest.fixture
def qapp():
    """A QApplication for the attempt's timers; never pumped."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Info:
    """A stand-in for QWebEngineLoadingInfo."""

    def __init__(
        self,
        status: Any,
        domain: Any = Domain.NoErrorDomain,
        code: int = 0,
        headers: dict[bytes, list[bytes]] | None = None,
        download: bool = False,
    ) -> None:
        """Hold what the load reports."""
        self._status = status
        self._domain = domain
        self._code = code
        self._headers = headers or {}
        self._download = download

    def isDownload(self) -> bool:  # noqa: N802
        """Whether the load became a download."""
        return self._download

    def status(self) -> Any:
        """The load's status."""
        return self._status

    def errorDomain(self) -> Any:  # noqa: N802
        """The domain of its code."""
        return self._domain

    def errorCode(self) -> int:  # noqa: N802
        """Its code: the HTTP status, for an answer."""
        return self._code

    def responseHeaders(self) -> dict[bytes, list[bytes]]:  # noqa: N802
        """The answer's headers."""
        return self._headers


def _answered(status: int, headers: dict[bytes, list[bytes]] | None = None) -> _Info:
    """A main-frame load that ended with this HTTP status."""
    return _Info(Status.LoadSucceededStatus, Domain.HttpStatusCodeDomain, status, headers)


def _page() -> SimpleNamespace:
    """A stand-in page the attempt can let go of."""
    signal = SimpleNamespace(disconnect=lambda *_: None)
    return SimpleNamespace(loadingChanged=signal, renderProcessTerminated=signal, deleteLater=lambda: None)


class _Request:
    """A stand-in download request from ``page``."""

    def __init__(self, page: object, total_bytes: int = 10) -> None:
        """A request from ``page`` announcing ``total_bytes``."""
        self._page = page
        self._total = total_bytes
        self.cancelled = False
        self.accepted = False
        self.receivedBytesChanged = SimpleNamespace(connect=lambda *_: None)  # noqa: N815
        self.isFinishedChanged = SimpleNamespace(connect=lambda *_: None)  # noqa: N815

    def page(self) -> object:
        """The page that started it."""
        return self._page

    def totalBytes(self) -> int:  # noqa: N802
        """The size announced."""
        return self._total

    def cancel(self) -> None:
        """Refuse it."""
        self.cancelled = True

    def accept(self) -> None:
        """Take it."""
        self.accepted = True

    def isFinished(self) -> bool:  # noqa: N802
        """Never finished: these tests end before it would."""
        return False

    def setDownloadDirectory(self, _directory: str) -> None:  # noqa: N802
        """Ignored."""

    def setDownloadFileName(self, _name: str) -> None:  # noqa: N802
        """Ignored."""


def _attempt(tmp_path: Path, pdf_path: Path | None = None) -> tuple[RescueAttempt, list[RescueVerdict]]:
    """An attempt whose page is a stand-in, as if started, and its verdicts."""
    attempt = RescueAttempt(
        None,  # type: ignore[arg-type]
        ADDRESS, pdf_path or tmp_path / "article.pdf", TIMEOUT_MS, STALL_MS, MAX_PDF_BYTES,
    )
    attempt._page = _page()  # type: ignore[assignment]
    verdicts: list[RescueVerdict] = []
    attempt.finished.connect(verdicts.append)
    return attempt, verdicts


# -- what a load says ---------------------------------------------------------


def test_an_internal_load_error_learnt_nothing(qapp, tmp_path: Path) -> None:
    """A Chromium internal failure other than ERR_ABORTED ends the visit as transport."""
    attempt, verdicts = _attempt(tmp_path)
    attempt._on_loading_changed(_Info(Status.LoadFailedStatus, Domain.InternalErrorDomain, ERR_UNEXPECTED))
    assert verdicts == [RescueVerdict(RescueOutcome.TRANSPORT)]


def test_an_aborted_navigation_is_no_failure(qapp, tmp_path: Path) -> None:
    """ERR_ABORTED, which a script-driven navigation produces, ends nothing."""
    attempt, verdicts = _attempt(tmp_path)
    attempt._on_loading_changed(_Info(Status.LoadFailedStatus, Domain.InternalErrorDomain, ERR_ABORTED))
    assert verdicts == []


@pytest.mark.parametrize(
    "domain", [Domain.ConnectionErrorDomain, Domain.DnsErrorDomain, Domain.CertificateErrorDomain]
)
def test_a_network_failure_learnt_nothing(qapp, tmp_path: Path, domain: Any) -> None:
    """A network failure ends the visit as transport, with the last status seen."""
    attempt, verdicts = _attempt(tmp_path)
    attempt._on_loading_changed(_answered(200))
    attempt._on_loading_changed(_Info(Status.LoadFailedStatus, domain, ERR_UNEXPECTED))
    assert verdicts == [RescueVerdict(RescueOutcome.TRANSPORT, 200)]


def test_a_renderer_crash_learnt_nothing(qapp, tmp_path: Path) -> None:
    """A renderer that dies ends the visit as transport."""
    attempt, verdicts = _attempt(tmp_path)
    attempt._on_loading_changed(_answered(200))
    attempt._on_render_process_terminated(None, 1)
    assert verdicts == [RescueVerdict(RescueOutcome.TRANSPORT, 200)]


def test_a_throttling_answer_slows_the_host(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every main-frame answer is offered to the host's limiter, with its headers."""
    seen: list[tuple[str, int | None, dict[str, str]]] = []
    monkeypatch.setattr(
        rescue_attempt, "penalise_host_for_answer", lambda *args: seen.append(args) or True
    )
    attempt, _ = _attempt(tmp_path)
    attempt._on_loading_changed(_answered(429, {b"Retry-After": [b"120"]}))
    assert seen == [(ADDRESS, 429, {"Retry-After": "120"})]


def test_a_code_that_is_no_http_status_counts_as_none(qapp, tmp_path: Path) -> None:
    """An answer whose code is no HTTP status leaves the visit with none seen."""
    attempt, verdicts = _attempt(tmp_path)
    attempt._on_loading_changed(_answered(0))
    attempt._on_html("<html>An abstract.</html>")
    assert verdicts == [RescueVerdict(RescueOutcome.TRANSPORT)]


# -- judging the page ---------------------------------------------------------


def test_a_page_still_navigating_is_not_judged_as_no_pdf(qapp, tmp_path: Path) -> None:
    """A page that is leaving for an unanswered address says nothing of that address."""
    attempt, verdicts = _attempt(tmp_path)
    attempt._on_loading_changed(_Info(Status.LoadStartedStatus))
    attempt._on_loading_changed(_answered(200))
    attempt._on_loading_changed(_Info(Status.LoadStartedStatus))  # its script moves on
    attempt._on_html("<html>Redirecting to the PDF...</html>")
    assert verdicts == [RescueVerdict(RescueOutcome.TRANSPORT, 200)]


def test_a_page_whose_navigation_ended_is_judged(qapp, tmp_path: Path) -> None:
    """Once the last navigation answered, a page with no PDF is no_pdf."""
    attempt, verdicts = _attempt(tmp_path)
    attempt._on_loading_changed(_Info(Status.LoadStartedStatus))
    attempt._on_loading_changed(_answered(200))
    attempt._on_html("<html>An abstract.</html>")
    assert verdicts == [RescueVerdict(RescueOutcome.NO_PDF, 200)]


def test_a_challenge_still_navigating_waits_for_a_person(qapp, tmp_path: Path) -> None:
    """A challenge shown while a navigation is open is a challenge: an open state."""
    attempt, verdicts = _attempt(tmp_path)
    attempt._on_loading_changed(_answered(403))
    attempt._on_loading_changed(_Info(Status.LoadStartedStatus))
    attempt._on_html("<html><title>Just a moment...</title></html>")
    assert verdicts == [RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare",))]


# -- downloads ----------------------------------------------------------------


def test_a_download_from_another_page_is_refused(qapp, tmp_path: Path) -> None:
    """A page an earlier visit let go of may start a download: another article's PDF."""
    attempt, verdicts = _attempt(tmp_path)
    stray = _Request(page=_page())
    attempt.accept_download(stray)  # type: ignore[arg-type]
    assert stray.cancelled and not stray.accepted
    assert attempt._download is None
    assert verdicts == []


def test_a_download_with_no_page_is_refused(qapp, tmp_path: Path) -> None:
    """A download no page started is not this visit's."""
    attempt, _ = _attempt(tmp_path)
    request = _Request(page=None)
    attempt.accept_download(request)  # type: ignore[arg-type]
    assert request.cancelled and attempt._download is None


def test_the_visits_own_download_is_taken(qapp, tmp_path: Path) -> None:
    """A download this visit's page started is accepted into the partial file."""
    attempt, _ = _attempt(tmp_path)
    request = _Request(page=attempt._page)
    attempt.accept_download(request)  # type: ignore[arg-type]
    assert request.accepted and attempt._download is request


def test_a_download_after_the_visit_ended_is_refused(qapp, tmp_path: Path) -> None:
    """Once its verdict is out, a visit takes no download, even from its own page."""
    attempt, verdicts = _attempt(tmp_path)
    page = attempt._page
    attempt._on_loading_changed(_answered(404))
    request = _Request(page=page)
    attempt.accept_download(request)  # type: ignore[arg-type]
    assert request.cancelled and not request.accepted
    assert [v.outcome for v in verdicts] == [RescueOutcome.GONE]


def test_no_folder_for_the_download_learnt_nothing(qapp, tmp_path: Path) -> None:
    """A PDF library folder that cannot be made is the computer's failure, not the article's."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the folder should be")
    attempt, verdicts = _attempt(tmp_path, blocker / "article.pdf")
    request = _Request(page=attempt._page)
    attempt.accept_download(request)  # type: ignore[arg-type]
    assert request.cancelled and not request.accepted
    assert verdicts == [RescueVerdict(RescueOutcome.TRANSPORT)]


def test_a_pdf_that_cannot_be_kept_learnt_nothing(qapp, tmp_path: Path) -> None:
    """A PDF that arrived and could not be renamed into place is transport, and leaves nothing."""
    pdf_path = tmp_path / "article.pdf"
    pdf_path.mkdir()  # a folder where the PDF should go: the rename fails
    written = partial_download_path(pdf_path)
    written.write_bytes(b"%PDF-1.4\n" + b"0" * 100)
    attempt, _ = _attempt(tmp_path, pdf_path)
    assert attempt._settle(written) == RescueVerdict(RescueOutcome.TRANSPORT)
    assert not written.exists()
    assert pdf_path.is_dir()


def test_a_pdf_within_the_limit_is_kept(qapp, tmp_path: Path) -> None:
    """The control: a whole PDF is renamed into place."""
    pdf_path = tmp_path / "article.pdf"
    written = partial_download_path(pdf_path)
    written.write_bytes(b"%PDF-1.4\n" + b"0" * 100)
    attempt, _ = _attempt(tmp_path, pdf_path)
    assert attempt._settle(written) == RescueVerdict(RescueOutcome.PDF)
    assert pdf_path.read_bytes().startswith(b"%PDF") and not written.exists()


def test_an_empty_download_learnt_nothing(qapp, tmp_path: Path) -> None:
    """An empty file is no evidence the address has no PDF."""
    pdf_path = tmp_path / "article.pdf"
    written = partial_download_path(pdf_path)
    written.write_bytes(b"")
    attempt, _ = _attempt(tmp_path, pdf_path)
    assert attempt._settle(written) == RescueVerdict(RescueOutcome.TRANSPORT)
    assert not pdf_path.exists() and not written.exists()
