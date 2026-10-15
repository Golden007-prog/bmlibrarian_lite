# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Bot-walled PDFs fetched by an embedded browser: the queue's rules (#480, stages 2 and 3).

A PDF that an open-access source names, and whose host answered our client
with a bot wall's challenge (``RequestFailureKind.CHALLENGED``), is queued
and fetched later by a real embedded web view
(``gui/browser_rescue_service.py``). This module holds what that needs. It
imports no Qt and touches no database (``sqlite3`` is imported only to catch
its errors): which failures are queued, what one browser visit concluded, and
how a queue entry moves between states.
``rescue_queue_store`` only persists these decisions.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, assert_never
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
    RESCUE_PENALISING_STATUSES,
    RESCUE_PRIORITY_DEFAULT,
    RESCUE_REFUSED_PAGE_STATUSES,
    RESCUE_UNANSWERED_PAGE_STATUSES,
    SERVICE_OPENALEX_PDF,
    SERVICE_UNPAYWALL_PDF,
)
from .data_models import LookupRecord, RequestFailureKind
from .exceptions import SQLiteError
from .pdf_title_check import TitleCheck, TitleCheckOutcome
from .pdf_utils import generate_pdf_path
from .polite_session import is_loopback_host, retry_after_from_headers
from .rate_limit import limiter_for

if TYPE_CHECKING:
    from .rescue_queue_store import RecordedVerdict

logger = logging.getLogger(__name__)

#: The services whose challenged PDFs are queued: copies an open-access
#: source named, recorded with their address. The DOI download records none.
RESCUABLE_SERVICES = frozenset({SERVICE_UNPAYWALL_PDF, SERVICE_OPENALEX_PDF})

#: The address schemes a web view is sent to; any other address is not queued.
_FETCHABLE_SCHEMES = frozenset({"http", "https"})

#: The status the challenge rules are handed for a download, which carries
#: none: it is no gone status and no Cloudflare refusal.
_NO_HTTP_STATUS = 0


class RescueState(Enum):
    """Where a queue entry stands. Stored as its value: never rename one."""

    #: Waiting for the hidden browser.
    PENDING = "pending"
    #: Waits for a person (stage 3's window): still a challenge after a visit,
    #: a refusal (401, 403) with no marker, a PDF that was not the article's
    #: (#532), or a transport failure on the last of ``MAX_RESCUE_ATTEMPTS``.
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


#: The open states: a verdict can still change them. The hidden pass visits
#: only ``PENDING``; ``NEEDS_PERSON`` is visited from stage 3's window.
OPEN_RESCUE_STATES = frozenset({RescueState.PENDING, RescueState.NEEDS_PERSON})


class RescueOutcome(Enum):
    """What one browser visit concluded. Stored as its value: never rename one."""

    PDF = "pdf"
    CHALLENGE = "challenge"
    NO_PDF = "no_pdf"
    GONE = "gone"
    TOO_LARGE = "too_large"
    #: A PDF arrived whose first page does not carry the article's title, so
    #: it was not kept (#532). The right PDF may still be on the page, so the
    #: entry waits for a person rather than settling as an absence.
    WRONG_PDF = "wrong_pdf"
    #: The visit learnt nothing: a network error, a renderer crash, a stalled
    #: or interrupted download, or a PDF that arrived and could not be kept
    #: or read.
    TRANSPORT = "transport"


#: The state each answering outcome settles an open entry in. A transport
#: failure is decided by the attempt count instead (:func:`next_state`).
_SETTLED_BY: dict[RescueOutcome, RescueState] = {
    RescueOutcome.PDF: RescueState.RESCUED,
    RescueOutcome.CHALLENGE: RescueState.NEEDS_PERSON,
    RescueOutcome.NO_PDF: RescueState.NO_PDF,
    RescueOutcome.GONE: RescueState.GONE,
    RescueOutcome.TOO_LARGE: RescueState.TOO_LARGE,
    RescueOutcome.WRONG_PDF: RescueState.NEEDS_PERSON,
}

# Every outcome but TRANSPORT settles an entry; a test holds this (an
# import-time assert would vanish under ``python -O``)


def http_status_or_none(value: object) -> int | None:
    """A value as an HTTP status, or ``None`` when it cannot be one.

    Args:
        value: A status from the browser or from storage, untrusted.

    Returns:
        The value when it is an integer from 100 to 999; ``None`` otherwise.
    """
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and HTTP_STATUS_CODE_MIN <= value <= HTTP_STATUS_CODE_MAX
    ):
        return value
    return None


