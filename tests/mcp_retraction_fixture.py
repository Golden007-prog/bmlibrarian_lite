"""A fixture for MCP tool tests that are not about retractions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from bmlib.publications.retraction_status import RetractionStatus, RetractionVerdict

from bmlibrarian_lite import mcp_server
from bmlibrarian_lite.retraction_store import PaperCheck


class _ClearChecker:
    """A checker that finds every paper clear."""

    def check(self, documents, **_kwargs):  # type: ignore[no-untyped-def]
        """Answer clear for each document."""
        clear = PaperCheck(RetractionVerdict(RetractionStatus.CLEAR), datetime.now(UTC), None, None)
        return {d.id: clear for d in documents}

    def close(self) -> None:
        """Nothing to close."""


@pytest.fixture
def mcp_retraction_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every paper is clear and no network is used."""
    monkeypatch.setattr(mcp_server, "_build_checker", lambda ctx: _ClearChecker())
