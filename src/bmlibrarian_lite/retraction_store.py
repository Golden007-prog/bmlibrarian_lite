# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The stored retraction verdicts' table: one row per paper.

SQL only; the rules are ``retraction_checks``'. Every function takes an open
connection whose ``row_factory`` is ``sqlite3.Row``; ``LiteStorage`` opens it
and commits after :func:`upsert`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime

from bmlib.publications.retraction_status import (
    RetractionEvidence,
    RetractionLookup,
    RetractionStatus,
    RetractionVerdict,
)

from .data_models import RequestFailure

logger = logging.getLogger(__name__)

TABLE = "retraction_checks"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    paper_key TEXT PRIMARY KEY,  -- "doi:" + crossref_key(doi), else "pmid:" + PMID
    status TEXT NOT NULL,
    deciding_json TEXT,          -- RetractionEvidence.to_dict(); NULL for none
    unanswered_json TEXT NOT NULL,  -- list of RetractionLookup values
    crossref_failure_json TEXT,  -- RequestFailure.to_dict(); NULL for none
    export_data_as_of TEXT,      -- ISO day of the export copy consulted
    checked_at TEXT NOT NULL     -- ISO, UTC
);
"""


@dataclass(frozen=True)
class PaperCheck:
    """One paper's verdict and what it rests on.

    Attributes:
        verdict: bmlib's verdict.
        checked_at: When it was decided (UTC).
        export_data_as_of: The day of the export copy consulted; ``None``
            when the export was not consulted.
        crossref_failure: Why Crossref did not answer; ``None`` when it did,
            or was not asked.
    """

    verdict: RetractionVerdict
    checked_at: datetime
    export_data_as_of: date | None
    crossref_failure: RequestFailure | None


def _decode(row: sqlite3.Row) -> PaperCheck:
    """Rebuild a stored check; raises on a row this build cannot read."""
    deciding = json.loads(row["deciding_json"]) if row["deciding_json"] else None
    failure = row["crossref_failure_json"]
    checked_at = datetime.fromisoformat(row["checked_at"])
    if checked_at.tzinfo is None:
        raise ValueError("checked_at carries no time zone")
    return PaperCheck(
        verdict=RetractionVerdict(
            status=RetractionStatus(row["status"]),
            deciding=RetractionEvidence.from_dict(deciding) if deciding else None,
            unanswered=tuple(RetractionLookup(v) for v in json.loads(row["unanswered_json"])),
        ),
        checked_at=checked_at,
        export_data_as_of=(
            date.fromisoformat(row["export_data_as_of"]) if row["export_data_as_of"] else None
        ),
        crossref_failure=RequestFailure.from_dict(json.loads(failure)) if failure else None,
    )


def read(conn: sqlite3.Connection, keys: Iterable[str]) -> dict[str, PaperCheck]:
    """The stored checks for *keys*.

    Args:
        conn: An open connection.
        keys: Paper keys.

    Returns:
        A check per key that has a readable row. A row this build cannot read
        (a newer build's status, damage) is left out, so the paper is checked
        again rather than shown a verdict nobody can vouch for.
    """
    out: dict[str, PaperCheck] = {}
    for key in set(keys):
        row = conn.execute(f"SELECT * FROM {TABLE} WHERE paper_key = ?", (key,)).fetchone()
        if row is None:
            continue
        try:
            out[key] = _decode(row)
        except (ValueError, TypeError, KeyError, AttributeError):
            logger.warning("Unreadable retraction check for %s; checking again", key)
    return out


def upsert(conn: sqlite3.Connection, checks: Mapping[str, PaperCheck]) -> None:
    """Store each check under its paper key, replacing any earlier one.

    Args:
        conn: An open connection; the caller commits.
        checks: Checks by paper key.
    """
    conn.executemany(
        f"INSERT OR REPLACE INTO {TABLE} (paper_key, status, deciding_json, unanswered_json,"
        " crossref_failure_json, export_data_as_of, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                key,
                check.verdict.status.value,
                json.dumps(check.verdict.deciding.to_dict()) if check.verdict.deciding else None,
                json.dumps([lookup.value for lookup in check.verdict.unanswered]),
                json.dumps(check.crossref_failure.to_dict()) if check.crossref_failure else None,
                check.export_data_as_of.isoformat() if check.export_data_as_of else None,
                check.checked_at.astimezone(UTC).isoformat(),
            )
            for key, check in checks.items()
        ],
    )
