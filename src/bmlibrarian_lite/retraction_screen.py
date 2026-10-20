# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Screening documents by their retraction verdicts. Pure: no network, no DB."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from bmlib.publications.retraction_status import (
    RetractionEvidence,
    RetractionLookup,
    RetractionStatus,
    RetractionVerdict,
    retraction_status,
)

from .data_models import LiteDocument
from .retraction_store import PaperCheck

logger = logging.getLogger(__name__)

_SNAPSHOT_REQUIRED = ("document_id", "status")


def as_utc(moment: datetime) -> datetime:
    """Read a naive datetime as UTC, the zone every stored check is in."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


class RetractionRecord(Protocol):
    """What the report reads of a run's retraction check, live or restored."""

    @property
    def excluded(self) -> Sequence[Any]:
        """The papers left out."""
        ...

    @property
    def unchecked_count(self) -> int:
        """Kept papers whose status could not be checked."""
        ...

    @property
    def checked_at(self) -> datetime | None:
        """The oldest check time; ``None`` without any."""
        ...

    def to_snapshot(self) -> list[dict[str, Any]]:
        """One row per held document."""
        ...


@dataclass(frozen=True)
class RetractionScreen:
    """Documents split by retraction status.

    Attributes:
        kept: Everything not retracted, in input order.
        excluded: The retracted papers, in input order.
        checks: Each checked document's verdict, keyed by ``LiteDocument.id``.
        failed: The check itself failed: every check is a stand-in (unchecked,
            or retracted by the record's own label), and the screen reports
            no check time.
    """

    kept: tuple[LiteDocument, ...]
    excluded: tuple[LiteDocument, ...]
    checks: Mapping[str, PaperCheck]
    failed: bool = False

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
        """The oldest ``checked_at`` among the checks; ``None`` without any, or when the check failed."""
        if self.failed:
            return None
        return min((as_utc(c.checked_at) for c in self.checks.values()), default=None)

    def to_snapshot(self) -> list[dict[str, Any]]:
        """Rows for the report and audit trail, one per held document.

        A document with no check, or whose check failed, is an unchecked row
        with ``checked_at`` ``None``.
        ``excluded`` is a fact of the run: a later re-check may rewrite
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
                    "checked_at": (
                        as_utc(check.checked_at).isoformat()
                        if check and not self.failed
                        else None
                    ),
                    "deciding": deciding.to_dict() if deciding else None,
                    "excluded": doc.id in excluded_ids,
                }
            )
        return rows

    @staticmethod
    def from_snapshot(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        """Read stored snapshot rows back, keyed by document id, each read once.

        The rows are stored JSON, so every reader gets them normalised here:
        ``excluded`` is a strict bool (only ``true`` left a paper out), an
        unknown ``status`` reads as unchecked, a missing ``title`` as "", a
        ``deciding`` that is not a mapping as none and an unreadable
        ``checked_at`` as none. A row is kept through all of these, so a
        paper left out never reads as not scored; only a row without
        ``document_id`` or ``status`` is skipped with a warning.

        Args:
            rows: Rows as ``to_snapshot`` wrote them.

        Returns:
            The normalised rows by document id; keys beyond those named are
            returned as found.
        """
        known = {status.value for status in RetractionStatus}
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping) or any(k not in row for k in _SNAPSHOT_REQUIRED):
                logger.warning("Skipping a retraction snapshot row from an older build")
                continue
            read = dict(row)
            stamp = read.get("checked_at")
            if stamp is not None:
                try:
                    datetime.fromisoformat(stamp)
                except (TypeError, ValueError):
                    logger.warning("A retraction snapshot row's time is unreadable; read as none")
                    read["checked_at"] = None
            if read["status"] not in known:
                logger.warning("A retraction snapshot row's status is unknown; read as unchecked")
                read["status"] = RetractionStatus.UNCHECKED.value
            read["excluded"] = read.get("excluded") is True
            read["title"] = str(read.get("title") or "")
            if not isinstance(read.get("deciding"), Mapping):
                read["deciding"] = None
            out[str(read["document_id"])] = read
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

    @classmethod
    def from_stored(cls, stored: Any) -> RetractionSnapshot | None:
        """Read a stored value: ``None`` for none, or for one that is not a list."""
        if stored is None:
            return None
        if not isinstance(stored, list):
            logger.warning("Ignoring a stored retraction_screen that is not a list")
            return None
        return cls.from_rows(stored)


def screen_documents(
    documents: Iterable[LiteDocument],
    checks: Mapping[str, PaperCheck],
    *,
    failed: bool = False,
) -> RetractionScreen:
    """Leave out the retracted; keep everything else, in input order.

    Args:
        documents: The documents checked.
        checks: Verdicts keyed by document id; a document without one is kept.
        failed: The check failed and ``checks`` are stand-ins.

    Returns:
        The screen.
    """
    kept: list[LiteDocument] = []
    excluded: list[LiteDocument] = []
    for doc in documents:
        check = checks.get(doc.id)
        retracted = check is not None and check.verdict.status is RetractionStatus.RETRACTED
        (excluded if retracted else kept).append(doc)
    return RetractionScreen(tuple(kept), tuple(excluded), dict(checks), failed)


def unchecked_check() -> PaperCheck:
    """A check saying no lookup answered: the stand-in when checking failed."""
    return PaperCheck(
        RetractionVerdict(RetractionStatus.UNCHECKED, None, tuple(RetractionLookup)),
        datetime.now(UTC),
        None,
        None,
    )


class UnbuiltChecker:
    """Stands in for a checker that could not be built: every check fails.

    So a run whose checker could not be built goes on with every document
    unchecked, exactly as one whose check raised.
    """

    def check(self, documents: Sequence[LiteDocument], **_kwargs: Any) -> dict[str, PaperCheck]:
        """Fail, as the checker that could not be built would have.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("the retraction checker could not be built")

    def close(self) -> None:
        """Nothing to close."""


