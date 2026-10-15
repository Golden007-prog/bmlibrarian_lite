"""Whether a PDF is the article it was fetched for, judged by its title (#532)."""

from pathlib import Path

import fitz
import pytest

from bmlibrarian_lite.constants import PDF_TITLE_MIN_PAIR_SHARE
from bmlibrarian_lite.pdf_title_check import (
    TitleCheck,
    TitleCheckOutcome,
    check_pdf_title,
    judge_title,
    title_pair_share,
)

TITLE = "Caffeine and blood pressure: a critical review perspective."


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


# -- the judgement ----------------------------------------------------------------


def test_the_title_on_the_page_matches() -> None:
    """The control: a first page that opens with the title."""
    page = "Journal of Hypertension\nCaffeine and blood pressure: a critical review\nperspective\nA. Author"
    assert judge_title(page, TITLE) == TitleCheck(TitleCheckOutcome.MATCHES, 1.0)


def test_the_same_words_out_of_order_do_not_match() -> None:
    """Another article on the subject shares the words, not the title.

    The single-word check this replaced passed this page: five of the six
    significant words are on it.
    """
    page = (
        "Coffee and arterial hypertension. A review of how caffeine affects blood flow; "
        "pressure readings are critical."
    )
    check = judge_title(page, TITLE)
    assert check.outcome is TitleCheckOutcome.MISMATCH
    assert check.share is not None and check.share < PDF_TITLE_MIN_PAIR_SHARE


def test_a_short_title_sharing_one_term_does_not_match() -> None:
    """The survey's weak spot: ``SARS-CoV-2`` alone is two word pairs."""
    page = "Presence and vitality of SARS-CoV-2 virus in wastewaters and rivers"
    check = judge_title(page, "Molecular Epidemiology of SARS-CoV-2 in Cyprus")
    assert check.outcome is TitleCheckOutcome.MISMATCH


def test_a_ligature_reads_as_its_letters() -> None:
    """PDF text layers often carry ``ﬀ``, ``ﬁ``: NFKC folds them."""
    page = "Diﬀerences in eﬀective reproduction numbers across states"
    assert title_pair_share(page, "Differences in effective reproduction numbers") == 1.0


@pytest.mark.parametrize(
    ("page", "title"),
    [
        ("CO₂ emissions from hospital care", "CO2 emissions from hospital care"),
        ("A phase Ⅱ trial of metformin", "A phase II trial of metformin"),
    ],
)
def test_compatibility_forms_read_as_their_letters(page: str, title: str) -> None:
    """Subscripts and Roman numeral signs, which case folding alone keeps apart."""
    assert title_pair_share(page, title) == 1.0


def test_a_word_hyphenated_over_a_line_break_is_joined() -> None:
    """A typesetter's hyphen is no word boundary."""
    page = "Isolated systolic hyper-\ntension in older adults"
    assert title_pair_share(page, "Isolated systolic hypertension in older adults") == 1.0


def test_an_author_s_hyphen_at_a_line_break_still_splits() -> None:
    """The text layer cannot tell the hyphens apart, so both readings count."""
    page = "Antibodies against SARS-\nCoV-2 in health workers"
    assert title_pair_share(page, "Antibodies against SARS-CoV-2 in health workers") == 1.0


def test_case_and_punctuation_do_not_count() -> None:
    """A title page in capitals, with other punctuation, carries the title."""
    page = "CAFFEINE AND BLOOD PRESSURE — A CRITICAL REVIEW PERSPECTIVE"
    assert title_pair_share(page, TITLE) == 1.0


def test_markup_in_a_stored_title_is_not_a_word() -> None:
    """``<i>E. coli</i>`` is the words E and coli."""
    page = "Resistance of E. coli isolates from urinary tract infections"
    assert title_pair_share(page, "Resistance of <i>E. coli</i> isolates") == 1.0


def test_a_one_word_title_counts_the_word() -> None:
    """A title with no pair is checked by its one word, as a word, not a substring."""
    assert title_pair_share("Inflation in the early universe", "Inflation.") == 1.0
    assert title_pair_share("Inflationary models", "Inflation.") == 0.0


@pytest.mark.parametrize("title", ["", "   ", "<i></i>", "—: ."])
def test_a_title_with_no_words_is_not_checked(title: str) -> None:
    """Nothing to check against is no mismatch."""
    assert judge_title("Some article text", title) == TitleCheck(TitleCheckOutcome.NO_TITLE)
    assert title_pair_share("Some article text", title) is None


@pytest.mark.parametrize("page", ["", "  \n\t ", "\x0c"])
def test_a_page_with_no_text_is_not_checked(page: str) -> None:
    """A scan's page has no text layer: no mismatch, and no match either."""
    assert judge_title(page, TITLE) == TitleCheck(TitleCheckOutcome.NO_TEXT)


def test_the_bar_is_inclusive() -> None:
    """Exactly the bar's share of the pairs matches."""
    # Four pairs, two on the page: "alpha beta", "gamma delta"
    title = "alpha beta gamma delta epsilon"
    check = judge_title("alpha beta and gamma delta", title)
    assert check.share == 0.5 == PDF_TITLE_MIN_PAIR_SHARE
    assert check.outcome is TitleCheckOutcome.MATCHES


