# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every reader-facing sentence about retraction.

No provider text: a failure's cause comes from ``RequestFailure.describe()``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from bmlib.publications.retraction_status import (
    RetractionLookup,
    RetractionSource,
    RetractionStatus,
)

from .retraction_checks import paper_key
from .retraction_export import ExportCopy, ExportState
from .retraction_screen import RetractionRecord, RetractionScreen, as_utc
from .retraction_store import PaperCheck

_NOTICE_OPENING = "> **Retracted papers left out:** "
_NOTICE_SEPARATOR = "\n\n"
_RW = "Retraction Watch"
_CROSSREF = "Crossref"
_SOURCE_NAMES = {
    RetractionLookup.RETRACTION_WATCH_EXPORT: _RW,
    RetractionLookup.CROSSREF: _CROSSREF,
}


_EXPORT_CAUSE = "the Retraction Watch copy could not be used (run `bmll retractions-update`)"
_FAILED_CAUSE = "the check failed"
_NO_IDS_CAUSE = "no DOI or PMID"


def _unchecked_causes(screen: RetractionScreen) -> list[str]:
    """Why the screen's kept papers are unchecked, each cause class once.

    In order: the check itself, the export copy, Crossref (its distinct
    ``RequestFailure.describe()`` causes), papers without identifiers.
    """
    if screen.failed:
        return [_FAILED_CAUSE]
    export = no_ids = False
    crossref: list[str] = []
    for doc in screen.kept:
        check = screen.checks.get(doc.id)
        if check is None or check.verdict.status is not RetractionStatus.UNCHECKED:
            continue  # a paper the cancelled check never reached has no cause to give
        if paper_key(doc) is None:
            no_ids = True  # nothing could be looked up
            continue
        unanswered = check.verdict.unanswered
        export = export or RetractionLookup.RETRACTION_WATCH_EXPORT in unanswered
        failure = check.crossref_failure
        if RetractionLookup.CROSSREF in unanswered and failure is not None:
            cause = failure.describe()
            if cause not in crossref:
                crossref.append(cause)
    causes = [_EXPORT_CAUSE] if export else []
    if crossref:
        causes.append(f"{_CROSSREF} could not be used ({', '.join(crossref)})")
    return causes + ([_NO_IDS_CAUSE] if no_ids else [])


def run_summary_clause(screen: RetractionRecord) -> str | None:
    """Summarise a screen for a run's summary line, naming why papers are unchecked.

    A restored snapshot does not record why, so its clause gives the count only.

    Returns:
        E.g. ``"2 retracted papers left out; 3 papers' retraction status could
        not be checked: no DOI or PMID"``; ``None`` when both counts are 0.
    """
    parts = []
    left_out = len(screen.excluded)
    if left_out:
        noun = "retracted paper" if left_out == 1 else "retracted papers"
        parts.append(f"{left_out:,} {noun} left out")
    unchecked = screen.unchecked_count
    if unchecked:
        owner = "1 paper's" if unchecked == 1 else f"{unchecked:,} papers'"
        clause = f"{owner} retraction status could not be checked"
        causes = _unchecked_causes(screen) if isinstance(screen, RetractionScreen) else []
        parts.append(f"{clause}: {'; '.join(causes)}" if causes else clause)
    return "; ".join(parts) or None


def format_retraction_notice(screen: RetractionRecord) -> str | None:
    """Build the notice for a report; ``None`` when there is nothing to say."""
    clause = run_summary_clause(screen)
    if clause is None:
        return None
    return f"{_NOTICE_OPENING}{clause}."


def with_retraction_notice(text: str, screen: RetractionRecord) -> str:
    """Put the retraction notice in front of text; idempotent."""
    notice = format_retraction_notice(screen)
    if notice is None or text.startswith(_NOTICE_OPENING):
        return text
    return f"{notice}{_NOTICE_SEPARATOR}{text}"


def without_retraction_notice(text: str) -> str:
    """Read what follows the notice, for deciding what a text is. Not for display."""
    if not text.startswith(_NOTICE_OPENING):
        return text
    _, separator, rest = text.partition(_NOTICE_SEPARATOR)
    return rest if separator else text


def checked_on_line(checked_at: datetime | None) -> str:
    """Say when the status was checked (the UTC date), or that it never was."""
    if checked_at is None:
        return "Retraction status never checked"
    return f"Retraction status checked {as_utc(checked_at).date().isoformat()}"


def retraction_methods_line(record: RetractionRecord) -> str:
    """The report's methods line: when the run's status was checked.

    A run that held papers and has no check time could not check any (the
    check failed, or was cancelled first); that is said, never a date.
    """
    if record.checked_at is None and record.to_snapshot():
        return "Retraction status could not be checked"
    return checked_on_line(record.checked_at)


_LABEL = " (PubMed's label)"


