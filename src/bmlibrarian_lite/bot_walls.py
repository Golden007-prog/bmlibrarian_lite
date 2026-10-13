# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Recognise a bot wall's challenge page from an answer alone (#480).

A source that names an open-access PDF can be walled against programs: the
answer is a challenge page (a 200, or a 403/503 from Cloudflare) in place of
the PDF. That says nothing about the article, so it is recorded as its own
kind, ``CHALLENGED``, never as the host's answer. The rules and the shared
cases are ``doc/cross_platform/fulltext_parity/challenge_detection.json``;
the survey that measured them is ``doc/developer/unpaywall_pdf_survey/``.

Pure functions only: no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping

from .constants import (
    CHALLENGE_BODY_PREFIX_BYTES,
    CHALLENGE_GONE_STATUSES,
    CLOUDFLARE_REFUSAL_STATUSES,
    CLOUDFLARE_SERVER,
    PDF_MAGIC_BYTES,
)
from .data_models import RequestFailure, RequestFailureKind

__all__ = [
    "WALL_MARKERS",
    "challenge_failure",
    "challenge_markers",
    "named_defences",
]

#: The defences against programs, by name, and the lowercase needles each is
#: recognised by in a page's start or a header line. Equal to the contract's
#: ``markers`` table on every platform.
WALL_MARKERS: dict[str, tuple[str, ...]] = {
    "akamai": ("akamaighost", "errors.edgesuite.net", "you don't have permission to access"),
    "anubis": ("anubis", "making sure you're not a bot"),
    "aws-waf": ("awswaf", "aws-waf-token", "x-amzn-waf-action"),
    "captcha": ("g-recaptcha", "hcaptcha", "turnstile", "captcha"),
    "cloudflare": (
        "just a moment",
        "cf-chl",
        "challenge-platform",
        "cf-mitigated",
        "attention required! | cloudflare",
    ),
    "datadome": ("datadome", "captcha-delivery.com"),
    "imperva": ("incapsula", "_incapsula_resource", "visid_incap"),
    "perimeterx": ("px-captcha", "perimeterx", "_pxhd"),
    "proof-of-work": (
        "proof of work",
        "pow-challenge",
        "checking your browser",
        "preparing to download",
    ),
    "robot-text": (
        "verify you are human",
        "are you a robot",
        "unusual traffic",
        "not a robot",
        "automated access",
        "bot detection",
        "ai crawlers",
        "hostile web agent",
    ),
}

# A-Z to a-z and nothing else: str.lower() also folds non-ASCII letters (the
# Kelvin sign to "k"), which Swift's and Kotlin's ports would not match.
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _ascii_lower(text: str) -> str:
    """Lowercase ASCII letters only.

    Args:
        text: Any text.

    Returns:
        ``text`` with its A-Z letters made a-z; every other character unchanged.
    """
    return text.translate(_ASCII_LOWER)


def named_defences(text: str, headers: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The defences a page's text and its header lines name.

    Args:
        text: The start of a non-PDF body, any case.
        headers: The answer's headers, when there are any; each is matched as
            the line ``name: value``.

    Returns:
        The sorted names in :data:`WALL_MARKERS` that matched.
    """
    haystacks = [_ascii_lower(text)]
    if headers:
        haystacks.extend(_ascii_lower(f"{name}: {value}") for name, value in headers.items())
    return tuple(
        sorted(
            name
            for name, needles in WALL_MARKERS.items()
            if any(needle in haystack for haystack in haystacks for needle in needles)
        )
    )


def _server(headers: Mapping[str, str]) -> str:
    """The ``Server`` header, trimmed and ASCII-lowercased; ``""`` without one.

    Args:
        headers: The answer's headers, in any name case.

    Returns:
        The server's name as the rule compares it.
    """
    for name, value in headers.items():
        if _ascii_lower(name) == "server":
            return _ascii_lower(value.strip())
    return ""


def challenge_markers(
    status: int, headers: Mapping[str, str], body_prefix: bytes
) -> tuple[str, ...]:
    """The defences an answer names, if it is a challenge page.

    Args:
        status: The answer's HTTP status.
        headers: The answer's headers.
        body_prefix: The start of its body; only the first
            :data:`~bmlibrarian_lite.constants.CHALLENGE_BODY_PREFIX_BYTES`
            are read.

    Returns:
        The sorted defence names; empty when the answer is not a challenge:
        a gone status (404, 410), a body that is a PDF, or nothing named.
    """
    if status in CHALLENGE_GONE_STATUSES:
        return ()
    body = body_prefix[:CHALLENGE_BODY_PREFIX_BYTES]
    if body.startswith(PDF_MAGIC_BYTES):
        return ()
    found = set(named_defences(body.decode("utf-8", errors="replace"), headers))
    if status in CLOUDFLARE_REFUSAL_STATUSES and _server(headers) == CLOUDFLARE_SERVER:
        # Cloudflare's own refusal, whatever the page says: ScienceDirect's
        # challenge markup lies beyond the bytes read.
        found.add("cloudflare")
    return tuple(sorted(found))


def challenge_failure(
    status: int, headers: Mapping[str, str], body_prefix: bytes
) -> RequestFailure | None:
    """The failure to record for a challenge page, or ``None`` for any other answer.

    Args:
        status: The answer's HTTP status.
        headers: The answer's headers.
        body_prefix: The start of its body.

    Returns:
        ``RequestFailure(CHALLENGED, status)`` when :func:`challenge_markers`
        names a defence; ``None`` otherwise.
    """
    if not challenge_markers(status, headers, body_prefix):
        return None
    return RequestFailure(RequestFailureKind.CHALLENGED, status)
