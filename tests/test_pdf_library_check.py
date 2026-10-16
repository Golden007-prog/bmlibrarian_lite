"""A PDF in the library is read as its article's only if it carries the title (#541).

Ten of 25 PDFs in a real desktop library were other articles, and the
cached-PDF tier read every one as the article it was filed under. A PDF whose
first page with text lacks the title is now set aside beside itself (never
deleted) and not read; a PDF a person kept, whose bytes its marker names, is
trusted as it is.
"""

from pathlib import Path

import fitz
import pytest

from bmlibrarian_lite.constants import PDF_KEPT_SUFFIX, PDF_MISMATCH_SUFFIX
from bmlibrarian_lite.pdf_library_check import (
    CachedPdfOutcome,
    check_cached_pdf,
    is_kept,
    keep_pdf,
    kept_marker_path,
    mark_kept,
    set_aside,
    set_aside_note,
)
from bmlibrarian_lite.pdf_title_check import TitleCheck, TitleCheckOutcome

TITLE = "Coffee and caffeine consumption and the risk of hypertension in postmenopausal women"
OTHER = "Scientific opinion on the safety of bovine colostrum filtration"


def write_pdf(path: Path, *pages: str) -> Path:
    """A real PDF with one page per text given; an empty text leaves a page blank."""
    document = fitz.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_textbox(fitz.Rect(50, 50, 550, 800), text)
    document.save(str(path))
    document.close()
    return path


# -- the check ------------------------------------------------------------------


def test_a_pdf_carrying_the_title_is_read(tmp_path: Path) -> None:
    """The control: the article's own PDF stays where it is."""
    pdf = write_pdf(tmp_path / "a.pdf", TITLE)

    found = check_cached_pdf(pdf, TITLE)

    assert found.outcome is CachedPdfOutcome.USABLE
    assert found.path == pdf
    assert found.check == TitleCheck(TitleCheckOutcome.MATCHES, 1.0)
    assert pdf.is_file()


def test_another_article_s_pdf_is_set_aside_and_not_read(tmp_path: Path) -> None:
    """Renamed beside itself, not deleted: the cache no longer finds it."""
    pdf = write_pdf(tmp_path / "a.pdf", OTHER)
    original = pdf.read_bytes()

    found = check_cached_pdf(pdf, TITLE)

    assert found.outcome is CachedPdfOutcome.SET_ASIDE
    assert found.set_aside_to == tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}"
    assert found.check is not None and found.check.outcome is TitleCheckOutcome.MISMATCH
    assert not pdf.exists()
    assert found.set_aside_to.read_bytes() == original


@pytest.mark.parametrize(
    ("pages", "title"),
    [
        (("",), TITLE),  # a scan: no text to judge by
        ((OTHER,), ""),  # no title to judge against
        ((OTHER,), "[Coffee and the heart in older women]."),  # PubMed's translation
    ],
)
def test_a_pdf_whose_title_cannot_be_judged_is_read(
    tmp_path: Path, pages: tuple[str, ...], title: str
) -> None:
    """Only a mismatch is refused: a check that judged nothing leaves the PDF be."""
    pdf = write_pdf(tmp_path / "a.pdf", *pages)

    found = check_cached_pdf(pdf, title)

    assert found.outcome is CachedPdfOutcome.USABLE
    assert pdf.is_file()


