# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Bot-walled PDFs fetched by an embedded browser: the queue's rules (#480, stage 2).

A PDF that an open-access source names, and whose host answered our client
with a bot wall's challenge (``RequestFailureKind.CHALLENGED``), is queued
and fetched later by a real embedded web view
(``gui/browser_rescue_service.py``). This module holds what that needs and
imports neither Qt nor SQLite: which failures are queued, what one browser
visit concluded, and how a queue entry moves between states.
``rescue_queue_store`` only persists these decisions.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from .bot_walls import challenge_markers
from .constants import (
    CHALLENGE_BODY_PREFIX_BYTES,
    CHALLENGE_GONE_STATUSES,
    HTTP_STATUS_CODE_MAX,
    HTTP_STATUS_CODE_MIN,
    MAX_RESCUE_ATTEMPTS,
    MS_PER_SECOND,
    PDF_MAGIC_BYTES,
    RESCUE_PRIORITY_DEFAULT,
    SERVICE_OPENALEX_PDF,
    SERVICE_UNPAYWALL_PDF,
)
from .data_models import LookupRecord, RequestFailureKind
from .exceptions import SQLiteError
from .pdf_utils import generate_pdf_path
from .polite_session import is_loopback_host
from .rate_limit import limiter_for

logger = logging.getLogger(__name__)

#: The services whose challenged PDFs are queued: copies an open-access
#: source named, recorded with their address. The DOI download records none.
RESCUABLE_SERVICES = frozenset({SERVICE_UNPAYWALL_PDF, SERVICE_OPENALEX_PDF})

#: The address schemes a web view is sent to; any other address is not queued.
_FETCHABLE_SCHEMES = frozenset({"http", "https"})

#: The status the challenge rules are handed when the browser saw none: it
#: is no gone status and no Cloudflare refusal.
_NO_HTTP_STATUS = 0


class RescueState(Enum):
    """Where a queue entry stands. Stored as its value: never rename one."""

    #: Waiting for the hidden browser.
    PENDING = "pending"
    #: Still a challenge after a visit: waits for a person (stage 3's window).
    NEEDS_PERSON = "needs_person"
    #: The PDF arrived and was saved.
    RESCUED = "rescued"
    #: The page loaded, unchallenged, and served no PDF (an abstract page).
    NO_PDF = "no_pdf"
    #: The browser saw 404 or 410.
    GONE = "gone"
    #: The PDF is larger than this application downloads (``MAX_PDF_SIZE``).
    TOO_LARGE = "too_large"
    #: The reader set it aside (stage 3).
    DISMISSED = "dismissed"
    #: Another address of the same document was rescued.
    SUPERSEDED = "superseded"


#: The states the browser still visits.
OPEN_RESCUE_STATES = frozenset({RescueState.PENDING, RescueState.NEEDS_PERSON})


class RescueOutcome(Enum):
    """What one browser visit concluded. Stored as its value: never rename one."""

    PDF = "pdf"
    CHALLENGE = "challenge"
    NO_PDF = "no_pdf"
    GONE = "gone"
    TOO_LARGE = "too_large"
    #: The visit learnt nothing: a network error, a renderer crash, a stalled
    #: or interrupted download, or a PDF that arrived and could not be kept.
    TRANSPORT = "transport"


#: The state each answering outcome settles an open entry in. A transport
#: failure is decided by the attempt count instead (:func:`next_state`).
_SETTLED_BY: dict[RescueOutcome, RescueState] = {
    RescueOutcome.PDF: RescueState.RESCUED,
    RescueOutcome.CHALLENGE: RescueState.NEEDS_PERSON,
    RescueOutcome.NO_PDF: RescueState.NO_PDF,
    RescueOutcome.GONE: RescueState.GONE,
    RescueOutcome.TOO_LARGE: RescueState.TOO_LARGE,
}

assert set(_SETTLED_BY) | {RescueOutcome.TRANSPORT} == set(RescueOutcome), (
    "every rescue outcome needs the state it leaves an entry in"
)


