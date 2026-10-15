# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Whether a PDF is the article it was fetched for, judged by its title (#532).

A fetched PDF can be another article: a publisher's script redirects, a
reader downloads a supplement, a DOI resolves to the wrong file. Its text
would then feed the article's transparency rating. The check asks whether the
first page carries the title: the share of the title's adjacent word pairs,
in order, that the page also has. Single words are not enough: an article on
the same subject shares most of them (see ``PDF_TITLE_MIN_PAIR_SHARE``).

A check can also find nothing to go on: a title with no words, a page with
no text (a scan), or a file that is no readable PDF. Those are outcomes of
their own, never a match or a mismatch; each caller decides what they mean.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .constants import PDF_TITLE_MIN_PAIR_SHARE, PDF_TITLE_PAGES_READ

logger = logging.getLogger(__name__)

#: Markup a stored title may carry (``<i>E. coli</i>``): not words of the title.
_TAG = re.compile(r"<[^>]+>")
#: A word hyphenated over a line break, as a PDF's text layer gives it.
_LINE_BREAK_HYPHEN = re.compile(r"(\w)-[ \t]*\r?\n\s*(\w)")
#: A word: a run of letters and digits. Hyphens and punctuation separate words.
_WORD = re.compile(r"[^\W_]+")


class TitleCheckOutcome(Enum):
    """What checking a PDF against a title found."""

    #: The pages read carry the title.
    MATCHES = "matches"
    #: The pages read have text, and too little of the title.
    MISMATCH = "mismatch"
    #: The title has no words: there is nothing to check against.
    NO_TITLE = "no_title"
    #: The pages read have no text (a scan): nothing to check.
    NO_TEXT = "no_text"
    #: The file could not be read as a PDF.
    UNREADABLE = "unreadable"


#: The outcomes that compared a title with text, and so carry a share.
_COMPARED = frozenset({TitleCheckOutcome.MATCHES, TitleCheckOutcome.MISMATCH})


@dataclass(frozen=True)
class TitleCheck:
    """What checking a PDF against a title found.

    Attributes:
        outcome: What it found.
        share: The share of the title's word pairs the pages carry, from 0
            to 1, when text was compared; ``None`` otherwise.

    Raises:
        ValueError: On construction, for a share missing from a comparison,
            given for anything else, or outside 0 to 1.
    """

    outcome: TitleCheckOutcome
    share: float | None = None

    def __post_init__(self) -> None:
        """Hold a share exactly when text was compared."""
        compared = self.outcome in _COMPARED
        if compared != (self.share is not None):
            raise ValueError(
                f"A {self.outcome.value!r} check {'needs' if compared else 'takes no'} share"
            )
        if self.share is not None and not 0.0 <= self.share <= 1.0:
            raise ValueError(f"A share runs from 0 to 1, not {self.share!r}")


def _words(text: str) -> list[str]:
    """Text as words, compared alike whatever the source's encoding.

    NFKC folds ligatures (``ﬀ`` to ``ff``) and compatibility forms, and the
    text is case-folded.

    Args:
        text: Any text.

    Returns:
        Its words, in order.
    """
    return _WORD.findall(unicodedata.normalize("NFKC", text).casefold())


def _title_words(title: str) -> list[str]:
    """A stored title's words, without its markup.

    Args:
        title: The title as stored.

    Returns:
        Its words, in order.
    """
    return _words(_TAG.sub(" ", title))


def _pairs(words: list[str]) -> set[tuple[str, str]]:
    """Each word with the one after it.

    Args:
        words: Words in order.

    Returns:
        The adjacent pairs.
    """
    return set(zip(words, words[1:], strict=False))


def title_pair_share(page_text: str, title: str) -> float | None:
    """The share of a title's adjacent word pairs that the page text also has.

    The page is read twice over: once with a word hyphenated at a line break
    joined (``hyper-``/``tension``), once with it split (``SARS-``/``CoV-2``),
    since the text layer cannot say which hyphen was the author's. A
    one-word title counts that word.

    Args:
        page_text: The text of the pages read.
        title: The article's title, as stored; markup is not counted.

    Returns:
        From 0 to 1; ``None`` when the title has no words.
    """
    title_words = _title_words(title)
    if not title_words:
        return None
    joined = _words(_LINE_BREAK_HYPHEN.sub(r"\1\2", page_text))
    split = _words(page_text)
    if len(title_words) == 1:
        return 1.0 if title_words[0] in set(joined) | set(split) else 0.0
    wanted = [*zip(title_words, title_words[1:], strict=False)]
    have = _pairs(joined) | _pairs(split)
    return sum(1 for pair in wanted if pair in have) / len(wanted)


def judge_title(page_text: str, title: str) -> TitleCheck:
    """Whether the text of a PDF's first pages carries an article's title.

    Args:
        page_text: The text of the pages read.
        title: The article's title.

    Returns:
        ``NO_TITLE`` when the title has no words, ``NO_TEXT`` when the pages
        have none; otherwise ``MATCHES`` when at least
        ``PDF_TITLE_MIN_PAIR_SHARE`` of the title's word pairs are there, and
        ``MISMATCH`` when fewer are, each with the share.
    """
    share = title_pair_share(page_text, title)
    if share is None:
        return TitleCheck(TitleCheckOutcome.NO_TITLE)
    if not _words(page_text):
        return TitleCheck(TitleCheckOutcome.NO_TEXT)
    if share >= PDF_TITLE_MIN_PAIR_SHARE:
        return TitleCheck(TitleCheckOutcome.MATCHES, share)
    return TitleCheck(TitleCheckOutcome.MISMATCH, share)


def first_pages_text(pdf_path: Path, pages: int = PDF_TITLE_PAGES_READ) -> str:
    """The text of a PDF's first pages.

    Args:
        pdf_path: The PDF.
        pages: How many pages to read from the start.

    Returns:
        Their text, joined; empty for a PDF with no pages or no text layer.

    Raises:
        Exception: Whatever PyMuPDF raises for a file it cannot read: a
            ``RuntimeError`` for one that is no PDF or is empty or missing,
            a ``ValueError`` for an encrypted one, its own ``FzErrorBase``
            (an ``Exception``) for damage. The set is not documented.
    """
    import fitz  # type: ignore[import-untyped]  # PyMuPDF, imported here as pdf_utils does

    with fitz.open(str(pdf_path)) as document:
        return "".join(document[i].get_text() for i in range(min(pages, document.page_count)))


def check_pdf_title(pdf_path: Path, title: str) -> TitleCheck:
    """Whether a PDF's first page carries an article's title.

    A title with no words is not checked, so the file is not opened for it.

    Args:
        pdf_path: The PDF.
        title: The article's title.

    Returns:
        The check; ``UNREADABLE`` when the file cannot be read as a PDF
        (logged).
    """
    if not _title_words(title):
        return TitleCheck(TitleCheckOutcome.NO_TITLE)
    try:
        text = first_pages_text(pdf_path)
    except Exception:  # noqa: BLE001
        # PyMuPDF wraps a C library and documents no closed set of errors
        # (first_pages_text); a file from the network it cannot read, for
        # whatever reason, is unreadable, and must not escape a Qt slot
        logger.warning("%s could not be read to check its title", pdf_path.name, exc_info=True)
        return TitleCheck(TitleCheckOutcome.UNREADABLE)
    return judge_title(text, title)
