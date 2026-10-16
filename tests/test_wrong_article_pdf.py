"""A downloaded PDF of another article is refused, never read (#541).

Discovery used to save a PDF whose first page lacked the title and attach a
``verification_warning`` that full-text discovery dropped, so another
article's text fed this one's transparency rating. Such a PDF is now set
aside (``<pdf>.mismatch``), the next source is tried, and the copy is
recorded as ``WRONG_ARTICLE``: the right PDF may exist elsewhere, so it is
never an absence. The cached-PDF tier checks what it finds the same way.
"""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import fitz
import pytest
import requests

from bmlibrarian_lite.analysis_failures import tried_sources_statement
from bmlibrarian_lite.constants import (
    PDF_MISMATCH_SUFFIX,
    SERVICE_ELSEVIER,
    SERVICE_PDF_DOWNLOAD,
    SERVICE_UNPAYWALL_PDF,
)
from bmlibrarian_lite.data_models import LookupRecord, LookupSkipReason, SourceLookupSkipped
from bmlibrarian_lite.elsevier_api import ElsevierFetch
from bmlibrarian_lite.fulltext_discovery import FulltextDiscoverer, FulltextSourceType
from bmlibrarian_lite.pdf_discovery import (
    DiscoveryResult,
    PDFDiscoverer,
    PDFSource,
    PDFSourceType,
)
from bmlibrarian_lite.pdf_library_check import mark_kept

TITLE = "Coffee and caffeine consumption and the risk of hypertension in postmenopausal women"
OTHER = "Scientific opinion on the safety of bovine colostrum filtration"
DOI = "10.3945/ajcn.115.120147"
UNPAYWALL_PDF = "https://repo.example.org/bitstream/a.pdf"
PUBLISHER_PDF = "https://publisher.example.org/a.pdf"


def pdf_bytes(*pages: str) -> bytes:
    """A real PDF with one page per text given; an empty text leaves a page blank."""
    document = fitz.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_textbox(fitz.Rect(50, 50, 550, 800), text)
    data = document.tobytes()
    document.close()
    return bytes(data)


THIS_ARTICLE = pdf_bytes(TITLE + "\n\nAbstract. Methods. Results.")
ANOTHER_ARTICLE = pdf_bytes(OTHER + "\n\nAbstract. Methods. Results.")


def _unpaywall(url: str = UNPAYWALL_PDF) -> PDFSource:
    """A PDF Unpaywall named."""
    return PDFSource(url=url, source_type=PDFSourceType.UNPAYWALL_OA, is_open_access=True)


def _publisher(url: str = PUBLISHER_PDF) -> PDFSource:
    """A PDF from the DOI's own resolution."""
    return PDFSource(url=url, source_type=PDFSourceType.DOI_DIRECT, is_open_access=False)


def _response(body: bytes, url: str) -> requests.Response:
    """A 200 PDF held in memory."""
    response = requests.Response()
    response.status_code = 200
    response._content = body
    response._content_consumed = True
    response.headers["Content-Type"] = "application/pdf"
    response.url = url
    return response


class _Session:
    """Answers each URL with a canned PDF."""

    def __init__(self, answers: dict[str, bytes]) -> None:
        self.answers = answers
        self.requested: list[str] = []

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        """Route one GET by URL."""
        self.requested.append(url)
        return _response(self.answers[url], url)


def _discover(
    sources: list[PDFSource], answers: dict[str, bytes], tmp_path: Path, title: str | None = TITLE
) -> DiscoveryResult:
    """Run the download loop over ``sources``, every lookup answered."""
    discoverer = PDFDiscoverer(unpaywall_email="test@example.com")
    discoverer._discover_sources = lambda doi, pmid, pmcid: (list(sources), LookupRecord())  # type: ignore[method-assign]
    discoverer._session = _Session(answers)  # type: ignore[assignment]
    return discoverer.discover_and_download(tmp_path / "a.pdf", doi=DOI, expected_title=title)


def _wrong(service: str, address: str) -> SourceLookupSkipped:
    """The record of another article's PDF served at ``address``."""
    return SourceLookupSkipped(service, LookupSkipReason.WRONG_ARTICLE, address)


# -- the download ---------------------------------------------------------------