@dataclass(frozen=True)
class RescueVerdict:
    """What one browser visit concluded, as the queue stores and shows it.

    Attributes:
        outcome: What the visit came to.
        http_status: The main frame's last HTTP status, for a page the visit
            stayed on; ``None`` for a download or when none was seen.
        markers: The defences the page named, for a challenge.
    """

    outcome: RescueOutcome
    http_status: int | None = None
    markers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """The verdict as JSON-safe values.

        Returns:
            ``outcome``, ``http_status`` and ``markers``.
        """
        return {
            "outcome": self.outcome.value,
            "http_status": self.http_status,
            "markers": list(self.markers),
        }

    @classmethod
    def from_dict(cls, data: Any) -> RescueVerdict | None:
        """Read a stored verdict, untrusted.

        A verdict is shown, never acted on, so one this build cannot read
        (a newer build's outcome, or damage) is dropped rather than guessed.

        Args:
            data: The decoded JSON.

        Returns:
            The verdict, keeping only a status from 100 to 999 and text
            markers; ``None`` when its outcome cannot be read.
        """
        if not isinstance(data, dict):
            return None
        try:
            outcome = RescueOutcome(data.get("outcome"))
        except (ValueError, TypeError):
            return None
        status = data.get("http_status")
        if not (
            isinstance(status, int)
            and not isinstance(status, bool)
            and HTTP_STATUS_CODE_MIN <= status <= HTTP_STATUS_CODE_MAX
        ):
            status = None
        raw = data.get("markers")
        markers = tuple(m for m in raw if isinstance(m, str)) if isinstance(raw, list) else ()
        return cls(outcome, status, markers)


def next_state(state: RescueState, verdict: RescueVerdict, attempts: int) -> RescueState:
    """Where an entry stands after a visit.

    Args:
        state: Where it stood.
        verdict: What the visit concluded.
        attempts: Visits made, this one included.

    Returns:
        A settled entry's own state, unchanged. For an open one, the state
        the outcome settles it in; after a transport failure, ``PENDING``
        until ``MAX_RESCUE_ATTEMPTS`` is reached, then ``NEEDS_PERSON``, so
        no entry is retried for ever. An entry already waiting for a person
        stays there.
    """
    if state not in OPEN_RESCUE_STATES:
        return state
    if verdict.outcome is RescueOutcome.TRANSPORT:
        if state is RescueState.PENDING and attempts < MAX_RESCUE_ATTEMPTS:
            return RescueState.PENDING
        return RescueState.NEEDS_PERSON
    return _SETTLED_BY[verdict.outcome]


def page_verdict(status: int | None, headers: Mapping[str, str], html: str) -> RescueVerdict:
    """What a visit concluded from the page it stayed on, with no download.

    A gone status (404, 410) is gone whatever the page says, as discovery
    rules. Any other page goes through the challenge rules a download's
    answer goes through (``bot_walls.challenge_markers``), on its first
    ``CHALLENGE_BODY_PREFIX_BYTES`` as UTF-8: a bounded read of a lookup
    page, not research content.

    Args:
        status: The main frame's last HTTP status; ``None`` when none was seen.
        headers: That answer's headers.
        html: The page as the browser holds it, serialised.

    Returns:
        ``GONE``, ``CHALLENGE`` with the defences named, or ``NO_PDF``; each
        with the status.
    """
    if status in CHALLENGE_GONE_STATUSES:
        return RescueVerdict(RescueOutcome.GONE, status)
    body = html.encode("utf-8", errors="replace")[:CHALLENGE_BODY_PREFIX_BYTES]
    markers = challenge_markers(_NO_HTTP_STATUS if status is None else status, headers, body)
    if markers:
        return RescueVerdict(RescueOutcome.CHALLENGE, status, markers)
    return RescueVerdict(RescueOutcome.NO_PDF, status)


def download_verdict(first_bytes: bytes) -> RescueVerdict:
    """What a finished download concluded: the PDF, or something served as one.

    Args:
        first_bytes: The start of the downloaded file.

    Returns:
        ``PDF`` when it begins with ``%PDF``; ``NO_PDF`` otherwise.
    """
    if first_bytes.startswith(PDF_MAGIC_BYTES):
        return RescueVerdict(RescueOutcome.PDF)
    return RescueVerdict(RescueOutcome.NO_PDF)


