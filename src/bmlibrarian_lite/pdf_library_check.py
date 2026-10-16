# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Whether a PDF in the library is read as its article's (#541).

Ten of 25 PDFs in a real desktop library were other articles, and the
cached-PDF tier read each as the article it was filed under, so its text fed
that article's transparency rating. A PDF is now read only if its first page
with text carries the title (:func:`pdf_title_check.check_pdf_title`) or a
person kept it.

A PDF that lacks the title is set aside: renamed beside itself with
:data:`PDF_MISMATCH_SUFFIX`, which the cache never looks for, and never
deleted, since the check can be wrong (a preprint retitled between versions).
A person who says it is the article keeps it (``bmll keep-pdf``, or the
review queue's *Keep this PDF*): a marker beside it, with
:data:`PDF_KEPT_SUFFIX`, holds the PDF's SHA-256, so it vouches for those
bytes alone and a later download in its place is checked again.

A check that judged nothing (no title, no text, a translated title, a file
that cannot be read) leaves the PDF where it is: only a mismatch is refused.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from enum import Enum
from itertools import count
from pathlib import Path

from .constants import PDF_DIGEST_CHUNK_BYTES, PDF_KEPT_SUFFIX, PDF_MISMATCH_SUFFIX
from .pdf_title_check import TitleCheck, TitleCheckOutcome, check_pdf_title

logger = logging.getLogger(__name__)

#: The extension the cache looks for (``pdf_utils.find_existing_pdf``).
_PDF_EXTENSION = ".pdf"
#: A set-aside PDF's name: the PDF's own, the suffix, and a number after the
#: first (``a.pdf.mismatch``, ``a.pdf.mismatch-2``).
_SET_ASIDE_NAME = re.compile(
    rf"^(?P<pdf>.+{re.escape(_PDF_EXTENSION)}){re.escape(PDF_MISMATCH_SUFFIX)}(?:-\d+)?$"
)
#: The reader's command for keeping a set-aside PDF as the article's.
KEEP_PDF_COMMAND = "bmll keep-pdf"


class CachedPdfOutcome(Enum):
    """What checking a PDF in the library came to."""

    #: It is read: it carries the title, a person kept it, or its title
    #: could not be judged.
    USABLE = "usable"
    #: It lacks the title, and was renamed aside: not read.
    SET_ASIDE = "set_aside"
    #: It lacks the title, and could not be renamed (logged): not read.
    NOT_SET_ASIDE = "not_set_aside"


@dataclass(frozen=True)
class CachedPdfCheck:
    """What checking a PDF in the library found, and what became of it.

    Attributes:
        outcome: Whether it is read, or where it went.
        path: The PDF as found.
        check: The title check; ``None`` when none was made (no title given,
            or a person kept it).
        set_aside_to: Where it was renamed, for ``SET_ASIDE`` alone.

    Raises:
        ValueError: On construction, for ``set_aside_to`` given with any
            other outcome or missing from ``SET_ASIDE``, or a refusal without
            a mismatching check.
    """

    outcome: CachedPdfOutcome
    path: Path
    check: TitleCheck | None = None
    set_aside_to: Path | None = None

    def __post_init__(self) -> None:
        """Hold a destination exactly when the PDF was set aside."""
        if (self.outcome is CachedPdfOutcome.SET_ASIDE) != (self.set_aside_to is not None):
            raise ValueError("A PDF set aside, and only one, names where it went")
        if self.outcome is not CachedPdfOutcome.USABLE and (
            self.check is None or self.check.outcome is not TitleCheckOutcome.MISMATCH
        ):
            raise ValueError("Only a PDF that lacks the title is refused")

    @property
    def usable(self) -> bool:
        """Whether the PDF is read as the article's.

        Returns:
            ``True`` for ``USABLE``.
        """
        return self.outcome is CachedPdfOutcome.USABLE


def kept_marker_path(pdf_path: Path) -> Path:
    """Where the marker of a PDF a person kept is.

    Args:
        pdf_path: The PDF.

    Returns:
        ``pdf_path`` with :data:`PDF_KEPT_SUFFIX` appended, beside it.
    """
    return pdf_path.with_name(pdf_path.name + PDF_KEPT_SUFFIX)


def pdf_digest(path: Path) -> str:
    """A file's SHA-256, read a chunk at a time.

    Args:
        path: The file.

    Returns:
        The digest, in hex.

    Raises:
        OSError: When the file cannot be read.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(PDF_DIGEST_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def mark_kept(pdf_path: Path, contents: Path | None = None) -> None:
    """Record that a person kept this PDF as its article's.

    Args:
        pdf_path: The PDF, at the name the cache finds it by.
        contents: The file whose bytes are kept, when they are not yet at
            ``pdf_path`` (a held PDF, marked before it is renamed into
            place); ``pdf_path`` itself when ``None``.

    Raises:
        OSError: When the PDF cannot be read or the marker written.
    """
    digest = pdf_digest(contents if contents is not None else pdf_path)
    kept_marker_path(pdf_path).write_text(digest, encoding="ascii")


def is_kept(pdf_path: Path, contents: Path | None = None) -> bool:
    """Whether a person kept this PDF, as it is now.

    Args:
        pdf_path: The PDF, at the name the cache finds it by.
        contents: Bytes to compare with the marker instead of
            ``pdf_path``'s: a download of the PDF the person kept, waiting
            for its check.

    Returns:
        ``True`` when its marker holds its digest; ``False`` with no marker,
        a marker for other bytes, or one that cannot be read (logged).
    """
    marker = kept_marker_path(pdf_path)
    if not marker.exists():
        return False
    try:
        digest = pdf_digest(contents if contents is not None else pdf_path)
        return marker.read_text(encoding="ascii").strip() == digest
    except (OSError, UnicodeDecodeError):
        logger.warning("The keep marker of %s could not be read", pdf_path.name, exc_info=True)
        return False


def _same_bytes(path: Path, digest: str) -> bool:
    """Whether a file already set aside holds the bytes of this digest.

    Args:
        path: An earlier set-aside file.
        digest: The digest of the PDF being set aside.

    Returns:
        ``False`` too for one that cannot be read (logged): the next name is
        taken, so an unreadable earlier file never blocks a later one.
    """
    try:
        return path.is_file() and pdf_digest(path) == digest
    except OSError:
        logger.warning("%s could not be read to compare it", path.name, exc_info=True)
        return False


def set_aside(pdf_path: Path, contents: Path | None = None) -> Path:
    """Rename a PDF aside, where the cache does not look, never over another.

    The first free name of ``<pdf>.mismatch``, ``<pdf>.mismatch-2``, ... is
    taken. A PDF identical to one already set aside is removed instead, so
    the same wrong download, set aside on every run, is kept once.

    Args:
        pdf_path: The PDF's name in the cache, which the set-aside names
            follow.
        contents: The file holding the PDF, when it is not at ``pdf_path``
            (a download waiting for its check); ``pdf_path`` when ``None``.

    Returns:
        Where its bytes now are.

    Raises:
        OSError: When it cannot be read, renamed or removed; it is then
            where it was.
    """
    source = contents if contents is not None else pdf_path
    digest: str | None = None
    for number in count(1):
        suffix = PDF_MISMATCH_SUFFIX if number == 1 else f"{PDF_MISMATCH_SUFFIX}-{number}"
        target = pdf_path.with_name(pdf_path.name + suffix)
        if not target.exists():
            source.rename(target)
            return target
        if digest is None:
            digest = pdf_digest(source)
        if _same_bytes(target, digest):
            source.unlink()
            return target
    raise AssertionError("count() does not end")  # pragma: no cover


def keep_pdf(path: Path) -> Path:
    """Keep a PDF as its article's, as a person asked: ``bmll keep-pdf``.

    A set-aside PDF is renamed back to the name the cache finds it by; a PDF
    is marked where it is.

    Args:
        path: A ``.pdf`` or a set-aside PDF.

    Returns:
        The PDF kept, at the name the cache finds it by.

    Raises:
        ValueError: For a file that is neither.
        FileExistsError: When another PDF already has the name to go back
            to; nothing is changed.
        OSError: When it cannot be renamed or marked.
    """
    named = _SET_ASIDE_NAME.match(path.name)
    if named is not None:
        pdf_path = path.with_name(named.group("pdf"))
        if pdf_path.exists():
            raise FileExistsError(f"{pdf_path.name} is already in the library")
        # Marked first: renamed back without its marker, it would be set
        # aside again. A marker left by a failed rename names these bytes
        # alone, so it vouches for no other PDF.
        mark_kept(pdf_path, contents=path)
        path.rename(pdf_path)
    elif path.name.endswith(_PDF_EXTENSION):
        pdf_path = path
        mark_kept(pdf_path)
    else:
        raise ValueError(f"{path.name} is neither a PDF nor one set aside")
    logger.info("Kept %s as its article's, as the reader asked", pdf_path.name)
    return pdf_path


def check_cached_pdf(pdf_path: Path, title: str | None) -> CachedPdfCheck:
    """Whether a PDF in the library is read as its article's; set it aside if not.

    Args:
        pdf_path: The PDF the cache found.
        title: The article's title; ``None`` or blank checks nothing.

    Returns:
        ``USABLE`` for a PDF a person kept, one carrying the title, and one
        whose title could not be judged; ``SET_ASIDE`` with its new name, or
        ``NOT_SET_ASIDE`` when the rename failed (logged), for one that
        lacks it.
    """
    if not title or not title.strip():
        return CachedPdfCheck(CachedPdfOutcome.USABLE, pdf_path)
    if is_kept(pdf_path):
        return CachedPdfCheck(CachedPdfOutcome.USABLE, pdf_path)
    check = check_pdf_title(pdf_path, title)
    if check.outcome is not TitleCheckOutcome.MISMATCH:
        return CachedPdfCheck(CachedPdfOutcome.USABLE, pdf_path, check)
    try:
        moved = set_aside(pdf_path)
    except OSError:
        logger.error(
            "%s does not carry its article's title and could not be moved aside",
            pdf_path.name, exc_info=True,
        )
        return CachedPdfCheck(CachedPdfOutcome.NOT_SET_ASIDE, pdf_path, check)
    logger.warning(
        "%s does not carry its article's title (%.0f%% of its word pairs); set aside as %s",
        pdf_path.name, (check.share or 0.0) * 100, moved.name,
    )
    return CachedPdfCheck(CachedPdfOutcome.SET_ASIDE, pdf_path, check, moved)


def set_aside_note(found: CachedPdfCheck) -> str | None:
    """Tell the reader a PDF in the library was not read as the article's.

    Args:
        found: The check of a PDF the cache found.

    Returns:
        A sentence or two naming the file, the share of the title it carries
        and, for one set aside, where it went and how to keep it; ``None``
        for a PDF that is read.
    """
    if found.usable:
        return None
    share = found.check.share if found.check is not None else None
    carried = f" ({share:.0%} of the title's word pairs on its first page)" if share is not None else ""
    lead = (
        f"The PDF filed for this article in the library, {found.path.name}, does not "
        f"carry this article's title{carried}, so it was not read as this article."
    )
    if found.set_aside_to is None:
        return (
            f"{lead} It could not be moved aside (the log says why); if it is the "
            f"article, keep it with: {KEEP_PDF_COMMAND} \"{found.path}\""
        )
    return (
        f"{lead} It was renamed {found.set_aside_to.name}; if it is the article, "
        f"keep it with: {KEEP_PDF_COMMAND} \"{found.set_aside_to}\""
    )


def set_aside_download_note(paths: tuple[Path, ...]) -> str | None:
    """Tell the reader the PDFs downloaded for an article that were not read (#541).

    Args:
        paths: Where each PDF that does not carry the title was set aside.

    Returns:
        A sentence naming each, with how to keep one; ``None`` for none.
    """
    if not paths:
        return None
    if len(paths) == 1:
        lead = f"A PDF downloaded for this article does not carry its title, so it was not read; it was saved as {paths[0].name}."
    else:
        names = ", ".join(path.name for path in paths)
        lead = f"{len(paths)} PDFs downloaded for this article do not carry its title, so they were not read; they were saved as {names}."
    return f"{lead} If one is the article, keep it with: {KEEP_PDF_COMMAND} \"{paths[0]}\""
