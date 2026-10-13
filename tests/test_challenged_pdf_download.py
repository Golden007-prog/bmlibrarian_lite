# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""An open-access PDF met by a bot wall is recorded as challenged (#480).

Only the recorded kind changes: which branch of ``_try_download`` answers,
and whether it is a paywall, stay as they were.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
import requests

from bmlibrarian_lite.data_models import RequestFailure, RequestFailureKind
from bmlibrarian_lite.pdf_discovery import PDFDiscoverer, PDFSource, PDFSourceType

URL = "https://walled.example.org/article.pdf"


class _Response:
    """A streamed answer: status, headers, and the body in chunks.

    Attributes:
        status_code: The HTTP status.
        headers: The response headers.
        url: The URL the answer came from.
        is_redirect: Never a redirect.
    """

    is_redirect = False

    def __init__(
        self,
        status_code: int,
        headers: dict[str, str],
        chunks: list[bytes],
        break_after: int | None = None,
    ) -> None:
        """Answer with these chunks, optionally breaking after some.

        Args:
            status_code: The HTTP status.
            headers: The headers.
            chunks: The body, chunk by chunk.
            break_after: Raise ``ChunkedEncodingError`` after this many chunks.
        """
        self.status_code = status_code
        self.headers = headers
        self.url = URL
        self._chunks = chunks
        self._break_after = break_after

    def iter_content(self, chunk_size: int = 8192) -> Iterator[bytes]:
        """Yield the chunks, breaking where scripted."""
        for index, chunk in enumerate(self._chunks):
            if self._break_after is not None and index >= self._break_after:
                raise requests.exceptions.ChunkedEncodingError("broken")
            yield chunk

    def raise_for_status(self) -> None:
        """Raise for an error status, as ``requests`` does."""
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)


class _Session:
    """Answers every GET with one response."""

    def __init__(self, response: _Response) -> None:
        self._response = response

    def get(self, url: str, **kwargs: object) -> _Response:
        """The scripted response."""
        return self._response


@pytest.fixture
def discovery() -> PDFDiscoverer:
    """A discoverer with no network."""
    return PDFDiscoverer(unpaywall_email="test@example.org")


def _download(
    discovery: PDFDiscoverer,
    tmp_path: Path,
    response: _Response,
    source_type: PDFSourceType = PDFSourceType.UNPAYWALL_OA,
):
    discovery._session = _Session(response)  # type: ignore[assignment]
    source = PDFSource(url=URL, source_type=source_type, is_open_access=True)
    return discovery._try_download(source, tmp_path / "out.pdf", None)


def test_a_cloudflare_403_is_challenged(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """ScienceDirect's wall: its markup lies past what is read."""
    result = _download(
        discovery, tmp_path, _Response(403, {"Server": "cloudflare"}, [b"<html><style>"])
    )
    assert result.failure == RequestFailure(RequestFailureKind.CHALLENGED, 403)
    assert result.is_paywall is True  # the branch it took is unchanged


def test_a_cloudflare_503_is_challenged(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """A challenge with an outage's status is not an outage."""
    result = _download(
        discovery, tmp_path, _Response(503, {"Server": "cloudflare"}, [b"Just a moment..."])
    )
    assert result.failure == RequestFailure(RequestFailureKind.CHALLENGED, 503)


def test_a_challenge_served_as_a_pdf_is_challenged(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """Review Focus 1: a 200 labelled application/pdf whose body is a challenge."""
    result = _download(
        discovery,
        tmp_path,
        _Response(200, {"Content-Type": "application/pdf"}, [b"<html>", b"<title>Preparing to download"]),
    )
    assert result.failure == RequestFailure(RequestFailureKind.CHALLENGED, 200)
    assert not (tmp_path / "out.pdf").exists()


def test_a_marker_past_the_first_chunk_is_read(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """The prefix is read past the four bytes the PDF check needs."""
    chunks = [b"<htm", b"l>" + b" " * 9000, b"g-recaptcha"]
    result = _download(discovery, tmp_path, _Response(200, {}, chunks))
    assert result.failure == RequestFailure(RequestFailureKind.CHALLENGED, 200)


def test_a_stream_breaking_in_the_longer_read_keeps_what_was_read(
    discovery: PDFDiscoverer, tmp_path: Path
) -> None:
    """Review Focus 2: the challenge already seen is not lost to a broken stream."""
    chunks = [b"<htm", b"l>Just a moment...", b"never read"]
    result = _download(
        discovery, tmp_path, _Response(403, {"Server": "nginx"}, chunks, break_after=2)
    )
    assert result.failure == RequestFailure(RequestFailureKind.CHALLENGED, 403)


def test_control_an_ordinary_403_keeps_its_status(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """No marker: the refusal is still the host's answer."""
    result = _download(discovery, tmp_path, _Response(403, {"Server": "nginx"}, [b"<html>Forbidden</html>"]))
    assert result.failure == RequestFailure(RequestFailureKind.HTTP_STATUS, 403)


def test_control_a_404_with_challenge_markup_is_gone(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """Gone is gone, whatever the page says."""
    result = _download(discovery, tmp_path, _Response(404, {"Server": "cloudflare"}, [b"Just a moment..."]))
    assert result.failure == RequestFailure(RequestFailureKind.HTTP_STATUS, 404)


def test_control_a_non_pdf_without_markers_is_malformed(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """Review Focus 3: a JPEG listed as a PDF."""
    result = _download(discovery, tmp_path, _Response(200, {}, [b"\xff\xd8\xff\xe0" + bytes(range(128, 256))]))
    assert result.failure == RequestFailure(RequestFailureKind.MALFORMED_RESPONSE)


def test_control_a_pdf_is_saved_whole(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """The longer read never runs for a PDF, and the file keeps its header."""
    body = [b"%PDF", b"-1.7 rest of the file"]
    result = _download(discovery, tmp_path, _Response(200, {"Content-Type": "application/pdf"}, body))
    assert result.success is True
    assert (tmp_path / "out.pdf").read_bytes() == b"".join(body)


def test_control_other_sources_keep_their_reporting(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """Only the open-access copies are recognised: a DOI download keeps its 403."""
    result = _download(
        discovery,
        tmp_path,
        _Response(403, {"Server": "cloudflare"}, [b"Just a moment..."]),
        source_type=PDFSourceType.DOI_DIRECT,
    )
    assert result.failure == RequestFailure(RequestFailureKind.HTTP_STATUS, 403)


def test_the_paywall_sniff_keeps_its_own_prefix(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """The longer read is for the challenge only: a phrase past the first chunk is no paywall."""
    chunks = [b"<html>" + b" " * 9000, b"<p>access denied</p>"]
    result = _download(discovery, tmp_path, _Response(200, {"Content-Type": "text/html"}, chunks))
    assert result.is_paywall is False
    assert result.failure == RequestFailure(RequestFailureKind.MALFORMED_RESPONSE)


def test_a_challenged_copy_is_told_as_a_non_answer() -> None:
    """The record a challenged download leaves is told as a source that gave no answer."""
    from bmlibrarian_lite.analysis_failures import unestablished_access_clause
    from bmlibrarian_lite.pdf_discovery import DiscoveryResult, unobtained_open_access_pdf

    source = PDFSource(url=URL, source_type=PDFSourceType.UNPAYWALL_OA)
    result = DiscoveryResult(
        success=False, failure=RequestFailure(RequestFailureKind.CHALLENGED, 403)
    )
    text = unestablished_access_clause(unobtained_open_access_pdf(source, result))
    assert "walled.example.org" in text
    assert "the host asked for a robot check" in text
    assert "Whether this document is open access was not established." in text
