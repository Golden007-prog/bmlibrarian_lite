"""The Playwright fallback is gone (#483); the browser rescue replaces it (#480)."""

import inspect

import pytest

from bmlibrarian_lite import pdf_discovery
from bmlibrarian_lite.fulltext_discovery import FulltextDiscoverer
from bmlibrarian_lite.pdf_discovery import PDFDiscoverer
from bmlibrarian_lite.study_transparency_analyzer import study_transparency_analyzer as sta


@pytest.mark.parametrize("cls", [PDFDiscoverer, FulltextDiscoverer, sta.StudyTransparencyAnalyzer])
def test_no_discovery_takes_a_browser_fallback(cls: type) -> None:
    """No discovery class accepts the retired browser parameters."""
    parameters = inspect.signature(cls).parameters
    assert "use_browser_fallback" not in parameters
    assert "browser_headless" not in parameters


@pytest.mark.parametrize(
    "name", ["BrowserSession", "get_browser_session", "close_browser_session"]
)
def test_the_playwright_session_is_gone(name: str) -> None:
    """The Playwright session and its download method no longer exist."""
    assert not hasattr(pdf_discovery, name)
    assert not hasattr(PDFDiscoverer, "_try_browser_download")


def test_the_analyser_cli_has_no_browser_flag() -> None:
    """The analyser CLI no longer offers ``--no-browser``."""
    assert "--no-browser" not in inspect.getsource(sta)