def test_a_pdf_with_no_title_given_is_read_unchecked(tmp_path: Path) -> None:
    """``None`` for a title checks nothing, and the file is not opened."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"not a pdf at all")

    found = check_cached_pdf(pdf, None)

    assert found.outcome is CachedPdfOutcome.USABLE
    assert found.check is None


def test_an_unreadable_pdf_is_left_for_the_reader_to_find_unreadable(tmp_path: Path) -> None:
    """Not a mismatch: the extraction that follows reports it as unread (#354)."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.7\n broken")

    found = check_cached_pdf(pdf, TITLE)

    assert found.outcome is CachedPdfOutcome.USABLE
    assert found.check == TitleCheck(TitleCheckOutcome.UNREADABLE)
    assert pdf.is_file()


def test_a_pdf_that_cannot_be_set_aside_is_still_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed rename leaves the file in place, and it is refused all the same."""
    pdf = write_pdf(tmp_path / "a.pdf", OTHER)

    def refuse(self: Path, target: Path) -> Path:
        raise PermissionError("read-only library")

    monkeypatch.setattr(Path, "rename", refuse)

    found = check_cached_pdf(pdf, TITLE)

    assert found.outcome is CachedPdfOutcome.NOT_SET_ASIDE
    assert not found.usable
    assert found.set_aside_to is None
    assert pdf.is_file()


# -- setting aside --------------------------------------------------------------


def test_setting_aside_never_overwrites_an_earlier_one(tmp_path: Path) -> None:
    """A second, different PDF goes to a numbered name; the first is kept."""
    first = write_pdf(tmp_path / "a.pdf", OTHER)
    first_bytes = first.read_bytes()
    assert set_aside(first) == tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}"

    second = write_pdf(tmp_path / "a.pdf", "Another article altogether, on renal physiology")
    second_bytes = second.read_bytes()
    moved = set_aside(second)

    assert moved == tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}-2"
    assert (tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}").read_bytes() == first_bytes
    assert moved.read_bytes() == second_bytes


def test_the_same_pdf_set_aside_again_is_not_kept_twice(tmp_path: Path) -> None:
    """Each run downloads the same wrong PDF: one copy is kept, not one per run."""
    pdf = write_pdf(tmp_path / "a.pdf", OTHER)
    data = pdf.read_bytes()
    assert set_aside(pdf) == tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}"

    (tmp_path / "a.pdf").write_bytes(data)
    assert set_aside(tmp_path / "a.pdf") == tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}"

    assert not (tmp_path / "a.pdf").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"a.pdf{PDF_MISMATCH_SUFFIX}"]


# -- a person's keep ------------------------------------------------------------


def test_a_pdf_a_person_kept_is_read_though_it_lacks_the_title(tmp_path: Path) -> None:
    """A retitled preprint, a scan with garbled text: the person's word stands."""
    pdf = write_pdf(tmp_path / "a.pdf", OTHER)
    mark_kept(pdf)

    found = check_cached_pdf(pdf, TITLE)

    assert kept_marker_path(pdf) == tmp_path / f"a.pdf{PDF_KEPT_SUFFIX}"
    assert found.outcome is CachedPdfOutcome.USABLE
    assert found.check is None
    assert pdf.is_file()


def test_a_marker_vouches_for_the_bytes_kept_and_no_others(tmp_path: Path) -> None:
    """A later download in the kept PDF's place is checked as any other."""
    pdf = write_pdf(tmp_path / "a.pdf", "Another article altogether, on renal physiology")
    mark_kept(pdf)
    write_pdf(pdf, OTHER)

    assert not is_kept(pdf)
    assert check_cached_pdf(pdf, TITLE).outcome is CachedPdfOutcome.SET_ASIDE


def test_no_marker_is_no_keep(tmp_path: Path) -> None:
    """The control: a PDF nobody kept is not kept."""
    assert not is_kept(write_pdf(tmp_path / "a.pdf", OTHER))


def test_an_unreadable_marker_is_no_keep(tmp_path: Path) -> None:
    """A marker that cannot be read vouches for nothing (logged)."""
    pdf = write_pdf(tmp_path / "a.pdf", OTHER)
    kept_marker_path(pdf).mkdir()

    assert not is_kept(pdf)


def test_keeping_a_set_aside_pdf_puts_it_back_and_marks_it(tmp_path: Path) -> None:
    """``bmll keep-pdf``'s path: the person's override of a set-aside PDF."""
    pdf = write_pdf(tmp_path / "a.pdf", OTHER)
    moved = set_aside(pdf)

    kept = keep_pdf(moved)

    assert kept == pdf
    assert pdf.is_file() and not moved.exists()
    assert is_kept(pdf)
    assert check_cached_pdf(pdf, TITLE).outcome is CachedPdfOutcome.USABLE


def test_keeping_a_numbered_set_aside_pdf_puts_it_back(tmp_path: Path) -> None:
    """``a.pdf.mismatch-2`` goes back to ``a.pdf`` as the first does."""
    moved = tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}-2"
    write_pdf(moved, OTHER)

    assert keep_pdf(moved) == tmp_path / "a.pdf"
    assert is_kept(tmp_path / "a.pdf")