def paper_status_line(check: PaperCheck) -> str:
    """One paper's status, for a banner or an MCP header."""
    verdict = check.verdict
    evidence = verdict.deciding
    if verdict.status in (RetractionStatus.RETRACTED, RetractionStatus.CONCERN):
        retracted = verdict.status is RetractionStatus.RETRACTED
        line = "Retracted" if retracted else "Expression of concern"
        # the label, not a notice: a notice without a DOI (the export's
        # "unavailable") is still Retraction Watch's or Crossref's
        if evidence is None or evidence.source is RetractionSource.PUBMED_LABEL:
            line += _LABEL
        else:
            if evidence.date:
                line += f" on {evidence.date}" if retracted else f", {evidence.date}"
            if evidence.notice_doi:
                line += f" (notice {evidence.notice_doi})"
    elif verdict.status is RetractionStatus.UNCHECKED:
        names = []
        for lookup in verdict.unanswered:
            name = _SOURCE_NAMES[lookup]
            if lookup is RetractionLookup.CROSSREF and check.crossref_failure is not None:
                name = f"{name} ({check.crossref_failure.describe()})"
            names.append(name)
        line = "Retraction status not checked"
        if names:
            line += ": " + ", ".join(names)
    else:
        line = "No retraction found"
    if check.export_data_as_of is not None:
        line += f"; {_RW} data as of {check.export_data_as_of.isoformat()}"
    return line


LEFT_OUT_HEADING = "## Left out as retracted"
_CONCERN_INDENT = "    "


def _evidence_parts(row: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """The deciding notice's date and DOI from a snapshot row."""
    deciding = row.get("deciding") or {}
    return deciding.get("date"), deciding.get("notice_doi")


def _row_by_label(row: Mapping[str, Any]) -> bool:
    """Whether a snapshot row's verdict rests on PubMed's label."""
    deciding = row.get("deciding") or {}
    return deciding.get("source") in (None, RetractionSource.PUBMED_LABEL.value)


_MARKDOWN_SPECIALS = re.compile(r"([\\`*_\[\]<>])")


def plain_title(title: str) -> str:
    """A title safe inside one markdown line: whitespace collapsed, specials escaped."""
    return _MARKDOWN_SPECIALS.sub(r"\\\1", " ".join(str(title).split()))


def format_retracted_section(screen: RetractionRecord) -> str:
    """List the papers a run left out, from the screen's snapshot rows.

    Returns:
        The section, one line per paper ("<title>. Retracted <date>; notice
        <doi>"), or "" when nothing was left out.
    """
    lines = []
    for row in screen.to_snapshot():
        if not row.get("excluded"):
            continue
        when, notice = _evidence_parts(row)
        line = f"- {plain_title(row['title'])}. Retracted"
        if _row_by_label(row):
            line += _LABEL
        else:
            line += f" {when}" if when else ""
            line += f"; notice {notice}" if notice else ""
        lines.append(line)
    return f"{LEFT_OUT_HEADING}\n\n" + "\n".join(lines) if lines else ""


def format_reference_concern_annotation(row: Mapping[str, Any]) -> str:
    """Caveat for a cited paper under an expression of concern (a snapshot row)."""
    when, notice = _evidence_parts(row)
    detail = ", ".join(
        part for part in (when, f"notice {notice}" if notice else None) if part
    )
    return f"{_CONCERN_INDENT}⚠️ Expression of concern" + (f" ({detail})" if detail else "")


_BADGE_TEXTS = {
    RetractionStatus.RETRACTED: "RETRACTED",
    RetractionStatus.CONCERN: "CONCERN",
    RetractionStatus.UNCHECKED: "NOT CHECKED",
}
EXCLUDED_RETRACTED_LABEL = "Excluded: retracted"


def badge_text(status: RetractionStatus) -> str | None:
    """A status badge's text; ``None`` for clear, which shows no badge."""
    return _BADGE_TEXTS.get(status)


_EXPORT_STATE_WORDS = {
    ExportState.CURRENT: "current",
    ExportState.STALE: "out of date",
    ExportState.MISSING: "missing",
    ExportState.UNREADABLE: "unreadable",
}


def export_refreshed_line(copy: ExportCopy) -> str:
    """Say that ``bmll retractions-update`` built a new copy."""
    as_of = copy.data_as_of.isoformat() if copy.data_as_of else "unknown"
    return f"Retraction Watch copy refreshed; data as of {as_of}."


def export_not_refreshed_line(copy: ExportCopy) -> str:
    """Say that a refresh built no new copy, and what is kept, in words."""
    state = _EXPORT_STATE_WORDS[copy.state]
    if copy.data_as_of is None:
        return f"Refresh did not produce a new copy; there is no copy to keep (the copy is {state})."
    return (
        f"Refresh did not produce a new copy; the kept copy is {state}, "
        f"data as of {copy.data_as_of.isoformat()}."
    )


def refresh_failed_line(error: BaseException) -> str:
    """Say a refresh failed, naming the error's type only (never its text)."""
    return f"Refresh failed: {type(error).__name__}."