def test_another_article_s_pdf_is_set_aside_and_the_next_source_tried(tmp_path: Path) -> None:
    """The publisher's own PDF is the article's: it is what is kept."""
    result = _discover(
        [_unpaywall(), _publisher()],
        {UNPAYWALL_PDF: ANOTHER_ARTICLE, PUBLISHER_PDF: THIS_ARTICLE},
        tmp_path,
    )

    assert result.success
    assert result.file_path == tmp_path / "a.pdf"
    assert result.file_path.read_bytes() == THIS_ARTICLE
    assert result.verification_warning is None
    # A later source that serves the article settles what the first left open
    assert result.lookups == LookupRecord()
    assert (tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}").read_bytes() == ANOTHER_ARTICLE


def test_only_another_article_s_pdf_is_no_absence(tmp_path: Path) -> None:
    """Recorded under the copy's name, with its address, and told to the reader."""
    result = _discover([_unpaywall()], {UNPAYWALL_PDF: ANOTHER_ARTICLE}, tmp_path)

    assert not result.success
    assert not (tmp_path / "a.pdf").exists()
    assert result.lookups == LookupRecord(skipped=(_wrong(SERVICE_UNPAYWALL_PDF, UNPAYWALL_PDF),))
    assert result.lookups.anything_unsettled
    assert "repo.example.org, named by Unpaywall (another article's PDF)" in (result.error or "")


def test_a_non_open_access_source_s_wrong_pdf_is_recorded_under_the_download(tmp_path: Path) -> None:
    """The DOI's own PDF: recorded with its address, told by its host alone."""
    result = _discover([_publisher()], {PUBLISHER_PDF: ANOTHER_ARTICLE}, tmp_path)

    assert not result.success
    assert result.lookups == LookupRecord(skipped=(_wrong(SERVICE_PDF_DOWNLOAD, PUBLISHER_PDF),))
    assert "publisher.example.org (another article's PDF)" in (result.error or "")
    assert "named by the PDF download" not in (result.error or "")


def test_every_wrong_copy_is_told_in_order(tmp_path: Path) -> None:
    """Two copies, both another article's: both told, Unpaywall's first."""
    result = _discover(
        [_unpaywall(), _publisher()],
        {UNPAYWALL_PDF: ANOTHER_ARTICLE, PUBLISHER_PDF: pdf_bytes("Renal physiology in the aged rat")},
        tmp_path,
    )

    assert result.lookups.skipped == (
        _wrong(SERVICE_UNPAYWALL_PDF, UNPAYWALL_PDF),
        _wrong(SERVICE_PDF_DOWNLOAD, PUBLISHER_PDF),
    )
    # Different bytes: neither is lost
    assert (tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}").read_bytes() == ANOTHER_ARTICLE
    assert (tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}-2").is_file()


def test_the_article_s_own_pdf_is_kept(tmp_path: Path) -> None:
    """The control: a PDF carrying the title is a plain success."""
    result = _discover([_unpaywall()], {UNPAYWALL_PDF: THIS_ARTICLE}, tmp_path)

    assert result.success
    assert result.verification_warning is None
    assert result.lookups == LookupRecord()


def test_a_scan_is_kept_with_a_warning(tmp_path: Path) -> None:
    """A PDF whose title cannot be judged is not refused: only a mismatch is."""
    result = _discover([_unpaywall()], {UNPAYWALL_PDF: pdf_bytes("")}, tmp_path)

    assert result.success
    assert result.verification_warning == "PDF contains no extractable text"


def test_a_download_with_no_title_to_check_is_kept(tmp_path: Path) -> None:
    """The control: no expected title, no check, as before."""
    result = _discover([_unpaywall()], {UNPAYWALL_PDF: ANOTHER_ARTICLE}, tmp_path, title=None)

    assert result.success


def test_a_wrong_pdf_that_cannot_be_set_aside_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rename fails: the download is refused all the same, and logged."""

    def refuse(self: Path, target: Path) -> Path:
        raise PermissionError("read-only library")

    monkeypatch.setattr(Path, "rename", refuse)

    result = _discover([_unpaywall()], {UNPAYWALL_PDF: ANOTHER_ARTICLE}, tmp_path)

    assert not result.success
    assert result.lookups.skipped == (_wrong(SERVICE_UNPAYWALL_PDF, UNPAYWALL_PDF),)


@pytest.mark.parametrize(
    "why",
    [
        {"success": True, "file_path": Path("a.pdf")},
        {"not_saved": True},
        {"refused_for_size": True},
    ],
)
def test_another_article_s_pdf_is_its_own_reason(why: dict[str, Any]) -> None:
    """A wrong article is neither a success nor any other reason no PDF was had."""
    with pytest.raises(ValueError):
        DiscoveryResult(**{"success": False, **why, "wrong_article": True})


def test_wrong_article_names_its_pdf() -> None:
    """A wrong article's skip without an address is refused: it cannot be told."""
    with pytest.raises(ValueError):
        SourceLookupSkipped(SERVICE_UNPAYWALL_PDF, LookupSkipReason.WRONG_ARTICLE)