@dataclass(frozen=True)
class RescueVerdict:
    """What one browser visit concluded, as the queue stores and shows it.

    Attributes:
        outcome: What the visit came to.
        http_status: The main frame's last HTTP status, for a page the visit
            stayed on; ``None`` for a download or when none was seen.
        markers: The defences the page named, for a challenge.

    Raises:
        ValueError: On construction, for a status that is no HTTP status, or
            markers on anything but a challenge.
    """

    outcome: RescueOutcome
    http_status: int | None = None
    markers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse a verdict no visit can reach."""
        if self.http_status is not None and http_status_or_none(self.http_status) is None:
            raise ValueError(f"Not an HTTP status: {self.http_status!r}")
        if self.markers and self.outcome is not RescueOutcome.CHALLENGE:
            raise ValueError(f"Only a challenge names defences, not {self.outcome.value}")

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
            The verdict, keeping only a status from 100 to 999 and, for a
            challenge, text markers; ``None`` when its outcome cannot be read.
        """
        if not isinstance(data, dict):
            return None
        try:
            outcome = RescueOutcome(data.get("outcome"))
        except (ValueError, TypeError):
            return None
        raw = data.get("markers")
        markers = (
            tuple(m for m in raw if isinstance(m, str))
            if isinstance(raw, list) and outcome is RescueOutcome.CHALLENGE
            else ()
        )
        return cls(outcome, http_status_or_none(data.get("http_status")), markers)


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


def dismissed_state(state: RescueState) -> RescueState:
    """Where an entry stands after the reader dismisses it.

    Args:
        state: Where it stood.

    Returns:
        ``DISMISSED`` for an open entry; a settled entry's own state, since a
        dismissal never reopens or rewrites a verdict.
    """
    return RescueState.DISMISSED if state in OPEN_RESCUE_STATES else state


def awaiting_reader_states(hidden_pass_runs: bool) -> frozenset[RescueState]:
    """The states of the entries that wait for the reader.

    Args:
        hidden_pass_runs: Whether a hidden pass will visit pending entries:
            the setting is on and QtWebEngine started.

    Returns:
        ``NEEDS_PERSON``; with no hidden pass, every open state, since no one
        but the reader will visit a pending entry.
    """
    if hidden_pass_runs:
        return frozenset({RescueState.NEEDS_PERSON})
    return OPEN_RESCUE_STATES