def test_keeping_a_pdf_in_place_marks_it(tmp_path: Path) -> None:
    """A PDF that is no set-aside one is marked where it is."""
    pdf = write_pdf(tmp_path / "a.pdf", OTHER)

    assert keep_pdf(pdf) == pdf
    assert is_kept(pdf)


def test_keeping_never_overwrites_a_pdf_in_the_way(tmp_path: Path) -> None:
    """A PDF already at the article's name is not replaced by the one kept."""
    pdf = write_pdf(tmp_path / "a.pdf", OTHER)
    moved = set_aside(pdf)
    write_pdf(pdf, TITLE)
    in_the_way = pdf.read_bytes()

    with pytest.raises(FileExistsError):
        keep_pdf(moved)

    assert pdf.read_bytes() == in_the_way
    assert moved.is_file()


@pytest.mark.parametrize("name", ["a.txt", "a.pdf.held", "a.pdf.kept"])
def test_only_a_pdf_or_a_set_aside_one_is_kept(tmp_path: Path, name: str) -> None:
    """Anything else is refused: a marker for a file the cache never reads."""
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.7\n")

    with pytest.raises(ValueError):
        keep_pdf(path)


# -- the reader's sentence ------------------------------------------------------


def test_the_reader_is_told_where_the_pdf_went_and_how_to_keep_it(tmp_path: Path) -> None:
    """The share, the new name and the override, in one sentence set."""
    pdf = write_pdf(tmp_path / "a.pdf", OTHER)
    found = check_cached_pdf(pdf, TITLE)

    note = set_aside_note(found)

    assert note is not None
    assert "does not carry this article's title" in note
    assert f"a.pdf{PDF_MISMATCH_SUFFIX}" in note
    assert "bmll keep-pdf" in note


def test_the_reader_is_told_a_pdf_that_could_not_be_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not read, and not moved: said as such, naming the file."""
    pdf = write_pdf(tmp_path / "a.pdf", OTHER)
    monkeypatch.setattr(Path, "rename", lambda self, target: (_ for _ in ()).throw(OSError("x")))

    note = set_aside_note(check_cached_pdf(pdf, TITLE))

    assert note is not None
    assert "could not be moved aside" in note
    assert "a.pdf" in note


def test_a_usable_pdf_has_nothing_to_tell(tmp_path: Path) -> None:
    """The control: no sentence for a PDF that is read."""
    assert set_aside_note(check_cached_pdf(write_pdf(tmp_path / "a.pdf", TITLE), TITLE)) is None


# -- the PR review's findings -----------------------------------------------------


def test_an_unreadable_earlier_set_aside_does_not_block_a_later_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The next name is taken, rather than leaving the PDF in the library for good."""
    import bmlibrarian_lite.pdf_library_check as module

    first = tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}"
    write_pdf(first, OTHER)
    pdf = write_pdf(tmp_path / "a.pdf", "Renal physiology in the aged rat")
    real = module.pdf_digest

    def unreadable_first(path: Path) -> str:
        if path == first:
            raise PermissionError("unreadable")
        return real(path)

    monkeypatch.setattr(module, "pdf_digest", unreadable_first)

    assert set_aside(pdf) == tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}-2"
    assert first.is_file()