def test_the_tried_sources_name_a_copy_s_namer() -> None:
    """The control: an open-access copy is still told by who named it."""
    statement = tried_sources_statement(
        LookupRecord(skipped=(_wrong(SERVICE_UNPAYWALL_PDF, UNPAYWALL_PDF),))
    )
    assert "repo.example.org, named by Unpaywall (another article's PDF)" in statement


# -- Elsevier's API -------------------------------------------------------------


class _Elsevier:
    """Elsevier's client, serving one PDF's bytes for any Elsevier DOI."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    def article_url(self, doi: str) -> str:
        """The article URL, keyless."""
        return f"https://api.elsevier.com/content/article/doi/{doi}"

    def fetch_pdf(self, doi: str, output_path: Path, cancelled: Any) -> ElsevierFetch:
        """Write the PDF where asked."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(self.body)
        return ElsevierFetch.served(output_path)


def test_elsevier_s_wrong_pdf_is_refused_and_the_walk_goes_on(tmp_path: Path) -> None:
    """Elsevier's copy is recorded first, and the article's own PDF is then had."""
    discoverer = PDFDiscoverer(unpaywall_email="test@example.com", elsevier=_Elsevier(ANOTHER_ARTICLE))  # type: ignore[arg-type]
    discoverer._discover_sources = lambda doi, pmid, pmcid: ([_publisher()], LookupRecord())  # type: ignore[method-assign]
    discoverer._session = _Session({PUBLISHER_PDF: THIS_ARTICLE})  # type: ignore[assignment]

    result = discoverer.discover_and_download(tmp_path / "a.pdf", doi="10.1016/j.x.2020.1", expected_title=TITLE)

    assert result.success
    assert result.file_path is not None and result.file_path.read_bytes() == THIS_ARTICLE
    assert (tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}").read_bytes() == ANOTHER_ARTICLE


def test_elsevier_s_wrong_pdf_alone_is_no_absence(tmp_path: Path) -> None:
    """Recorded under Elsevier's API with the article URL, which carries no key."""
    elsevier = _Elsevier(ANOTHER_ARTICLE)
    discoverer = PDFDiscoverer(unpaywall_email="test@example.com", elsevier=elsevier)  # type: ignore[arg-type]
    discoverer._discover_sources = lambda doi, pmid, pmcid: ([], LookupRecord())  # type: ignore[method-assign]
    discoverer._discover_openalex = lambda doi, known: ([], LookupRecord())  # type: ignore[method-assign]

    result = discoverer.discover_and_download(tmp_path / "a.pdf", doi="10.1016/j.x.2020.1", expected_title=TITLE)

    assert not result.success
    assert result.lookups.skipped == (
        _wrong(SERVICE_ELSEVIER, elsevier.article_url("10.1016/j.x.2020.1")),
    )


# -- the chain: the cached PDF and the download ----------------------------------


def _chain(tmp_path: Path) -> FulltextDiscoverer:
    """A full-text discovery whose earlier tiers find nothing and whose PDFs live in ``tmp_path``."""
    discoverer = FulltextDiscoverer(unpaywall_email="test@example.com")
    discoverer._try_europepmc_xml = lambda doc, pmid, pmcid, doi: _no_europepmc()  # type: ignore[method-assign]
    return discoverer


