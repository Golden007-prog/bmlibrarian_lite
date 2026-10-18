"""Tests for the local Retraction Watch copy (no network: a stub session)."""

from datetime import date, timedelta

import pytest

from bmlibrarian_lite.retraction_export import (
    ExportState,
    export_copy,
    export_evidence,
    refresh_export,
)

_HEADER = (
    "Record ID,Title,Subject,Institution,Journal,Publisher,Country,Author,URLS,"
    "ArticleType,RetractionDate,RetractionDOI,RetractionPubMedID,OriginalPaperDate,"
    "OriginalPaperDOI,OriginalPaperPubMedID,RetractionNature,Reason,Paywalled,Notes,\n"
)
_ROW = (
    "1,A paper,Subject,Inst,Soft Computing,Pub,AU,Author,URL,Article,"
    "3/9/2026 0:00,10.1/notice,87654321,5/6/2023 0:00,10.1/paper,12345678,"
    "Retraction,Rogue Editor;,No,Notes,\n"
)
CSV = (_HEADER + _ROW).encode()
TODAY = date(2026, 10, 18)


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status_code = status
        self._body = body

    def iter_content(self, size: int):
        yield self._body

    def close(self) -> None:
        pass


class _Session:
    def __init__(self, status: int = 200, body: bytes = CSV) -> None:
        self.status, self.body, self.calls = status, body, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.status, self.body)


def _refresh(tmp_path, session, today=TODAY):
    return refresh_export(tmp_path, session, mailto="a@b.c", today=today)


def test_refresh_builds_a_current_copy(tmp_path):
    """A refresh yields a current, queryable copy."""
    session = _Session()
    copy = _refresh(tmp_path, session, today=date.today())
    assert copy.state is ExportState.CURRENT and copy.data_as_of == date.today()
    assert session.calls[0][1]["params"] == {"mailto": "a@b.c"}
    evidence = export_evidence(tmp_path, doi="10.1/paper", pmid=None)
    assert evidence
    assert not list(tmp_path.glob("*.part")) and not list(tmp_path.glob("*.new"))


@pytest.mark.parametrize(
    "bad",
    [
        _Session(body=b"<html><body>Please wait</body></html>"),
        _Session(body=_HEADER.encode()[:40]),
        _Session(body=_HEADER.encode()),
        _Session(status=503, body=b""),
    ],
)
def test_a_refused_download_keeps_the_previous_copy(tmp_path, bad):
    """A refused download leaves the earlier file byte-identical."""
    _refresh(tmp_path, _Session(), today=TODAY - timedelta(days=3))
    db = tmp_path / "retraction_watch.db"
    before = db.read_bytes()
    copy = _refresh(tmp_path, bad)
    assert db.read_bytes() == before
    assert copy.state is ExportState.CURRENT and copy.data_as_of == TODAY - timedelta(days=3)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["retraction_watch.db"]


def test_a_copy_older_than_35_days_is_stale_and_not_consulted(tmp_path):
    """Day 35 is current, day 36 stale and unconsulted."""
    _refresh(tmp_path, _Session(), today=TODAY)
    assert export_copy(tmp_path, today=TODAY + timedelta(days=35)).state is ExportState.CURRENT
    stale = export_copy(tmp_path, today=TODAY + timedelta(days=36))
    assert stale.state is ExportState.STALE and not stale.consulted
    _refresh(tmp_path, _Session(), today=date.today() - timedelta(days=36))
    assert export_evidence(tmp_path, doi="10.1/paper", pmid=None) is None


def test_missing_and_unreadable_copies_are_not_consulted(tmp_path):
    """A missing or garbage file is never consulted."""
    assert export_copy(tmp_path, today=TODAY).state is ExportState.MISSING
    assert export_evidence(tmp_path, doi="10.1/paper", pmid=None) is None
    (tmp_path / "retraction_watch.db").write_bytes(b"garbage" * 100)
    copy = export_copy(tmp_path, today=TODAY)
    assert copy.state is ExportState.UNREADABLE and not copy.consulted
    assert export_evidence(tmp_path, doi="10.1/paper", pmid=None) is None


def test_no_identifier_is_not_consulted(tmp_path):
    """No usable identifier reads as not consulted."""
    _refresh(tmp_path, _Session(), today=date.today())
    assert export_evidence(tmp_path, doi=None, pmid=None) is None
    assert export_evidence(tmp_path, doi=None, pmid="abc") is None


def test_a_found_nothing_lookup_is_consulted(tmp_path):
    """An export that knows nothing of the paper answers an empty list."""
    _refresh(tmp_path, _Session(), today=date.today())
    assert export_evidence(tmp_path, doi="10.9/other", pmid=None) == []