def _stripped_or_none(value: str | None) -> str | None:
    """A value stripped, or ``None`` when blank.

    Args:
        value: Any text, or ``None``.

    Returns:
        The stripped text, or ``None``.
    """
    if value is None:
        return None
    return value.strip() or None


@dataclass(frozen=True)
class RescueSubject:
    """The document whose walled PDFs are queued, as the run knew it.

    Attributes:
        document_id: The document's ID in storage, or the MCP server's own.
        doi: Its DOI; names the file a rescued PDF is saved as.
        pmid: Its PubMed ID, if known.
        title: Its title, for the queue window.

    Raises:
        ValueError: On construction, if the document ID is blank.
    """

    document_id: str
    doi: str | None
    pmid: str | None = None
    title: str = ""

    def __post_init__(self) -> None:
        """Refuse a nameless document, and strip what is given."""
        if not self.document_id or not self.document_id.strip():
            raise ValueError("A rescue names the document it is for")
        object.__setattr__(self, "document_id", self.document_id.strip())
        object.__setattr__(self, "doi", _stripped_or_none(self.doi))
        object.__setattr__(self, "pmid", _stripped_or_none(self.pmid))
        object.__setattr__(self, "title", (self.title or "").strip())


@dataclass(frozen=True)
class RescueRequest:
    """One walled PDF to queue.

    Attributes:
        document_id: The document it belongs to.
        doi: The document's DOI.
        pmid: The document's PubMed ID, if known.
        title: The document's title.
        address: The PDF's address.
        host: The address's host, which the visit is paced by.
        service: Who named the address (``SERVICE_UNPAYWALL_PDF``,
            ``SERVICE_OPENALEX_PDF``).
    """

    document_id: str
    doi: str
    pmid: str | None
    title: str
    address: str
    host: str
    service: str


@dataclass(frozen=True)
class RescueEntry:
    """A queued walled PDF, as the queue holds it.

    Attributes:
        id: The row's ID.
        request: What was queued.
        state: Where it stands.
        attempts: Browser visits made.
        verdict: The last visit's verdict; ``None`` before one, or when it
            could not be read.
        priority: ``RESCUE_PRIORITY_DEFAULT``, or higher for an entry a
            reader is waiting on.
        created_at: When it was queued, as stored.
        updated_at: When it last changed, as stored.
    """

    id: int
    request: RescueRequest
    state: RescueState
    attempts: int
    verdict: RescueVerdict | None
    priority: int
    created_at: str
    updated_at: str


def rescue_requests(subject: RescueSubject, record: LookupRecord) -> tuple[RescueRequest, ...]:
    """The walled PDFs in a discovery's record that the browser should try.

    A challenged failure of an open-access copy, with an ``http(s)`` address,
    for a document with a DOI: the DOI names the file the PDF is saved as,
    which the next discovery's cached-PDF tier finds (:func:`rescue_pdf_path`).
    Each address once, in the record's order.

    Args:
        subject: The document the record is about.
        record: What its discovery left unsettled.

    Returns:
        The requests; empty when nothing was challenged.
    """
    challenged = [
        failure
        for failure in record.failures
        if failure.failure.kind is RequestFailureKind.CHALLENGED
        and failure.service in RESCUABLE_SERVICES
        and failure.address
    ]
    if not challenged:
        return ()
    if subject.doi is None:
        # Unpaywall and OpenAlex are asked only by DOI, so this is a caller's
        # slip; without a DOI no file name is known for the PDF.
        logger.warning(
            "Not queueing %d walled PDF(s) of document %s: it has no DOI",
            len(challenged),
            subject.document_id,
        )
        return ()
    requests: list[RescueRequest] = []
    seen: set[str] = set()
    for failure in challenged:
        address = failure.address or ""
        parsed = urlparse(address)
        if parsed.scheme.lower() not in _FETCHABLE_SCHEMES or not parsed.hostname:
            continue
        if address in seen:
            continue
        seen.add(address)
        requests.append(
            RescueRequest(
                document_id=subject.document_id,
                doi=subject.doi,
                pmid=subject.pmid,
                title=subject.title,
                address=address,
                host=parsed.hostname,
                service=failure.service,
            )
        )
    return tuple(requests)