def _no_europepmc() -> Any:
    """Europe PMC answered, and holds no record."""
    from bmlibrarian_lite.fulltext_discovery import FulltextResult

    return FulltextResult(success=False, source_type=FulltextSourceType.NOT_ASSESSED)


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The PDF library and the full-text cache, both in ``tmp_path``."""
    pdfs = tmp_path / "pdf"
    monkeypatch.setattr("bmlibrarian_lite.pdf_utils.get_pdf_base_dir", lambda: pdfs)
    monkeypatch.setattr("bmlibrarian_lite.fulltext_discovery.find_existing_fulltext", lambda doc: None)
    return pdfs


def _cached(library: Path, body: bytes) -> Path:
    """A PDF already in the library for :data:`DOI`, as the cache files it."""
    from bmlibrarian_lite.pdf_utils import generate_pdf_path

    path = generate_pdf_path({"doi": DOI}, library)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def test_a_cached_pdf_of_another_article_is_set_aside_and_not_read(library: Path) -> None:
    """Its text never reaches the result; the reader is told where it went."""
    cached = _cached(library, ANOTHER_ARTICLE)
    discoverer = _chain(library)

    with patch.object(
        FulltextDiscoverer, "_try_pdf_download",
        lambda self, *args, **kwargs: _no_europepmc(),
    ):
        result = discoverer.discover_fulltext(doi=DOI, title=TITLE)

    assert not result.success
    assert OTHER not in (result.markdown_content or "")
    assert not cached.exists()
    assert result.set_aside_pdf == cached.with_name(cached.name + PDF_MISMATCH_SUFFIX)
    assert "does not carry this article's title" in (result.error or "")


def test_a_cached_pdf_set_aside_leaves_the_download_to_find_the_article(library: Path) -> None:
    """The download that follows is told the PDF went aside, on success too."""
    cached = _cached(library, ANOTHER_ARTICLE)
    discoverer = _chain(library)

    def download(self: FulltextDiscoverer, doc: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        from bmlibrarian_lite.fulltext_discovery import FulltextResult

        assert not cached.exists(), "the download must not find the wrong PDF in its place"
        cached.write_bytes(THIS_ARTICLE)
        return FulltextResult(
            success=True, source_type=FulltextSourceType.DOWNLOADED_PDF,
            markdown_content=TITLE, file_path=cached,
        )

    with patch.object(FulltextDiscoverer, "_try_pdf_download", download):
        result = discoverer.discover_fulltext(doi=DOI, title=TITLE)

    assert result.success
    assert result.source_type is FulltextSourceType.DOWNLOADED_PDF
    assert result.set_aside_pdf is not None


def test_a_cached_pdf_of_this_article_is_read(library: Path) -> None:
    """The control: the article's own cached PDF is served from the cache."""
    _cached(library, THIS_ARTICLE)

    result = _chain(library).discover_fulltext(doi=DOI, title=TITLE)

    assert result.success
    assert result.source_type is FulltextSourceType.CACHED_PDF
    assert result.set_aside_pdf is None


def test_a_cached_pdf_a_person_kept_is_read(library: Path) -> None:
    """The person's keep stands though the title check doubts it."""
    mark_kept(_cached(library, ANOTHER_ARTICLE))

    result = _chain(library).discover_fulltext(doi=DOI, title=TITLE)

    assert result.success
    assert result.source_type is FulltextSourceType.CACHED_PDF