def test_setting_aside_a_staged_download_names_it_after_the_cache_path(tmp_path: Path) -> None:
    """A download waiting for its check goes to the PDF's set-aside name; the PDF stays."""
    pdf = write_pdf(tmp_path / "a.pdf", TITLE)
    staged = write_pdf(tmp_path / "a.pdf.unchecked", OTHER)

    moved = set_aside(pdf, contents=staged)

    assert moved == tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}"
    assert not staged.exists()
    assert pdf.is_file()


def test_a_keep_whose_marker_cannot_be_written_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Marked before the rename: a failed marker leaves the set-aside PDF where it was."""
    moved = set_aside(write_pdf(tmp_path / "a.pdf", OTHER))

    def refuse(self: Path, data: str, encoding: str | None = None) -> int:
        raise PermissionError("read-only library")

    monkeypatch.setattr(Path, "write_text", refuse)

    with pytest.raises(PermissionError):
        keep_pdf(moved)

    assert moved.is_file()
    assert not (tmp_path / "a.pdf").exists()


def test_a_marker_vouches_for_a_download_of_the_bytes_kept(tmp_path: Path) -> None:
    """The same bytes downloaded again are the person's kept PDF."""
    pdf = write_pdf(tmp_path / "a.pdf", OTHER)
    mark_kept(pdf)
    again = tmp_path / "a.pdf.unchecked"
    again.write_bytes(pdf.read_bytes())

    assert is_kept(pdf, contents=again)


def test_a_pdf_that_could_not_be_moved_is_told_with_the_keep_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reader can still keep it in place."""
    pdf = write_pdf(tmp_path / "a.pdf", OTHER)
    monkeypatch.setattr(Path, "rename", lambda self, target: (_ for _ in ()).throw(OSError("x")))

    note = set_aside_note(check_cached_pdf(pdf, TITLE))

    assert note is not None and f'bmll keep-pdf "{pdf}"' in note


def test_downloads_set_aside_are_told_with_the_keep_command(tmp_path: Path) -> None:
    """One or more, each named; none, nothing said."""
    from bmlibrarian_lite.pdf_library_check import set_aside_download_note

    one = tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}"
    two = tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}-2"

    assert set_aside_download_note(()) is None
    single = set_aside_download_note((one,))
    assert single is not None and one.name in single and f'bmll keep-pdf "{one}"' in single
    both = set_aside_download_note((one, two))
    assert both is not None and one.name in both and two.name in both


# -- the second review's findings ------------------------------------------------


def test_forget_kept_removes_the_marker(tmp_path: Path) -> None:
    """The keep is withdrawn: the PDF is no longer kept."""
    from bmlibrarian_lite.pdf_library_check import forget_kept

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.7\nkept")
    mark_kept(pdf)

    forget_kept(pdf)

    assert not is_kept(pdf)
    assert not kept_marker_path(pdf).exists()


def test_forget_kept_of_a_pdf_never_kept_is_nothing(tmp_path: Path) -> None:
    """The control: no marker, no error."""
    from bmlibrarian_lite.pdf_library_check import forget_kept

    forget_kept(tmp_path / "a.pdf")


def test_keep_refused_for_a_pdf_in_the_way_says_what_to_do(tmp_path: Path) -> None:
    """The reader is told to move the PDF in the way, not only that it is there."""
    moved = tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}"
    moved.write_bytes(b"%PDF-1.7\nset aside")
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.7\nscan")

    with pytest.raises(FileExistsError, match="move it out of") as raised:
        keep_pdf(moved)

    assert str(tmp_path) in str(raised.value)


def test_several_set_aside_downloads_each_have_their_keep_command(tmp_path: Path) -> None:
    """Every file is named with the command that keeps it, not the first alone."""
    from bmlibrarian_lite.pdf_library_check import set_aside_download_note

    paths = (tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}", tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}-2")

    note = set_aside_download_note(paths) or ""

    for path in paths:
        assert f'bmll keep-pdf "{path}"' in note
