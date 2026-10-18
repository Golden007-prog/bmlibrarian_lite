# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every reader-facing sentence about retraction.

No provider text: a failure's cause comes from ``RequestFailure.describe()``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from bmlib.publications.retraction_status import RetractionLookup, RetractionStatus

from .retraction_screen import RetractionScreen, as_utc
from .retraction_store import PaperCheck

_NOTICE_OPENING = "> **Retracted papers left out:** "
_NOTICE_SEPARATOR = "\n\n"
_RW = "Retraction Watch"
_CROSSREF = "Crossref"
_SOURCE_NAMES = {
    RetractionLookup.RETRACTION_WATCH_EXPORT: _RW,
    RetractionLookup.CROSSREF: _CROSSREF,
}


def run_summary_clause(screen: RetractionScreen) -> str | None:
    """Summarise a screen for a run's summary line.

    Returns:
        E.g. ``"2 retracted papers left out; 3 papers' retraction status could
        not be checked"``; ``None`` when both counts are 0.
    """
    parts = []
    left_out = len(screen.excluded)
    if left_out:
        noun = "retracted paper" if left_out == 1 else "retracted papers"
        parts.append(f"{left_out:,} {noun} left out")
    unchecked = screen.unchecked_count
    if unchecked:
        owner = "1 paper's" if unchecked == 1 else f"{unchecked:,} papers'"
        parts.append(f"{owner} retraction status could not be checked")
    return "; ".join(parts) or None


def format_retraction_notice(screen: RetractionScreen) -> str | None:
    """Build the notice for a report; ``None`` when there is nothing to say."""
    clause = run_summary_clause(screen)
    if clause is None:
        return None
    return f"{_NOTICE_OPENING}{clause}."


def with_retraction_notice(text: str, screen: RetractionScreen) -> str:
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


def paper_status_line(check: PaperCheck) -> str:
    """One paper's status, for a banner or an MCP header."""
    verdict = check.verdict
    evidence = verdict.deciding
    if verdict.status is RetractionStatus.RETRACTED:
        if evidence is None or evidence.notice_doi is None:
            line = "Retracted (PubMed's label)"
        else:
            when = f" on {evidence.date}" if evidence.date else ""
            line = f"Retracted{when} (notice {evidence.notice_doi})"
    elif verdict.status is RetractionStatus.CONCERN:
        when = f", {evidence.date}" if evidence and evidence.date else ""
        notice = f" (notice {evidence.notice_doi})" if evidence and evidence.notice_doi else ""
        line = f"Expression of concern{when}{notice}"
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


def format_retracted_section(screen: Any) -> str:
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
        line = f"- {row['title']}. Retracted"
        line += f" {when}" if when else " (PubMed's label)" if notice is None else ""
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
