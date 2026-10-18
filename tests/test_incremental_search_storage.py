# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2025 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every document the incremental search finds is stored, not only handed on.

The review that scores them links and scores by id and never stored them, so a
reopened question dropped them.
"""

import sqlite3
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

from bmlibrarian_lite import pubmed  # noqa: E402
from bmlibrarian_lite.config import LiteConfig  # noqa: E402
from bmlibrarian_lite.data_models import DocumentSource, LiteDocument, ScoredDocument  # noqa: E402
from bmlibrarian_lite.gui import systematic_review_tab  # noqa: E402
from bmlibrarian_lite.gui.systematic_review_tab import WorkflowWorker  # noqa: E402
from bmlibrarian_lite.gui.workers import IncrementalSearchWorker  # noqa: E402
from bmlibrarian_lite.pubmed.data_types import (  # noqa: E402
    ArticleFetchResult,
    ArticleMetadata,
    SearchResult,
)
from bmlibrarian_lite.storage import LiteStorage  # noqa: E402

QUESTION = "Does aspirin prevent stroke?"
TYPES = ["Randomized Controlled Trial", "Journal Article"]


class StubClient:
    """A PubMed client answering one page of two records."""

    def __init__(self, **_: Any) -> None:
        """Ignore the credentials."""

    def search_with_offset(self, **_: Any) -> SearchResult:
        """Two PMIDs, the whole result set."""
        return SearchResult(query=MagicMock(), total_count=2, retrieved_count=2, pmids=["1", "2"])

    def fetch_articles(self, pmids: list[str]) -> ArticleFetchResult:
        """One article per PMID, with publication types."""
        return ArticleFetchResult(
            articles=[
                ArticleMetadata(
                    pmid=p,
                    title=f"Title {p}",
                    abstract=f"Abstract {p}",
                    doi=f"10.1/{p}",
                    publication_date="2024-01-02",
                    publication_types=TYPES,
                )
                for p in pmids
            ]
        )


@pytest.fixture
def storage(tmp_path: Any) -> LiteStorage:
    """A database this test owns."""
    config = LiteConfig()
    config.storage.data_dir = tmp_path
    return LiteStorage(config)


def search_worker(monkeypatch: pytest.MonkeyPatch, storage: Any) -> tuple[Any, dict[str, list]]:
    """An incremental search over the stub client, recording its signals."""
    monkeypatch.setattr(pubmed, "PubMedSearchClient", StubClient)
    worker = IncrementalSearchWorker(
        question=QUESTION,
        pubmed_query="aspirin AND stroke",
        target_new_docs=5,
        already_scored_ids=set(),
        config=LiteConfig(),
        storage=storage,
    )
    seen: dict[str, list] = {"batch": [], "finished": [], "error": []}
    worker.batch_complete.connect(lambda docs: seen["batch"].append(docs))
    worker.finished.connect(lambda docs, shortfalls: seen["finished"].append(docs))
    worker.error.connect(lambda message: seen["error"].append(message))
    return worker, seen


def test_documents_found_incrementally_are_stored_before_they_are_emitted(
    monkeypatch: pytest.MonkeyPatch, storage: LiteStorage
) -> None:
    """Both records are in the database, linked to the question, with their types."""
    worker, seen = search_worker(monkeypatch, storage)

    worker.run()

    assert seen["error"] == []
    for pmid in ("1", "2"):
        stored = storage.get_document(f"pmid-{pmid}")
        assert stored is not None
        assert (stored.title, stored.pmid, stored.doi) == (f"Title {pmid}", pmid, f"10.1/{pmid}")
        assert stored.publication_types == TYPES
    assert set(storage.get_document_ids_for_question(QUESTION)) == {"pmid-1", "pmid-2"}
    assert [d.id for d in seen["finished"][0]] == ["pmid-1", "pmid-2"]


def test_a_storage_failure_is_reported_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The error names the failure, and nothing is presented as stored."""
    storage = MagicMock()
    storage.add_documents.side_effect = sqlite3.OperationalError("disk I/O error")
    worker, seen = search_worker(monkeypatch, storage)

    worker.run()

    [message] = seen["error"]
    assert "disk I/O error" in message
    assert seen["batch"] == []
    assert seen["finished"] == []


def test_a_scored_preloaded_document_survives_a_reopen(
    monkeypatch: pytest.MonkeyPatch, storage: LiteStorage
) -> None:
    """Preloaded documents the review scores are stored, whatever route preloaded them."""
    articles = StubClient().fetch_articles(["1"]).articles
    doc = LiteDocument(
        id="pmid-1",
        title=articles[0].title,
        abstract=articles[0].abstract,
        pmid="1",
        source=DocumentSource.PUBMED,
        publication_types=TYPES,
    )
    scoring_agent = MagicMock()
    scoring_agent.score_document.side_effect = lambda question, d: ScoredDocument(
        document=d, score=5, explanation="Relevant."
    )
    monkeypatch.setattr(systematic_review_tab, "LiteScoringAgent", lambda **_: scoring_agent)
    config = LiteConfig()
    monkeypatch.setattr(config.parallel, "get_scoring_workers", lambda provider: 1)
    worker = WorkflowWorker(
        question=QUESTION,
        config=config,
        storage=storage,
        min_score=6,  # above the score: the review ends before any LLM call
        preloaded_documents=[doc],
    )

    worker.run()

    reopened = storage.get_documents(sorted(storage.get_document_ids_for_question(QUESTION)))
    assert [d.id for d in reopened] == ["pmid-1"]
    assert reopened[0].title == "Title 1"
    assert reopened[0].publication_types == TYPES
