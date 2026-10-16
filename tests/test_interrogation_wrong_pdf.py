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


def test_a_download_set_aside_during_a_citation_load_is_told(
    qapp: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """The full-text worker's notice reaches the chat; its failure's error reaches only the log."""
    from tests.test_interrogation_degradation import StubFulltextWorker, StubPdfWorker

    monkeypatch.setattr(tab_module, "FulltextDiscoveryWorker", StubFulltextWorker)
    monkeypatch.setattr(tab_module, "PDFDiscoveryWorker", StubPdfWorker)
    widget = make_tab(tmp_path, monkeypatch)
    widget.load_from_citation(make_citation())

    widget._fulltext_worker.pdf_set_aside.emit("A PDF downloaded for this article was set aside.")

    assert widget._bubbles.last == "A PDF downloaded for this article was set aside."


def test_fetch_pdf_s_download_set_aside_is_told(
    qapp: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """*Fetch PDF*'s worker notice reaches the chat: its error names no set-aside file."""
    from tests.test_interrogation_degradation import StubPdfWorker

    monkeypatch.setattr(tab_module, "PDFDiscoveryWorker", StubPdfWorker)
    widget = make_tab(tmp_path, monkeypatch)
    monkeypatch.setattr(widget, "_create_progress_dialog", lambda _title: _NoWorker())
    widget._start_pdf_discovery({"doi": "10.1/x", "title": TITLE}, TITLE)

    widget._pdf_worker.pdf_set_aside.emit("A PDF downloaded for this article was set aside.")

    assert widget._bubbles.last == "A PDF downloaded for this article was set aside."


def test_a_superseded_citation_load_s_set_aside_pdf_is_told_with_its_article(
    qapp: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """The reader opened another citation: the note still comes, naming the article it is about."""
    from tests.test_interrogation_degradation import StubFulltextWorker, StubPdfWorker

    monkeypatch.setattr(tab_module, "FulltextDiscoveryWorker", StubFulltextWorker)
    monkeypatch.setattr(tab_module, "PDFDiscoveryWorker", StubPdfWorker)
    widget = make_tab(tmp_path, monkeypatch)
    widget.load_from_citation(make_citation())
    earlier = widget._fulltext_worker
    widget._supersede_fulltext_discovery()

    earlier.pdf_set_aside.emit("A PDF downloaded for this article was set aside.")

    assert widget._bubbles.last.startswith("For the article opened before")
    assert TITLE in widget._bubbles.last
    assert widget._bubbles.last.endswith("A PDF downloaded for this article was set aside.")


def test_deleting_a_kept_pdf_withdraws_its_keep(
    qapp: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """*Wrong PDF → Delete*: the marker goes too, so the same bytes downloaded again are checked."""
    from bmlibrarian_lite.pdf_library_check import kept_marker_path, mark_kept

    widget = make_tab(tmp_path, monkeypatch)
    pdf = _pdf(tmp_path / "a.pdf", "Renal physiology in the aged rat")
    mark_kept(pdf)

    assert widget._delete_pdf(pdf)

    assert not pdf.exists()
    assert not kept_marker_path(pdf).exists()


def test_a_keep_that_cannot_be_withdrawn_leaves_the_pdf_and_says_so(
    qapp: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """A marker that cannot be removed: nothing is deleted, and the reader is shown why."""
    from bmlibrarian_lite.pdf_library_check import mark_kept

    widget = make_tab(tmp_path, monkeypatch)
    pdf = _pdf(tmp_path / "a.pdf", "Renal physiology in the aged rat")
    mark_kept(pdf)

    def refuse(_path: Path) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(tab_module, "forget_kept", refuse)

    assert not widget._delete_pdf(pdf)

    assert pdf.exists()
    assert tab_module.QMessageBox.warning.called
