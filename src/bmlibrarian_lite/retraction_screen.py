# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Screening documents by their retraction verdicts. Pure: no network, no DB."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bmlib.publications.retraction_status import RetractionStatus

from .data_models import LiteDocument
from .retraction_store import PaperCheck

logger = logging.getLogger(__name__)

_SNAPSHOT_REQUIRED = ("document_id", "status")


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
        return min((c.checked_at for c in self.checks.values()), default=None)

    def to_snapshot(self) -> list[dict[str, Any]]:
        """Rows for the report and audit trail, one per checked document.

        Returns:
            JSON-safe dicts: document_id, title, doi, pmid, status,
            checked_at (ISO) and deciding (evidence dict or ``None``).
        """
        docs = {d.id: d for d in (*self.kept, *self.excluded)}
        rows = []
        for doc_id, check in self.checks.items():
            doc = docs.get(doc_id)
            deciding = check.verdict.deciding
            rows.append(
                {
                    "document_id": doc_id,
                    "title": doc.title if doc else None,
                    "doi": doc.doi if doc else None,
                    "pmid": doc.pmid if doc else None,
                    "status": check.verdict.status.value,
                    "checked_at": check.checked_at.isoformat(),
                    "deciding": deciding.to_dict() if deciding else None,
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
