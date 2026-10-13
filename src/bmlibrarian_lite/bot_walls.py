# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Recognise a bot wall's challenge page from an answer alone (#480).

A source that names an open-access PDF can be walled against programs: the
answer is a challenge page in place of the PDF, served with any status but a
gone 404 or 410 (a 403 or 503 from Cloudflare is one whatever its page says). That says nothing about the article, so it is recorded as its own
kind, ``CHALLENGED``, never as the host's answer. The rules and the shared
cases are ``doc/cross_platform/fulltext_parity/challenge_detection.json``;
the survey that measured them is ``doc/developer/unpaywall_pdf_survey/``.

Pure functions only: no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .constants import (
    CHALLENGE_BODY_PREFIX_BYTES,
    CHALLENGE_GONE_STATUSES,
    CLOUDFLARE_REFUSAL_STATUSES,
    CLOUDFLARE_SERVER,
    HTTP_HEADER_WHITESPACE,
    PDF_MAGIC_BYTES,
)
from .data_models import RequestFailure, RequestFailureKind

__all__ = [
    "CLOUDFLARE_DEFENCE",
    "WALL_HEADER_MARKERS",
    "WALL_MARKERS",
    "challenge_failure",
    "challenge_markers",
    "named_defences",
]

#: The defences against programs, by name, and the lowercase needles each is
#: recognised by in the start of a page. Equal to the contract's ``markers``
#: table on every platform. Header lines are not searched for these: a
#: defence's cookie, its CDN's ``Server`` value or a CSP naming a captcha comes
#: with ordinary answers too.
WALL_MARKERS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        # Akamai's refusal carries no Server header, and escapes its reference
        # URL as entities; "you don't have permission to access" is also
        # Apache's stock 403 text, so it is no needle.
        "akamai": ("errors.edgesuite.net", "errors&#46;edgesuite&#46;net"),
        "anubis": ("anubis", "making sure you're not a bot"),
        "aws-waf": ("awswaf", "aws-waf-token"),
        "captcha": ("g-recaptcha", "hcaptcha", "turnstile", "captcha"),
        # "_cf_chl_opt" is the challenge's own script; "challenge-platform"
        # also names the bot-management script Cloudflare adds to ordinary pages.
        "cloudflare": ("just a moment", "cf-chl", "_cf_chl_opt", "attention required! | cloudflare"),
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
)

#: The header names, lowercase, that a defence sends only with a challenge.
#: Matched as whole names; values are not read. Equal to the contract's
#: ``header_markers`` table on every platform.
WALL_HEADER_MARKERS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "aws-waf": ("x-amzn-waf-action",),
        "cloudflare": ("cf-mitigated",),
    }
)

#: The defence named by Cloudflare's own refusal (a key of both tables).
CLOUDFLARE_DEFENCE = "cloudflare"

# A-Z to a-z and nothing else: str.lower() also folds non-ASCII letters (the
# Kelvin sign to "k"), which the Swift and Kotlin ports deliberately do not do,
# so all three agree.
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
    """The defences a page's text and its header names name.

    Args:
        text: The start of a non-PDF body, any case; searched for
            :data:`WALL_MARKERS`.
        headers: The answer's headers, when there are any; their names are
            compared whole with :data:`WALL_HEADER_MARKERS`.

    Returns:
        The sorted defence names that matched.
    """
    page = _ascii_lower(text)
    found = {
        name for name, needles in WALL_MARKERS.items() if any(needle in page for needle in needles)
    }
    if headers:
        names = {_ascii_lower(name) for name in headers}
        found.update(
            name for name, needles in WALL_HEADER_MARKERS.items() if names.intersection(needles)
        )
    return tuple(sorted(found))


def _server(headers: Mapping[str, str]) -> str:
    """The ``Server`` header, trimmed of HTTP whitespace and ASCII-lowercased.

    Args:
        headers: The answer's headers, in any name case.

    Returns:
        The server's name as the rule compares it; ``""`` without one.
    """
    for name, value in headers.items():
        if _ascii_lower(name) == "server":
            return _ascii_lower(value.strip(HTTP_HEADER_WHITESPACE))
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
        found.add(CLOUDFLARE_DEFENCE)
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