def _label_check(doc: LiteDocument) -> PaperCheck:
    """The stand-in for a failed check: the record's own label still retracts.

    The label needs neither network nor database, so a retraction it states
    survives any failure; every other paper is unchecked.
    """
    verdict = retraction_status(
        doi=None, export=None, crossref=None, publication_types=list(doc.publication_types or ())
    )
    if verdict.status is RetractionStatus.RETRACTED:
        return PaperCheck(verdict, datetime.now(UTC), None, None)
    return unchecked_check()


def screen_with_fallback(
    checker: Any,
    documents: Sequence[LiteDocument],
    should_cancel: Callable[[], bool] = lambda: False,
) -> RetractionScreen:
    """Check the documents and leave the retracted ones out; a failed check never raises.

    A check that fails never stops the caller: every document is then
    unchecked and goes on, unless its record's own label says retracted. The checker is built for one use, so it is closed
    when the check is done.

    Args:
        checker: A ``RetractionChecker`` (anything with ``check`` and ``close``).
        documents: The documents.
        should_cancel: Passed to the checker.

    Returns:
        The screen; after a cancel, possibly partial.
    """
    try:
        return screen_documents(documents, checker.check(documents, should_cancel=should_cancel))
    except Exception as e:
        # the type only: provider text never reaches the reader or the log
        logger.warning("Retraction check failed (%s); every document unchecked", type(e).__name__)
        return screen_documents(
            documents, {doc.id: _label_check(doc) for doc in documents}, failed=True
        )
    finally:
        checker.close()


def check_from_snapshot_row(row: Mapping[str, Any]) -> PaperCheck | None:
    """Rebuild a paper's check from a stored snapshot row, for display.

    A row does not record which lookups went unanswered, so an unchecked row
    names every source (bmlib refuses an unchecked verdict with none
    unanswered). ``None`` for a row this build cannot read.
    """
    try:
        status = RetractionStatus(row["status"])
        deciding = row.get("deciding")
        stamp = row.get("checked_at")
        return PaperCheck(
            RetractionVerdict(
                status,
                RetractionEvidence.from_dict(deciding) if deciding else None,
                tuple(RetractionLookup) if status is RetractionStatus.UNCHECKED else (),
            ),
            # nothing displays checked_at; a row without one gets a stand-in
            as_utc(datetime.fromisoformat(stamp)) if stamp else datetime.now(UTC),
            None,
            None,
        )
    except (KeyError, ValueError, TypeError, AttributeError):
        logger.warning("Skipping a retraction snapshot row this build cannot read")
        return None
