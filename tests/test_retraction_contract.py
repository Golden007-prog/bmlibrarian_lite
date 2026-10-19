"""Lite's copy of bmlib's retraction-status contract, run through bmlib's API.

The fixture is copied verbatim from bmlib and never edited here; these
runners mirror bmlib's own ``tests/test_retraction_status.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from bmlib.publications.retraction_status import (
    RetractionEvidence,
    crossref_retraction_requests,
    parse_crossref_retraction_response,
    retraction_status,
)

FIXTURE = json.loads(
    (
        Path(__file__).parent.parent
        / "doc" / "cross_platform" / "retraction_parity" / "retraction_status.json"
    ).read_text(encoding="utf-8")
)


def _body(case: dict[str, Any]) -> bytes:
    if "body_text" in case:
        return case["body_text"].encode("utf-8")
    return json.dumps(case["body"]).encode("utf-8")


def _without_reason(result: dict[str, Any]) -> dict[str, Any]:
    assert result["outcome"] != "unreachable" or result["reason"], "an unreachable result names why"
    return {key: value for key, value in result.items() if key != "reason"}


@pytest.mark.parametrize("case", FIXTURE["rule_cases"], ids=lambda c: c["name"])
def test_rule_case(case: dict[str, Any]) -> None:
    """A rule case's verdict equals the expected one."""
    crossref = None
    if case["crossref"] is not None:
        (crossref,) = parse_crossref_retraction_response(
            case["crossref"]["status"],
            _body(case["crossref"]),
            [case["crossref"].get("for", case["doi"])],
        ).values()
    export = case["export"]
    verdict = retraction_status(
        doi=case["doi"],
        export=None if export is None else [RetractionEvidence.from_dict(e) for e in export],
        crossref=crossref,
        publication_types=case["publication_types"],
    )
    assert verdict.to_dict() == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["parse_cases"], ids=lambda c: c["name"])
def test_parse_case(case: dict[str, Any]) -> None:
    """A Crossref answer parses to the expected results."""
    results = parse_crossref_retraction_response(case["status"], _body(case), case["requested"])
    got = {doi: _without_reason(result.to_dict()) for doi, result in results.items()}
    assert got == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["request_cases"], ids=lambda c: c["name"])
def test_request_case(case: dict[str, Any]) -> None:
    """The Crossref requests built equal the expected ones."""
    requests = crossref_retraction_requests(case["dois"], case["mailto"])
    got = {
        "batches": [{"url": b.url, "dois": list(b.dois)} for b in requests.batches],
        "unsendable": list(requests.unsendable),
    }
    assert got == case["expected"]


def test_the_copy_is_the_pinned_bmlib_s() -> None:
    """An older fixture predates ``crossref.for`` (bmlib#508); fail loudly."""
    assert "crossref.for" in FIXTURE["_readme"]
