# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Each paper's retraction verdict, decided by bmlib and stored per paper.

The evidence is the local Retraction Watch copy, Crossref's answer and the
record's PubMed label. A decided verdict is reused while it is younger than
``RETRACTION_CHECK_MAX_AGE_DAYS``; one with a lookup unanswered rests on a
failure, not knowledge, so it is asked again on every check.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests
from bmlib.publications.retraction_status import (
    RetractionStatus,
    RetractionVerdict,
    crossref_key,
    retraction_status,
)

from .constants import RETRACTION_CHECK_MAX_AGE_DAYS
from .data_models import LiteDocument
from .identifiers import canonical_pmid
from .retraction_crossref import lookup_crossref
from .retraction_export import ExportCopy, export_copy, export_evidence, refresh_export
from .retraction_store import PaperCheck
from .storage import LiteStorage

__all__ = ["PaperCheck", "RetractionChecker", "paper_key"]


def _pmid(doc: LiteDocument) -> str | None:
    """The document's canonical PMID; a non-canonical one is no PMID."""
    return canonical_pmid(doc.pmid).pmid


def paper_key(doc: LiteDocument) -> str | None:
    """The key a paper's verdict is stored under.

    Args:
        doc: The document.

    Returns:
        ``"doi:"`` plus the DOI's Crossref key, else ``"pmid:"`` plus the
        canonical PMID, else ``None``.
    """
    doi = crossref_key(doc.doi)
    if doi:
        return "doi:" + doi
    pmid = _pmid(doc)
    return "pmid:" + pmid if pmid else None


def _types(docs: Sequence[LiteDocument]) -> list[str]:
    """Every publication type the paper's records carry."""
    return [t for doc in docs for t in (doc.publication_types or ())]


def _label_retracts(docs: Sequence[LiteDocument]) -> bool:
    """Whether the records' own label says retracted."""
    verdict = retraction_status(doi=None, export=None, crossref=None, publication_types=_types(docs))
    return verdict.status is RetractionStatus.RETRACTED


def _keeps(old: PaperCheck, new: RetractionVerdict) -> bool:
    """Whether the stored check stands against a fresh verdict.

    It stands only when the fresh verdict leaves unanswered a lookup the
    stored one had answered and does not warn more: a fresh retraction, or a
    concern over a stored clear, is news even while a lookup is down.
    """
    if not set(new.unanswered) - set(old.verdict.unanswered):
        return False
    if new.status is RetractionStatus.RETRACTED:
        return False
    return not (
        new.status is RetractionStatus.CONCERN and old.verdict.status is RetractionStatus.CLEAR
    )


class RetractionChecker:
    """Decides and stores retraction verdicts for documents."""

    def __init__(
        self,
        storage: LiteStorage,
        data_dir: Path,
        session: requests.Session,
        *,
        mailto: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Keep the collaborators.

        Args:
            storage: Where verdicts are stored.
            data_dir: The directory holding the Retraction Watch copy.
            session: The session for Crossref and the export download.
            mailto: Contact address for both.
            now: The clock; stored rows' age and the export's day both read it.
        """
        self._storage = storage
        self._data_dir = data_dir
        self._session = session
        self._mailto = mailto
        self._now = now
        self._refresh_tried = False

    def _today(self) -> date:
        return self._now().astimezone(UTC).date()

    def ensure_export(self) -> ExportCopy:
        """The export copy, refreshed once per checker when not current.

        Returns:
            The copy's state; a failed refresh is not tried again by this
            checker.
        """
        today = self._today()
        copy = export_copy(self._data_dir, today=today)
        if copy.consulted or self._refresh_tried:
            return copy
        self._refresh_tried = True
        return refresh_export(self._data_dir, self._session, mailto=self._mailto, today=today)

    def check(
        self,
        documents: Sequence[LiteDocument],
        *,
        refresh: bool = False,
        should_cancel: Callable[[], bool] = lambda: False,
    ) -> dict[str, PaperCheck]:
        """Each document's verdict, reusing fresh stored ones.

        A stored verdict is reused while it is younger than
        ``RETRACTION_CHECK_MAX_AGE_DAYS`` and every lookup answered it. A new
        verdict that leaves unanswered a lookup the stored one had answered
        never replaces it: the stored row keeps its ``checked_at`` and is
        asked again next time. Meanwhile a stored retraction or concern is
        still returned, and a stored clear is not.

        Args:
            documents: The documents.
            refresh: Ask again even for a fresh stored verdict.
            should_cancel: Passed to the Crossref lookup. A check that is
                cancelled before it starts does not refresh the export.

        Returns:
            A check per ``LiteDocument.id``. After a cancel, a document whose
            Crossref batch was never asked has none, and none is stored.

        Raises:
            sqlite3.Error: Reading or storing verdicts failed. Nothing is
                caught here, so the caller marks the documents unchecked.
        """
        now = self._now()
        out: dict[str, PaperCheck] = {}
        papers: dict[str, list[LiteDocument]] = defaultdict(list)
        for doc in documents:
            key = paper_key(doc)
            if key:
                papers[key].append(doc)
            else:
                verdict = retraction_status(
                    doi=None, export=None, crossref=None, publication_types=_types([doc])
                )
                out[doc.id] = PaperCheck(verdict, now, None, None)

        stored = self._storage.retraction_checks(papers)
        max_age = timedelta(days=RETRACTION_CHECK_MAX_AGE_DAYS)
        due: dict[str, list[LiteDocument]] = {}
        for key, docs in papers.items():
            row = stored.get(key)
            if (
                refresh
                or row is None
                or row.verdict.unanswered  # a failure is not knowledge: ask again
                or now - row.checked_at > max_age
                or (row.verdict.status is not RetractionStatus.RETRACTED and _label_retracts(docs))
            ):
                due[key] = docs
            else:
                out.update((doc.id, row) for doc in docs)
        if not due:
            return out

        # a cancel never waits for the export download
        copy = (
            export_copy(self._data_dir, today=self._today())
            if should_cancel()
            else self.ensure_export()
        )
        today = self._today()
        dois = [doc.doi for docs in due.values() for doc in docs[:1] if doc.doi]
        lookups = lookup_crossref(
            dois, self._session, mailto=self._mailto, should_cancel=should_cancel
        )
        cancelled = should_cancel()
        decided: dict[str, PaperCheck] = {}
        for key, docs in due.items():
            doi = crossref_key(docs[0].doi)
            if doi and doi not in lookups.results and cancelled:
                continue  # never asked: no verdict to give or keep
            pmid = next((p for p in map(_pmid, docs) if p), None)
            export = export_evidence(self._data_dir, doi=doi, pmid=pmid, today=today)
            verdict = retraction_status(
                doi=doi,
                export=export,
                crossref=lookups.results.get(doi) if doi else None,
                publication_types=_types(docs),
            )
            fresh = PaperCheck(
                verdict,
                now,
                copy.data_as_of if export is not None else None,
                lookups.failures.get(doi) if doi else None,
            )
            old = stored.get(key)
            if old is not None and _keeps(old, verdict):
                # an outage never erases what an answer established
                kept = old.verdict.status in (RetractionStatus.RETRACTED, RetractionStatus.CONCERN)
                out.update((doc.id, old if kept else fresh) for doc in docs)
                continue
            decided[key] = fresh
            out.update((doc.id, fresh) for doc in docs)
        self._storage.store_retraction_checks(decided)
        return out