def stamp(moment: datetime) -> str:
    """A moment as stored: UTC ISO 8601 to the microsecond, so text order is time order.

    Args:
        moment: A timezone-aware moment.

    Returns:
        The stored text.

    Raises:
        ValueError: If ``moment`` is naive: it would be read as local time,
            and compare wrongly with every stored stamp.
    """
    if moment.tzinfo is None:
        raise ValueError("A stored moment must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def fetchable_host(address: str | None) -> str | None:
    """The host of an address a browser can be sent to.

    Args:
        address: An address from a discovery record or from storage, untrusted.

    Returns:
        Its host, lowercased, for an ``http(s)`` address that names one;
        ``None`` for anything else.
    """
    if not address:
        return None
    parsed = urlparse(address)
    if parsed.scheme.lower() not in _FETCHABLE_SCHEMES or not parsed.hostname:
        return None
    return parsed.hostname


def page_verdict(status: int | None, headers: Mapping[str, str], html: str) -> RescueVerdict:
    """What a visit concluded from the page it stayed on, with no download.

    A page with no status is the empty document a load that never answered
    leaves: the visit learnt nothing, so it is a transport failure (unreachable
    is not absent). A gone status (404, 410) is gone whatever the page says, as
    discovery rules. Any other page goes through the challenge rules a download's
    answer goes through (``bot_walls.challenge_markers``), on its first
    ``CHALLENGE_BODY_PREFIX_BYTES`` as UTF-8: a bounded read of a lookup
    page, not research content. A page with markers is a challenge. One
    without them, whose status says "not now" (408, 429, 5xx), learnt
    nothing, so it is a transport failure and the entry stays pending; one
    that refuses us (401, 403) waits for a person, as the HTTP client was
    already walled at that address. Only an answered page that serves no PDF
    is ``NO_PDF``.

    Args:
        status: The main frame's last HTTP status; ``None`` when none was seen.
        headers: That answer's headers.
        html: The page as the browser holds it, serialised.

    Returns:
        ``GONE``, ``CHALLENGE`` with the defences named (none for a bare
        refusal), ``TRANSPORT`` or ``NO_PDF``; each with the status.
    """
    if status is None:
        return RescueVerdict(RescueOutcome.TRANSPORT)
    if status in CHALLENGE_GONE_STATUSES:
        return RescueVerdict(RescueOutcome.GONE, status)
    body = html.encode("utf-8", errors="replace")[:CHALLENGE_BODY_PREFIX_BYTES]
    markers = challenge_markers(status, headers, body)
    if markers:
        return RescueVerdict(RescueOutcome.CHALLENGE, status, markers)
    if status in RESCUE_UNANSWERED_PAGE_STATUSES:
        return RescueVerdict(RescueOutcome.TRANSPORT, status)
    if status in RESCUE_REFUSED_PAGE_STATUSES:
        return RescueVerdict(RescueOutcome.CHALLENGE, status, ())
    return RescueVerdict(RescueOutcome.NO_PDF, status)


def penalise_host_for_answer(address: str, status: int | None, headers: Mapping[str, str]) -> bool:
    """Slow the address's host for a throttling answer, as our requests do.

    A main-frame 429 or 503 penalises the host's shared limiter with the
    answer's ``Retry-After``, so the rescue and the HTTP client back off
    together. Loopback is never paced.

    Args:
        address: The address visited.
        status: The main frame's HTTP status.
        headers: That answer's headers.

    Returns:
        Whether the limiter was penalised.
    """
    if status not in RESCUE_PENALISING_STATUSES:
        return False
    host = urlparse(address).hostname
    if not host or is_loopback_host(host):
        return False
    limiter_for(host).penalise(retry_after_from_headers(headers))
    return True


def download_verdict(first_bytes: bytes) -> RescueVerdict:
    """What a finished download concluded: the PDF, or something served as one.

    An empty file says nothing about the article, so it is a transport
    failure (unparsed is not absent). Anything else that is not a PDF goes
    through the challenge rules a page goes through, on its first
    ``CHALLENGE_BODY_PREFIX_BYTES``: a wall served as a download waits for a
    person. Only a body that is neither is ``NO_PDF``.

    Args:
        first_bytes: The start of the downloaded file; at least its first
            ``CHALLENGE_BODY_PREFIX_BYTES`` when it is that long.

    Returns:
        ``PDF`` when it begins with ``%PDF``; ``TRANSPORT`` when empty;
        ``CHALLENGE`` with the defences named; ``NO_PDF`` otherwise.
    """
    if first_bytes.startswith(PDF_MAGIC_BYTES):
        return RescueVerdict(RescueOutcome.PDF)
    if not first_bytes:
        return RescueVerdict(RescueOutcome.TRANSPORT)
    markers = challenge_markers(_NO_HTTP_STATUS, {}, first_bytes[:CHALLENGE_BODY_PREFIX_BYTES])
    if markers:
        return RescueVerdict(RescueOutcome.CHALLENGE, None, markers)
    return RescueVerdict(RescueOutcome.NO_PDF)


def title_checked_verdict(check: TitleCheck) -> RescueVerdict:
    """What a visit concluded from a PDF that arrived, once checked against the title (#532).

    A PDF that does not carry the title is not kept: its text would feed
    the article's rating. One the check could not judge is kept: with no
    title there is nothing to check against, and a page with no text (a
    scan) feeds no rating. A file that is no readable PDF learnt nothing.

    Args:
        check: The PDF checked against the entry's title.

    Returns:
        ``PDF``, ``WRONG_PDF``, or ``TRANSPORT`` for an unreadable file.
    """
    match check.outcome:
        case TitleCheckOutcome.MATCHES | TitleCheckOutcome.NO_TITLE | TitleCheckOutcome.NO_TEXT:
            return RescueVerdict(RescueOutcome.PDF)
        case TitleCheckOutcome.MISMATCH:
            return RescueVerdict(RescueOutcome.WRONG_PDF)
        case TitleCheckOutcome.UNREADABLE:
            return RescueVerdict(RescueOutcome.TRANSPORT)
        case unknown:
            assert_never(unknown)


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

    Raises:
        ValueError: If the address is no ``http(s)`` address, its host is
            not ``host``, or the document ID or DOI is blank: a browser is
            sent to the address, the pacing goes by the host, and the DOI
            names the saved PDF.
    """

    document_id: str
    doi: str
    pmid: str | None
    title: str
    address: str
    host: str
    service: str

    def __post_init__(self) -> None:
        """Hold the invariants a visit relies on, whatever the request was read from."""
        if not self.document_id.strip() or not self.doi.strip():
            raise ValueError("A walled PDF needs its document ID and DOI")
        if fetchable_host(self.address) != self.host:
            raise ValueError(f"A walled PDF's address must be http(s) at its host {self.host!r}")


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
        retry_after: When a ``PENDING`` entry held back after a transport
            failure is due again, as stored; ``None`` when due now, and for
            every other state.
    """

    id: int
    request: RescueRequest
    state: RescueState
    attempts: int
    verdict: RescueVerdict | None
    priority: int
    created_at: str
    updated_at: str
    retry_after: str | None = None


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
        host = fetchable_host(address)
        if host is None or address in seen:
            continue
        seen.add(address)
        requests.append(
            RescueRequest(
                document_id=subject.document_id,
                doi=subject.doi,
                pmid=subject.pmid,
                title=subject.title,
                address=address,
                host=host,
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
    reanalyse: Callable[[str, str | None, str | None], bool],
) -> bool:
    """Re-analyse the document a rescued PDF belongs to, when storage holds it.

    By the entry's DOI, which names the saved PDF, so the analysis's
    cached-PDF tier finds it. The MCP server queues under its own document
    ID: ``pmid-<n>``, storage's own scheme, so such an article is re-analysed
    when storage holds it; or ``doi-...``, which storage need not hold. A
    rescue of a document storage does not hold is not re-analysed, as that
    would store a transparency row for no document. Its PDF is saved all the
    same, for the next request.

    Args:
        entry: The rescued entry.
        stored_document: Reads a document from storage by ID.
        reanalyse: Re-analyses ``(document_id, pmid, doi)``.

    Returns:
        Whether a re-analysis was actually queued.
    """
    request = entry.request
    if stored_document(request.document_id) is None:
        logger.info(
            "Rescued the PDF of %s, which storage does not hold; nothing to re-analyse",
            request.document_id,
        )
        return False
    return bool(reanalyse(request.document_id, request.pmid, request.doi))


class WaitOutcome(Enum):
    """What a reader waiting on a document's walled PDFs is told."""

    #: A watched entry has not had its visit yet.
    WAITING = "waiting"
    #: A PDF arrived during the wait.
    RESCUED = "rescued"
    #: An entry is still open: the review queue can try it with the reader.
    NEEDS_READER = "needs_reader"
    #: Every entry settled without a PDF.
    NOTHING = "nothing"


#: The outcomes decided by one entry, which the result carries.
_OUTCOMES_WITH_ENTRY = frozenset({WaitOutcome.RESCUED, WaitOutcome.NEEDS_READER})


@dataclass(frozen=True)
class WaitResult:
    """Where a reader's wait stands, with the entry that decided it.

    Carrying the entry keeps the outcome and the entry acted on from
    disagreeing: a caller never looks for "the rescued entry" by rules of its
    own.

    Attributes:
        outcome: Where the wait stands.
        entry: The rescued entry (``RESCUED``), or the open one to show the
            reader (``NEEDS_READER``); ``None`` for the other outcomes.

    Raises:
        ValueError: If the entry is missing for an outcome that needs one,
            or given for one that takes none.
    """

    outcome: WaitOutcome
    entry: RescueEntry | None = None

    def __post_init__(self) -> None:
        """Hold the outcome and its entry together."""
        needs_entry = self.outcome in _OUTCOMES_WITH_ENTRY
        if needs_entry != (self.entry is not None):
            raise ValueError(
                f"A {self.outcome.value!r} wait result "
                f"{'needs' if needs_entry else 'takes no'} entry"
            )


def wait_watch(
    entries: Sequence[RescueEntry], hidden_pass_runs: bool, now: datetime
) -> frozenset[int]:
    """The entries a reader waits for: those the hidden pass will visit now.

    A pending entry held back after a transport failure is not due for
    minutes, so waiting on it would only run out the wait's limit.

    Args:
        entries: The document's entries.
        hidden_pass_runs: Whether a hidden pass will visit them.
        now: The moment.

    Returns:
        The IDs of the pending entries that are due; none when no hidden
        pass runs.
    """
    if not hidden_pass_runs:
        return frozenset()
    at = stamp(now)
    return frozenset(
        e.id
        for e in entries
        if e.state is RescueState.PENDING and (e.retry_after is None or e.retry_after <= at)
    )


def wait_outcome(
    entries: Sequence[RescueEntry],
    watched: AbstractSet[int],
    answered: AbstractSet[int],
    *,
    final: bool = False,
) -> WaitResult:
    """Where a reader's wait stands.

    Args:
        entries: The document's entries as they stand now; never empty, as
            a wait is only for entries this load queued.
        watched: The entries waited for (:func:`wait_watch`).
        answered: The entries that have had a verdict since the wait began.
        final: The wait is over (its limit): it is decided on what is known.
            A watched entry then counts as answered, so the outcome is never
            ``WAITING``, and one rescued counts as this wait's even when its
            verdict was not seen here (another process recorded it).

    Returns:
        ``RESCUED`` with the entry when an entry answered during the wait
        was rescued (one rescued earlier is not this wait's: its PDF is not
        on disk, or discovery would have found it); ``WAITING`` while a
        watched entry is pending and unanswered; ``NEEDS_READER`` with an
        open entry while any is open; ``NOTHING`` otherwise.

    Raises:
        ValueError: If ``entries`` is empty: no wait is about nothing, and
            "every entry settled without a PDF" would be false of it.
    """
    if not entries:
        raise ValueError("A wait needs the entries it is about")
    counted = set(answered) | set(watched) if final else answered
    rescued = next(
        (e for e in entries if e.state is RescueState.RESCUED and e.id in counted), None
    )
    if rescued is not None:
        return WaitResult(WaitOutcome.RESCUED, rescued)
    if any(
        e.state is RescueState.PENDING and e.id in watched and e.id not in counted
        for e in entries
    ):
        return WaitResult(WaitOutcome.WAITING)
    open_entry = next((e for e in entries if e.state in OPEN_RESCUE_STATES), None)
    if open_entry is not None:
        return WaitResult(WaitOutcome.NEEDS_READER, open_entry)
    return WaitResult(WaitOutcome.NOTHING)


class WalkEndReason(Enum):
    """Why *Walk the queue* ended."""

    #: Every listed entry was visited or skipped as no longer open.
    FINISHED = "finished"
    #: The reader stopped it, chose an entry, or acted on the walk's entry.
    STOPPED = "stopped"
    #: The queue could not be read (logged): unreadable is not settled.
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class WalkEnd:
    """How *Walk the queue* ended.

    Attributes:
        reason: Why it ended.
        failed: How many of its pages could not be opened (logged).
    """

    reason: WalkEndReason
    failed: int = 0


class RescueDismissals(Protocol):
    """Where a queue entry is dismissed (``LiteStorage``)."""

    def dismiss_rescue(self, entry_id: int) -> RecordedVerdict:
        """Dismiss an open entry.

        Args:
            entry_id: The entry.

        Returns:
            The entry as it now stands, and whether the dismissal applied.
        """
        ...


class DismissOutcome(Enum):
    """What asking to dismiss an entry did."""

    #: It was set aside.
    DISMISSED = "dismissed"
    #: It was no longer open: settled, dismissed or gone meanwhile.
    NOT_OPEN = "not_open"
    #: The queue could not be written (logged).
    FAILED = "failed"


@dataclass(frozen=True)
class DismissResult:
    """What asking to dismiss an entry did, and the entry as it now stands.

    Attributes:
        outcome: What it did.
        entry: The entry as it now stands; ``None`` when it is gone, or
            unknown because the write failed.
    """

    outcome: DismissOutcome
    entry: RescueEntry | None = None


def dismiss_entry(queue: RescueDismissals, entry_id: int) -> DismissResult:
    """Dismiss an entry for the reader, logging rather than raising a failure.

    Args:
        queue: The storage.
        entry_id: The entry.

    Returns:
        ``DISMISSED`` with the entry; ``NOT_OPEN`` with the entry as it
        stands (``None`` when gone) when it was settled or dismissed
        elsewhere meanwhile; ``FAILED`` when the queue could not be written
        (logged): three answers, so a reader is never told a write failed
        when it was not tried.
    """
    try:
        recorded = queue.dismiss_rescue(entry_id)
    except (sqlite3.Error, SQLiteError) as e:
        logger.error("Walled PDF %s could not be dismissed: %s", entry_id, e)
        return DismissResult(DismissOutcome.FAILED)
    if recorded.applied:
        return DismissResult(DismissOutcome.DISMISSED, recorded.entry)
    return DismissResult(DismissOutcome.NOT_OPEN, recorded.entry)
