# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Screening documents by their retraction verdicts. Pure: no network, no DB."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bmlib.publications.retraction_status import RetractionStatus

from .data_models import LiteDocument
from .retraction_store import PaperCheck

logger = logging.getLogger(__name__)

_SNAPSHOT_REQUIRED = ("document_id", "status")


def as_utc(moment: datetime) -> datetime:
    """Read a naive datetime as UTC, the zone every stored check is in."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


@dataclass(frozen=True)
class RetractionScreen:
    """Documents split by retraction status.

    Attributes:
        kept: Everything not retracted, in input order.
        excluded: The retracted papers, in input order.
        checks: Each checked document's verdict, keyed by ``LiteDocument.id``.
    """

    kept: tuple[LiteDocument, ...]
    excluded: tuple[LiteDocument, ...]
    checks: Mapping[str, PaperCheck]

    @property
    def unchecked_count(self) -> int:
        """Kept documents whose status is unchecked or that have no check."""
        return sum(
            1
            for doc in self.kept
            if doc.id not in self.checks
            or self.checks[doc.id].verdict.status is RetractionStatus.UNCHECKED
        )

    @property
    def concern_ids(self) -> tuple[str, ...]:
        """Ids of the documents under an expression of concern."""
        return tuple(
            doc_id
            for doc_id, check in self.checks.items()
            if check.verdict.status is RetractionStatus.CONCERN
        )

    @property
    def checked_at(self) -> datetime | None:
        """The oldest ``checked_at`` among the checks; ``None`` without any."""
        return min((as_utc(c.checked_at) for c in self.checks.values()), default=None)

    def to_snapshot(self) -> list[dict[str, Any]]:
        """Rows for the report and audit trail, one per held document.

        A document with no check is an unchecked row with ``checked_at`` ``None``.
        ``excluded`` is a fact of the run: a later *Check again* may rewrite
        ``status``, never this, so readers decide exclusion from it alone.

        Returns:
            JSON-safe dicts: document_id, title, doi, pmid, status,
            checked_at (ISO or ``None``), deciding (evidence dict or ``None``)
            and excluded (whether this run left the document out).
        """
        rows = []
        excluded_ids = {doc.id for doc in self.excluded}
        for doc in (*self.kept, *self.excluded):
            check = self.checks.get(doc.id)
            deciding = check.verdict.deciding if check else None
            rows.append(
                {
                    "document_id": doc.id,
                    "title": doc.title,
                    "doi": doc.doi,
                    "pmid": doc.pmid,
                    "status": (check.verdict.status if check else RetractionStatus.UNCHECKED).value,
                    "checked_at": as_utc(check.checked_at).isoformat() if check else None,
                    "deciding": deciding.to_dict() if deciding else None,
                    "excluded": doc.id in excluded_ids,
                }
            )
        return rows

    @staticmethod
    def from_snapshot(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        """Read stored snapshot rows back, keyed by document id.

        A row an older build wrote without ``document_id`` or ``status`` is
        skipped with a warning; keys beyond those are returned as found.
        """
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping) or any(k not in row for k in _SNAPSHOT_REQUIRED):
                logger.warning("Skipping a retraction snapshot row from an older build")
                continue
            out[str(row["document_id"])] = dict(row)
        return out


@dataclass(frozen=True)
class RetractionSnapshot:
    """What a stored report needs of a screen, rebuilt from its snapshot rows.

    It does not rebuild the documents or verdicts: the report reads rows
    (``to_snapshot``) and the counts the notice needs, nothing more.

    Attributes:
        rows: Snapshot rows as ``RetractionScreen.to_snapshot`` wrote them.
    """

    rows: tuple[dict[str, Any], ...]

    @property
    def excluded(self) -> tuple[dict[str, Any], ...]:
        """The rows of the papers the run left out."""
        return tuple(r for r in self.rows if r.get("excluded"))

    @property
    def unchecked_count(self) -> int:
        """Kept rows whose status is unchecked."""
        return sum(
            1
            for r in self.rows
            if not r.get("excluded") and r["status"] == RetractionStatus.UNCHECKED.value
        )

    @property
    def checked_at(self) -> datetime | None:
        """The oldest check time; ``None`` without any."""
        return min(
            (as_utc(datetime.fromisoformat(r["checked_at"])) for r in self.rows if r.get("checked_at")),
            default=None,
        )

    def to_snapshot(self) -> list[dict[str, Any]]:
        """The rows, as given."""
        return [dict(r) for r in self.rows]

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, Any]]) -> RetractionSnapshot:
        """Build from stored rows, skipping any an older build wrote short."""
        return cls(tuple(RetractionScreen.from_snapshot(rows).values()))


def screen_documents(
    documents: Iterable[LiteDocument], checks: Mapping[str, PaperCheck]
) -> RetractionScreen:
    """Leave out the retracted; keep everything else, in input order.

    Args:
        documents: The documents checked.
        checks: Verdicts keyed by document id; a document without one is kept.

    Returns:
        The screen.
    """
    kept: list[LiteDocument] = []
    excluded: list[LiteDocument] = []
    for doc in documents:
        check = checks.get(doc.id)
        retracted = check is not None and check.verdict.status is RetractionStatus.RETRACTED
        (excluded if retracted else kept).append(doc)
    return RetractionScreen(tuple(kept), tuple(excluded), dict(checks))
