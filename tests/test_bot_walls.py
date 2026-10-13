# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A bot wall's challenge page, recognised from the answer alone (#480).

The rows are the shared contract,
``doc/cross_platform/fulltext_parity/challenge_detection.json``; the Swift and
Android ports read the same rows.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from bmlibrarian_lite.bot_walls import (
    WALL_MARKERS,
    challenge_failure,
    challenge_markers,
)
from bmlibrarian_lite.constants import (
    CHALLENGE_BODY_PREFIX_BYTES,
    CHALLENGE_GONE_STATUSES,
    CLOUDFLARE_REFUSAL_STATUSES,
)
from bmlibrarian_lite.data_models import RequestFailure, RequestFailureKind

CONTRACT: dict[str, Any] = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "doc"
        / "cross_platform"
        / "fulltext_parity"
        / "challenge_detection.json"
    ).read_text(encoding="utf-8")
)


def _body(case: dict[str, Any]) -> bytes:
    """The case's body, after its padding."""
    return b" " * case.get("body_padding_bytes", 0) + case["body"].encode("utf-8")


def test_the_marker_table_is_the_contracts() -> None:
    """One table on every platform."""
    assert {name: list(needles) for name, needles in WALL_MARKERS.items()} == CONTRACT["markers"]


def test_the_bounds_are_the_contracts() -> None:
    """The prefix, the gone statuses and Cloudflare's refusals."""
    assert CHALLENGE_BODY_PREFIX_BYTES == CONTRACT["body_prefix_bytes"]
    assert set(CHALLENGE_GONE_STATUSES) == set(CONTRACT["gone_statuses"])
    assert set(CLOUDFLARE_REFUSAL_STATUSES) == set(CONTRACT["cloudflare_refusal_statuses"])


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda c: c["name"])
def test_each_case(case: dict[str, Any]) -> None:
    """Each row's markers, in sorted order."""
    assert list(challenge_markers(case["status"], case["headers"], _body(case))) == case["expected"]


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda c: c["name"])
def test_a_challenge_is_recorded_with_its_status(case: dict[str, Any]) -> None:
    """Challenged rows become the kind, with the status; the rest none."""
    failure = challenge_failure(case["status"], case["headers"], _body(case))
    if case["expected"]:
        assert failure == RequestFailure(RequestFailureKind.CHALLENGED, case["status"])
    else:
        assert failure is None


def test_binary_that_is_not_a_pdf_is_no_challenge() -> None:
    """A JPEG listed as a PDF, invalid UTF-8 throughout (Review Focus 3)."""
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes(range(128, 256)) * 8
    assert challenge_markers(200, {}, jpeg) == ()


def test_lowercasing_is_ascii_only() -> None:
    """str.lower() turns the Kelvin sign into 'k', which would match; no platform does."""
    assert challenge_markers(200, {}, "AKAMAIGHOST".encode()) == ()