def rescue_pdf_path(doi: str, base_dir: Path | None = None) -> Path:
    """Where a rescued PDF is saved: where the next discovery looks first.

    The analysis asks discovery by DOI with no year, so its cached-PDF tier
    expects ``generate_pdf_path({"doi": doi})``; ``find_existing_pdf`` also
    finds that file for a document that has a year.

    Args:
        doi: The document's DOI, as the run held it.
        base_dir: The PDF library; the configured one when ``None``.

    Returns:
        The path.
    """
    return generate_pdf_path({"doi": doi}, base_dir)


class RescueQueue(Protocol):
    """Where walled PDFs are queued (``LiteStorage``)."""

    def enqueue_rescues(self, requests: Sequence[RescueRequest], priority: int = ...) -> int:
        """Queue walled PDFs.

        Args:
            requests: What to queue.
            priority: The entries' priority.

        Returns:
            How many entries were added or raised.
        """
        ...


def queue_challenged_copies(
    queue: RescueQueue,
    subject: RescueSubject,
    record: LookupRecord,
    priority: int = RESCUE_PRIORITY_DEFAULT,
) -> int:
    """Queue the walled PDFs a discovery left, for the browser.

    A queue that cannot be written is logged and not raised. The reader's
    sentence for the copy ("could not be asked … may exist") is already
    true, so only a later try is lost; the analysis must not fail for it.

    Args:
        queue: The storage.
        subject: The document.
        record: What its discovery left unsettled.
        priority: The entries' priority.

    Returns:
        How many entries were added or raised; 0 when nothing was queued.
    """
    requests = rescue_requests(subject, record)
    if not requests:
        return 0
    try:
        changed = queue.enqueue_rescues(requests, priority)
    except (sqlite3.Error, SQLiteError) as e:
        logger.error(
            "Could not queue %d walled PDF(s) of document %s for the browser: %s",
            len(requests),
            subject.document_id,
            e,
        )
        return 0
    if changed:
        logger.info(
            "Queued %d walled PDF(s) of document %s for the browser",
            changed,
            subject.document_id,
        )
    return changed


def rescue_delay_ms(host: str) -> int:
    """How long a visit to ``host`` waits for its pacing slot, claimed now.

    The browser reaches the publisher hosts our requests reach, so it takes
    its slot from the same per-host limiter
    (``doc/cross_platform/polite_request_pacing.md``). The slot is claimed
    without sleeping; the service schedules the visit for then. Loopback is
    never paced.

    Args:
        host: The address's host.

    Returns:
        Milliseconds to wait, rounded up.
    """
    if is_loopback_host(host):
        return 0
    return math.ceil(limiter_for(host).claim() * MS_PER_SECOND)


def reanalyse_rescued(
    entry: RescueEntry,
    stored_document: Callable[[str], object | None],
    reanalyse: Callable[[str, str | None, str | None], None],
) -> bool:
    """Re-analyse the document a rescued PDF belongs to, when storage holds it.

    By the entry's DOI, which names the saved PDF, so the analysis's
    cached-PDF tier finds it. The MCP server queues under its own document
    ID (``doi-...``), which storage need not hold: such a rescue is not
    re-analysed, as that would store a transparency row for no document. Its
    PDF is saved all the same, for the next request.

    Args:
        entry: The rescued entry.
        stored_document: Reads a document from storage by ID.
        reanalyse: Re-analyses ``(document_id, pmid, doi)``.

    Returns:
        Whether a re-analysis was asked for.
    """
    request = entry.request
    if stored_document(request.document_id) is None:
        logger.info(
            "Rescued the PDF of %s, which storage does not hold; nothing to re-analyse",
            request.document_id,
        )
        return False
    reanalyse(request.document_id, request.pmid, request.doi)
    return True