def test_a_cached_pdf_that_cannot_be_set_aside_is_not_read(
    library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused in place: the chain goes on to the download as if none were cached."""
    cached = _cached(library, ANOTHER_ARTICLE)
    discoverer = _chain(library)
    monkeypatch.setattr(Path, "rename", lambda self, target: (_ for _ in ()).throw(OSError("x")))
    reached: list[bool] = []

    def download(self: FulltextDiscoverer, *args: Any, **kwargs: Any) -> Any:
        reached.append(True)
        return _no_europepmc()

    with patch.object(FulltextDiscoverer, "_try_pdf_download", download):
        result = discoverer.discover_fulltext(doi=DOI, title=TITLE)

    assert reached == [True]
    assert not result.success
    assert cached.is_file()
    assert result.set_aside_pdf is None
    assert "could not be moved aside" in (result.error or "")


# -- Europe PMC's PDF render ---------------------------------------------------

RENDER_URL = "https://europepmc.org/articles/PMC1/pdf=render"


def _render(library: Path, body: bytes) -> Any:
    """Europe PMC's render step, serving ``body`` for the article titled :data:`TITLE`."""
    from bmlibrarian_lite.europepmc import ArticleInfo

    discoverer = FulltextDiscoverer(unpaywall_email="test@example.com")
    discoverer._europepmc._session = _Session({RENDER_URL: body})  # type: ignore[assignment]
    info = ArticleInfo(pmcid="PMC1", has_pdf=True, pdf_render_url=RENDER_URL)
    return discoverer._try_europepmc_pdf({"doi": DOI, "title": TITLE}, info)


def test_europe_pmc_s_pdf_of_another_article_is_set_aside_and_not_read(library: Path) -> None:
    """Recorded as Europe PMC's wrong article: no absence, and no text."""
    from bmlibrarian_lite.constants import SERVICE_EUROPE_PMC
    from bmlibrarian_lite.pdf_utils import generate_pdf_path

    result = _render(library, ANOTHER_ARTICLE)

    pdf = generate_pdf_path({"doi": DOI}, library)
    assert not result.success
    assert result.markdown_content is None
    assert not pdf.exists()
    assert pdf.with_name(pdf.name + PDF_MISMATCH_SUFFIX).read_bytes() == ANOTHER_ARTICLE
    assert result.lookups.skipped == (_wrong(SERVICE_EUROPE_PMC, RENDER_URL),)


def test_europe_pmc_s_pdf_of_this_article_is_read(library: Path) -> None:
    """The control: the article's own render is served."""
    result = _render(library, THIS_ARTICLE)

    assert result.success
    assert result.source_type is FulltextSourceType.EUROPEPMC_PDF


# -- the reader is told: MCP, and the transparency report ------------------------


def _refused_result(tmp_path: Path, *, success: bool) -> Any:
    """A discovery whose library held another article's PDF, set aside."""
    from bmlibrarian_lite.fulltext_discovery import FulltextResult
    from bmlibrarian_lite.pdf_library_check import CachedPdfCheck, CachedPdfOutcome
    from bmlibrarian_lite.pdf_title_check import TitleCheck, TitleCheckOutcome

    refused = CachedPdfCheck(
        CachedPdfOutcome.SET_ASIDE,
        tmp_path / "a.pdf",
        TitleCheck(TitleCheckOutcome.MISMATCH, 0.1),
        tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}",
    )
    if success:
        return FulltextResult(
            success=True, source_type=FulltextSourceType.EUROPEPMC_XML,
            markdown_content="# Article\nContent.", refused_library_pdf=refused,
        )
    return FulltextResult(
        success=False, source_type=FulltextSourceType.NOT_ASSESSED,
        error="No PDF.", refused_library_pdf=refused,
    )


@pytest.mark.parametrize("success", [True, False])
def test_mcp_tells_the_agent_of_a_pdf_set_aside(tmp_path: Path, success: bool) -> None:
    """On success and failure alike, as its own field."""
    from bmlibrarian_lite.mcp_server import _handle_fulltext
    from tests.test_mcp_server import _make_context

    ctx = _make_context()
    ctx.fulltext_discoverer.discover_fulltext.return_value = _refused_result(tmp_path, success=success)
    ctx.interrogation_agent.load_document.return_value = "pmid-1"

    payload = _handle_fulltext({"pmid": "1"}, ctx)

    assert payload["success"] is success
    assert "does not carry this article's title" in payload["library_pdf_note"]


def test_mcp_says_nothing_of_a_library_with_no_pdf_refused(tmp_path: Path) -> None:
    """The control: no field when nothing was set aside."""
    from bmlibrarian_lite.fulltext_discovery import FulltextResult
    from bmlibrarian_lite.mcp_server import _handle_fulltext
    from tests.test_mcp_server import _make_context

    ctx = _make_context()
    ctx.fulltext_discoverer.discover_fulltext.return_value = FulltextResult(
        success=True, source_type=FulltextSourceType.EUROPEPMC_XML, markdown_content="# A",
    )
    ctx.interrogation_agent.load_document.return_value = "pmid-1"

    assert "library_pdf_note" not in _handle_fulltext({"pmid": "1"}, ctx)


def test_the_transparency_report_tells_of_a_pdf_set_aside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caveat on the report, beside the text the chain then found."""
    from bmlibrarian_lite import fulltext_discovery
    from bmlibrarian_lite.study_transparency_analyzer.study_transparency_analyzer import (
        StudyTransparencyAnalyzer,
        TransparencyReport,
    )

    result = _refused_result(tmp_path, success=True)
    monkeypatch.setattr(fulltext_discovery.FulltextDiscoverer, "discover_fulltext", lambda *_a, **_k: result)
    report = TransparencyReport()
    report.doi = "10.1/abc"

    text = StudyTransparencyAnalyzer(email="test@example.com")._discover_fulltext(report)

    assert text == "# Article\nContent."
    assert result.library_pdf_note in report.warnings
