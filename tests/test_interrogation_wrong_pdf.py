"""The interrogation tab reads a PDF in the library only if it is the article's (#541).

Both of its cached-PDF loads (a citation, and *Fetch PDF*) read whatever PDF
was filed under the document. One that does not carry the title is now set
aside, the reader is told where it went, and the load looks for the
article's own PDF.
"""

from pathlib import Path
from typing import Any

import fitz
import pytest

pytest.importorskip("PySide6")

from bmlibrarian_lite.constants import PDF_MISMATCH_SUFFIX  # noqa: E402
from bmlibrarian_lite.gui import document_interrogation_tab as tab_module  # noqa: E402
from tests.test_interrogation_degradation import (  # noqa: E402
    make_citation,
    make_tab,
    qapp,  # noqa: F401 -- the fixture
)

#: The title :func:`make_citation`'s document carries.
TITLE = "Aspirin trial"


def _pdf(path: Path, text: str) -> Path:
    """A real PDF whose first page says ``text``."""
    document = fitz.open()
    document.new_page().insert_text((50, 72), text)
    document.save(str(path))
    document.close()
    return path


@pytest.fixture
def library_tab(qapp: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: F811
    """A tab whose library holds one PDF, filed under its citation; discovery recorded, not run."""
    widget = make_tab(tmp_path, monkeypatch)
    widget.cached = tmp_path / "filed.pdf"
    monkeypatch.setattr(tab_module, "find_existing_pdf", lambda _m: widget.cached if widget.cached.exists() else None)
    widget.discovered = []
    monkeypatch.setattr(
        widget, "_start_fulltext_discovery",
        lambda doc, *args, **kwargs: widget.discovered.append(doc),
    )
    widget.loaded = []
    monkeypatch.setattr(
        widget, "_load_citation_pdf", lambda path, *args: widget.loaded.append(path)
    )
    return widget


def test_a_citation_s_pdf_of_another_article_is_set_aside_and_told(library_tab: Any) -> None:
    """Not read; renamed aside; the reader told; the article's own looked for."""
    _pdf(library_tab.cached, "Renal physiology in the aged rat")

    library_tab.load_from_citation(make_citation())

    assert library_tab.loaded == []
    assert not library_tab.cached.exists()
    assert library_tab.cached.with_name(f"filed.pdf{PDF_MISMATCH_SUFFIX}").is_file()
    assert "does not carry this article's title" in library_tab._bubbles.last
    assert len(library_tab.discovered) == 1


def test_a_citation_s_own_pdf_is_read(library_tab: Any) -> None:
    """The control: the article's PDF is loaded from the library, and nothing is said of it."""
    _pdf(library_tab.cached, f"{TITLE}: a randomised controlled study")

    library_tab.load_from_citation(make_citation())

    assert library_tab.loaded == [library_tab.cached]
    assert library_tab.discovered == []
    assert not any("does not carry" in said for said in library_tab._bubbles.said)


def test_fetch_pdf_refuses_a_pdf_of_another_article_in_the_library(
    library_tab: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Fetch PDF*'s path: the library's wrong PDF is not loaded, and discovery runs."""
    _pdf(library_tab.cached, "Renal physiology in the aged rat")
    loaded: list[Path] = []
    monkeypatch.setattr(library_tab, "_load_pdf_file", lambda path, title: loaded.append(path))
    started: list[bool] = []
    monkeypatch.setattr(tab_module, "PDFDiscoveryWorker", lambda *a, **k: started.append(True) or _NoWorker())
    monkeypatch.setattr(library_tab, "_create_progress_dialog", lambda _title: _NoWorker())

    library_tab._start_pdf_discovery({"doi": "10.1/x", "title": TITLE}, TITLE)

    assert loaded == []
    assert started == [True]
    assert "does not carry this article's title" in library_tab._bubbles.last


class _NoWorker:
    """Anything with signals that connect and a start that does nothing."""

    def __getattr__(self, _name: str) -> Any:
        """Every attribute is another stand-in."""
        return _NoWorker()

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        """Calling one does nothing."""
        return None
