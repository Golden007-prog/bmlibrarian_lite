"""A PMID is canonical ASCII digits, or it is not sent (bmlib#470, #472, #478)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bmlibrarian_lite.identifiers import (
    PmidReading,
    canonical_pmid,
    is_canonical_pmid,
    read_caller_ids,
)

CONTRACT = (
    Path(__file__).resolve().parent.parent
    / "doc" / "cross_platform" / "fulltext_parity" / "pmid_canonical.json"
)
CASES = json.loads(CONTRACT.read_text(encoding="utf-8"))["cases"]


def test_the_contract_has_the_required_cases() -> None:
    """The contract has the required cases."""
    inputs = {c["input"] for c in CASES}
    for required in (
        "33301246", " 33301246 ", "0033301246", "", "0000", "PMID:33301246",
        "33301246abc", "1 OR 2", "1١٢", "-5", "PMC7745181",
    ):
        assert required in inputs


@pytest.mark.parametrize("case", CASES, ids=[repr(c["input"]) for c in CASES])
def test_contract_case(case: dict) -> None:
    """Contract case."""
    reading = canonical_pmid(case["input"])
    assert reading == PmidReading(case["pmid"], case["pmc_id"], case["refused"])


def test_integers() -> None:
    """Integers."""
    assert canonical_pmid(33301246).pmid == "33301246"
    assert canonical_pmid(0).pmid is None and not canonical_pmid(0).refused
    assert canonical_pmid(True).refused
    assert canonical_pmid(-5).refused


@pytest.mark.parametrize("value", [1.5, b"123", ["1"], object()])
def test_other_types_are_refused(value: object) -> None:
    """Other types are refused."""
    assert canonical_pmid(value).refused


def test_none_is_no_pmid() -> None:
    """None is no pmid."""
    assert canonical_pmid(None) == PmidReading()


def test_is_canonical_pmid() -> None:
    """Is canonical pmid."""
    assert is_canonical_pmid("33301246")
    assert not is_canonical_pmid("0033301246")
    assert not is_canonical_pmid(" 1 ")
    assert not is_canonical_pmid("PMC1")
    assert not is_canonical_pmid(33301246)
    assert not is_canonical_pmid(None)


def test_read_caller_ids_logs_a_refusal_naming_the_value(caplog) -> None:
    """Read caller ids logs a refusal naming the value."""
    with caplog.at_level(logging.WARNING):
        assert read_caller_ids("1 OR 2", "PMC9") == (None, "PMC9")
    assert "'1 OR 2'" in caplog.text


def test_read_caller_ids_pmc_id_fills_only_a_missing_pmcid(caplog) -> None:
    """Read caller ids pmc id fills only a missing pmcid."""
    assert read_caller_ids("PMC7745181", None) == (None, "PMC7745181")
    with caplog.at_level(logging.WARNING):
        assert read_caller_ids("PMC7745181", "PMC1") == (None, "PMC1")
    assert "PMC7745181" in caplog.text


def test_read_caller_ids_canonicalises_and_keeps_pmcid() -> None:
    """Read caller ids canonicalises and keeps pmcid."""
    assert read_caller_ids("0033301246", "PMC5") == ("33301246", "PMC5")
    assert read_caller_ids(None, None) == (None, None)


def test_read_caller_ids_newline_in_value_is_not_interpolated_raw(caplog) -> None:
    """Read caller ids newline in value is not interpolated raw."""
    with caplog.at_level(logging.WARNING):
        read_caller_ids("1\nFAKE LOG LINE x", None)
    assert "\nFAKE LOG LINE" not in caplog.text


# --- the entries ----------------------------------------------------------

def test_mcp_refused_pmid_makes_no_request() -> None:
    """Mcp refused pmid makes no request."""
    from bmlibrarian_lite.mcp_server import _handle_fulltext

    ctx = MagicMock()
    result = _handle_fulltext({"pmid": "1 OR 2"}, ctx)
    assert result["success"] is False
    assert "'1 OR 2'" in result["error"]
    ctx.fulltext_discoverer.discover_fulltext.assert_not_called()


def test_mcp_padded_pmid_is_sent_canonical() -> None:
    """Mcp padded pmid is sent canonical."""
    from bmlibrarian_lite.mcp_server import _handle_fulltext

    ctx = MagicMock()
    ctx.fulltext_discoverer.discover_fulltext.return_value = MagicMock(
        success=False, markdown_content=None, lookups=None,
        absence_established=False, error="x", set_aside_pdf_note=None,
    )
    try:
        _handle_fulltext({"pmid": "0033301246"}, ctx)
    except Exception:
        pass
    kwargs = ctx.fulltext_discoverer.discover_fulltext.call_args.kwargs
    assert kwargs["pmid"] == "33301246"


def test_mcp_refused_pmid_keeps_a_doi() -> None:
    """Mcp refused pmid keeps a doi."""
    from bmlibrarian_lite.mcp_server import _handle_fulltext

    ctx = MagicMock()
    try:
        _handle_fulltext({"pmid": "1 OR 2", "doi": "10.1/x"}, ctx)
    except Exception:
        pass
    kwargs = ctx.fulltext_discoverer.discover_fulltext.call_args.kwargs
    assert kwargs["pmid"] is None and kwargs["doi"] == "10.1/x"


def _discoverer():
    from bmlibrarian_lite.fulltext_discovery import FulltextDiscoverer

    return FulltextDiscoverer.__new__(FulltextDiscoverer)


def test_discover_fulltext_canonicalises_before_any_lookup(monkeypatch) -> None:
    """Discover fulltext canonicalises before any lookup."""
    seen: dict = {}
    d = _discoverer()

    def fake(self, doc_dict, pmid, pmcid, doi, title, year, skip_pdf, *, refusals):
        seen.update(doc_dict=doc_dict, pmid=pmid, pmcid=pmcid)
        return MagicMock(
            set_aside_pdf_note=None, success=True,
        )

    monkeypatch.setattr(type(d), "_discover_fulltext", fake)
    monkeypatch.setattr("bmlibrarian_lite.fulltext_discovery.replace", lambda r, **k: r)
    d.discover_fulltext(pmid="0033301246")
    assert seen["pmid"] == "33301246"
    d.discover_fulltext(doc_dict={"pmid": "1 OR 2", "doi": "10.1/x"})
    assert seen["pmid"] is None and "pmid" not in seen["doc_dict"]
    d.discover_fulltext(pmid="PMC7745181")
    assert seen["pmid"] is None and seen["pmcid"] == "PMC7745181"
    d.discover_fulltext(pmid="PMC7745181", pmcid="PMC1")
    assert seen["pmcid"] == "PMC1"


def test_europepmc_lookup_refuses_a_non_canonical_pmid(caplog) -> None:
    """Europepmc lookup refuses a non canonical pmid."""
    from bmlibrarian_lite.europepmc import EuropePMCClient

    client = EuropePMCClient.__new__(EuropePMCClient)
    client._session = MagicMock()
    with caplog.at_level(logging.ERROR):
        fetch = client.fetch_article_info(pmid="0033301246")
    client._session.get.assert_not_called()
    assert fetch.info is None and fetch.failure is not None
    assert "'0033301246'" in caplog.text


def test_europepmc_lookup_sends_a_canonical_pmid() -> None:
    """Europepmc lookup sends a canonical pmid."""
    from bmlibrarian_lite.europepmc import EuropePMCClient

    client = EuropePMCClient.__new__(EuropePMCClient)
    client._session = MagicMock()
    client._session.get.return_value.json.return_value = {
        "hitCount": 0, "resultList": {"result": []},
    }
    client.fetch_article_info(pmid="33301246")
    params = client._session.get.call_args.kwargs["params"]
    assert params["query"] == "ext_id:33301246 src:med"


def test_id_converter_refuses_a_non_canonical_pmid(caplog) -> None:
    """Id converter refuses a non canonical pmid."""
    from bmlibrarian_lite.pdf_discovery import PDFDiscoverer

    d = PDFDiscoverer.__new__(PDFDiscoverer)
    d._session = MagicMock()
    with caplog.at_level(logging.ERROR):
        pmcid, failure = d._get_pmcid_from_pmid("1 OR 2")
    d._session.get.assert_not_called()
    assert pmcid is None and failure is not None
    assert "'1 OR 2'" in caplog.text


def test_analyzer_pmid_entry(monkeypatch) -> None:
    """Analyzer pmid entry."""
    from bmlibrarian_lite.study_transparency_analyzer import study_transparency_analyzer as m

    a = m.StudyTransparencyAnalyzer.__new__(m.StudyTransparencyAnalyzer)
    a._auto_discover_fulltext = False
    seen: list = []
    monkeypatch.setattr(
        m.StudyTransparencyAnalyzer, "_fetch_basic_metadata",
        lambda self, report: seen.append((report.pmid, report.doi)) or (_ for _ in ()).throw(StopIteration),
    )
    for kwargs in ({"pmid": "0033301246"}, {"pmid": "1 OR 2", "doi": "10.1/x"}):
        try:
            a.analyze(**kwargs)
        except StopIteration:
            pass
    assert seen == [("33301246", None), (None, "10.1/x")]
    report = a.analyze(pmid="1 OR 2")
    assert report.errors and report.pmid is None


def test_discover_fulltext_updates_the_callers_dict_in_place(monkeypatch) -> None:
    """Discover fulltext updates the callers dict in place."""
    d = _discoverer()
    monkeypatch.setattr(
        type(d), "_discover_fulltext",
        lambda self, *a, refusals: MagicMock(set_aside_pdf_note=None, success=True),
    )
    monkeypatch.setattr("bmlibrarian_lite.fulltext_discovery.replace", lambda r, **k: r)
    doc = {"pmid": "0033301246"}
    d.discover_fulltext(doc_dict=doc)
    assert doc == {"pmid": "33301246"}


def test_an_object_whose_text_is_digits_is_still_refused() -> None:
    """Only text and integers are PMIDs; ``str()`` of anything else is not asked."""
    import decimal

    class Digits:
        def __str__(self) -> str:
            return "33301246"

    assert canonical_pmid(Digits()).refused
    assert canonical_pmid(decimal.Decimal(33301246)).refused