@pytest.mark.parametrize(
    ("outcome", "share"),
    [
        (TitleCheckOutcome.MATCHES, None),
        (TitleCheckOutcome.MISMATCH, None),
        (TitleCheckOutcome.NO_TEXT, 0.0),
        (TitleCheckOutcome.NO_TITLE, 1.0),
        (TitleCheckOutcome.UNREADABLE, 0.5),
        (TitleCheckOutcome.MATCHES, 1.5),
        (TitleCheckOutcome.MISMATCH, -0.1),
    ],
)
def test_a_check_no_comparison_reaches_is_refused(outcome: TitleCheckOutcome, share: float | None) -> None:
    """A share exactly when text was compared, from 0 to 1."""
    with pytest.raises(ValueError):
        TitleCheck(outcome, share)


# -- reading the PDF ------------------------------------------------------------


def test_a_pdf_carrying_the_title_matches(tmp_path: Path) -> None:
    """The control, through a real PDF."""
    pdf = write_pdf(tmp_path / "a.pdf", "Caffeine and blood pressure: a critical review perspective\n\nAbstract")
    assert check_pdf_title(pdf, TITLE).outcome is TitleCheckOutcome.MATCHES


def test_another_article_s_pdf_does_not_match(tmp_path: Path) -> None:
    """One of the wrong PDFs the survey found in a real library, by its first lines."""
    pdf = write_pdf(
        tmp_path / "a.pdf",
        "Using Effect Size - or Why the P Value Is Not Enough\nGail M. Sullivan, Richard Feinn",
    )
    check = check_pdf_title(
        pdf, "Acute Effects of Coffee Consumption on Blood Pressure and Endothelial Function"
    )
    assert check.outcome is TitleCheckOutcome.MISMATCH


def test_only_the_first_page_is_read(tmp_path: Path) -> None:
    """A title further in (a cited article, an issue's contents) is not this PDF's."""
    pdf = write_pdf(tmp_path / "a.pdf", "Supplementary table S1", TITLE)
    assert check_pdf_title(pdf, TITLE).outcome is TitleCheckOutcome.MISMATCH


def test_a_scan_is_not_checked(tmp_path: Path) -> None:
    """A PDF whose first page has no text layer."""
    pdf = write_pdf(tmp_path / "a.pdf", "")
    assert check_pdf_title(pdf, TITLE) == TitleCheck(TitleCheckOutcome.NO_TEXT)


@pytest.mark.parametrize("body", [b"%PDF-1.4\n" + b"0" * 100, b""])
def test_a_file_that_is_no_readable_pdf_is_unreadable(tmp_path: Path, body: bytes) -> None:
    """A truncated or empty file says nothing of the title."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(body)
    assert check_pdf_title(pdf, TITLE) == TitleCheck(TitleCheckOutcome.UNREADABLE)


def test_an_encrypted_pdf_is_unreadable(tmp_path: Path) -> None:
    """PyMuPDF raises ValueError, not RuntimeError, for its page: still unreadable, never raised."""
    document = fitz.open()
    document.new_page().insert_text((50, 72), TITLE)
    pdf = tmp_path / "a.pdf"
    document.save(str(pdf), encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner")
    document.close()
    assert check_pdf_title(pdf, TITLE) == TitleCheck(TitleCheckOutcome.UNREADABLE)


def test_a_missing_file_is_unreadable(tmp_path: Path) -> None:
    """Gone before it was read: unreadable, not a mismatch."""
    assert check_pdf_title(tmp_path / "gone.pdf", TITLE) == TitleCheck(TitleCheckOutcome.UNREADABLE)


def test_no_title_opens_no_file(tmp_path: Path) -> None:
    """With nothing to check against, the file is not read, readable or not."""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"not a pdf")
    assert check_pdf_title(pdf, "") == TitleCheck(TitleCheckOutcome.NO_TITLE)


# -- the download paths' warning shares the check -------------------------------


@pytest.mark.parametrize(
    ("pages", "title", "expected"),
    [
        (("Caffeine and blood pressure: a critical review perspective",), TITLE, (True, None)),
        (("Some PDF",), "", (True, None)),
        (
            ("Using Effect Size - or Why the P Value Is Not Enough",),
            TITLE,
            (False, "PDF content may not match expected document. Title match: 0%"),
        ),
        (("",), TITLE, (False, "PDF contains no extractable text")),
    ],
)
def test_the_download_warning_reads_the_check(
    tmp_path: Path, pages: tuple[str, ...], title: str, expected: tuple[bool, str | None]
) -> None:
    """A title with no words is no longer a 0% mismatch; a mismatch says its share."""
    from bmlibrarian_lite.pdf_discovery import PDFDiscoverer

    pdf = write_pdf(tmp_path / "a.pdf", *pages)
    assert PDFDiscoverer()._verify_pdf_content(pdf, title) == expected


def test_the_download_warning_says_when_the_pdf_could_not_be_read(tmp_path: Path) -> None:
    """Unread is not checked: once silent, now said."""
    from bmlibrarian_lite.pdf_discovery import PDFDiscoverer

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"0" * 100)
    assert PDFDiscoverer()._verify_pdf_content(pdf, TITLE) == (
        False,
        "The PDF could not be read to check it against the title",
    )
