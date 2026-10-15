# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What the reader reads about the browser rescue queue (#480, stage 3).

The review queue window's labels and sentences, and the interrogation tab's.
No Qt: pure text from the rules' types.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import assert_never

from .browser_rescue import (
    RescueEntry,
    RescueOutcome,
    RescueState,
    RescueVerdict,
    WalkEnd,
    WalkEndReason,
    stamp,
)
from .constants import SERVICE_OPENALEX_PDF, SERVICE_UNPAYWALL_PDF

logger = logging.getLogger(__name__)

#: Each state as the window's list shows it.
STATE_LABELS: dict[RescueState, str] = {
    RescueState.PENDING: "Waiting for the hidden browser",
    RescueState.NEEDS_PERSON: "Needs you",
    RescueState.RESCUED: "Fetched",
    RescueState.NO_PDF: "No PDF on the page",
    RescueState.GONE: "Gone",
    RescueState.TOO_LARGE: "Too large to download",
    RescueState.DISMISSED: "Dismissed",
    RescueState.SUPERSEDED: "Fetched from another address",
}
#: A pending entry's label when no hidden pass will visit it.
PENDING_WITHOUT_HIDDEN_PASS = "Waiting (the hidden browser is off)"

#: Who named an address, as the reader knows them.
SERVICE_LABELS: dict[str, str] = {
    SERVICE_UNPAYWALL_PDF: "Unpaywall",
    SERVICE_OPENALEX_PDF: "OpenAlex",
}

#: The window's help line, above the list.
REVIEW_QUEUE_HELP = (
    "These open-access PDFs sit behind a robot check the app's downloads could "
    "not pass. Select one to open its page in the built-in browser; if it asks, "
    "tick the box, then download the article's PDF. The PDF is saved when it "
    "arrives, and the article's transparency is re-checked."
)
#: Said while a selected entry's page opens.
SHOWN_VISIT_OPENING = "Opening the publisher's page…"
#: Said while a person's visit is under way.
SHOWN_VISIT_LOADING = (
    "If the page asks for a robot check, tick the box. The PDF is saved when it arrives."
)
#: Said while *Walk the queue* runs.
WALK_RUNNING = "Opening each page in turn and waiting up to a minute for its PDF…"
#: Said when the window cannot show pages.
REVIEW_QUEUE_NO_BROWSER = (
    "The built-in browser could not start on this computer, so pages cannot be "
    "opened here. Use “Open in system browser”; the log says why."
)
#: Said when the queue could not be read or written.
REVIEW_QUEUE_UNREADABLE = "The queue could not be read; the log says why."
REVIEW_QUEUE_UNWRITABLE = "The entry could not be dismissed; the log says why."
REVIEW_QUEUE_NOT_OPEN = "The entry was settled elsewhere meanwhile; it is not dismissed."
#: Said when a person's visit could not start.
SHOWN_VISIT_NOT_STARTED = "The page could not be opened here; the log says why."
#: Said when a person's download was not a PDF; the page stays open.
SHOWN_VISIT_NOT_A_PDF = (
    "That file was not a PDF, so it was not kept. The page stays open: "
    "download the article's own PDF."
)
#: Said when a person's PDF does not carry the article's title (#532); it is
#: held, and the page stays open.
SHOWN_VISIT_WRONG_PDF = (
    "That PDF's first page does not carry this article's title, so it was not "
    "kept: it may be a supplement or another article. The page stays open: "
    "download the article's own PDF. If this one is the article (a scan, or a "
    "preprint retitled since), choose “Keep this PDF”."
)
#: The button that keeps a held PDF as the article's.
KEEP_HELD_PDF_LABEL = "Keep this PDF"
#: Said when a visit's result could not be saved yet.
VERDICT_UNSAVED = (
    "The visit's result could not be saved yet; the app keeps trying, and "
    "opens no other page until it is saved. The log says why."
)
#: Said when the entry a visit was asked for was settled before its page opened.
SHOWN_VISIT_SETTLED_MEANWHILE = "Settled before its page opened"
#: Said when *Walk the queue* has nothing to visit.
WALK_NOTHING_WAITING = "No listed entry is waiting for you."
#: Said when an address cannot be opened in the system's browser.
SYSTEM_BROWSER_REFUSED = "This address is not a web address, so it is not opened."
SYSTEM_BROWSER_FAILED = "The system's browser could not be opened."

#: The interrogation tab's wait, and its offer.
BROWSER_RESCUE_WAITING = "Trying the publisher's page in a browser…"
REVIEW_QUEUE_OFFER_TITLE = "Robot check"
#: True whatever led here: a check the hidden browser met, a hidden browser
#: that is off, busy or slower than the wait, a page it could not reach, or
#: a PDF it fetched that was not the article's (#532).
REVIEW_QUEUE_OFFER = (
    "The publisher's copy of this article is behind a robot check and could "
    "not be fetched automatically, so what the app already had (usually the "
    "abstract) is loaded for now. In the "
    "review queue you can open the page, tick the box if it asks, and download "
    "the article's PDF; it is then saved, and opening the citation again loads it."
)
REVIEW_QUEUE_OFFER_OPEN = "Open the review queue"
REVIEW_QUEUE_OFFER_LATER = "Not now"

_RETRY_PREFIX = "Tried again after "
_CLOCK_FORMAT = "%H:%M"
_NOT_VISITED = "Not visited yet"
_VERDICT_UNREADABLE = "The last visit's result could not be read"
_WALK_FINISHED = "The walk is over."
_WALK_UNREADABLE = "The walk stopped: the queue could not be read; the log says why."


def state_label(state: RescueState, hidden_pass_runs: bool) -> str:
    """An entry's state as the list shows it.

    Args:
        state: The state.
        hidden_pass_runs: Whether a hidden pass will visit a pending entry.

    Returns:
        The label.
    """
    if state is RescueState.PENDING and not hidden_pass_runs:
        return PENDING_WITHOUT_HIDDEN_PASS
    return STATE_LABELS[state]


def service_label(service: str) -> str:
    """Who named the address, by name.

    Args:
        service: The stored service value.

    Returns:
        Its name; an unknown (newer build's) service as stored.
    """
    return SERVICE_LABELS.get(service, service)


def _with_status(text: str, verdict: RescueVerdict) -> str:
    """A sentence, with the verdict's HTTP status when one was seen.

    Args:
        text: The sentence.
        verdict: The verdict.

    Returns:
        The sentence, with "(HTTP n)" when a status was seen.
    """
    if verdict.http_status is None:
        return text
    return f"{text} (HTTP {verdict.http_status})"


def verdict_text(verdict: RescueVerdict | None) -> str:
    """A visit's verdict as a sentence.

    Args:
        verdict: The verdict; ``None`` before a visit.

    Returns:
        The sentence.
    """
    if verdict is None:
        return _NOT_VISITED
    match verdict.outcome:
        case RescueOutcome.PDF:
            return "The PDF arrived"
        case RescueOutcome.CHALLENGE:
            if verdict.markers:
                return f"A robot check ({', '.join(verdict.markers)})"
            return _with_status("Refused", verdict)
        case RescueOutcome.NO_PDF:
            return _with_status("The page served no PDF", verdict)
        case RescueOutcome.GONE:
            return _with_status("Not found", verdict)
        case RescueOutcome.TOO_LARGE:
            return "The PDF is larger than the app downloads"
        case RescueOutcome.WRONG_PDF:
            return "A PDF arrived whose first page does not carry this article's title; it was not kept"
        case RescueOutcome.TRANSPORT:
            return _with_status("Nothing was learnt: the page or its download failed", verdict)
        case unknown:
            assert_never(unknown)


def last_visit_text(entry: RescueEntry) -> str:
    """An entry's last visit as a sentence, telling an unreadable one from none.

    Args:
        entry: The entry.

    Returns:
        The verdict's sentence; "Not visited yet" when no visit was counted
        and none is stored; a sentence saying it could not be read when a
        visit was made but its verdict is unreadable (logged by the store).
    """
    if entry.verdict is None and entry.attempts > 0:
        return _VERDICT_UNREADABLE
    return verdict_text(entry.verdict)


def retry_text(entry: RescueEntry, now: datetime) -> str:
    """When a held-back pending entry is tried again, in the reader's local time.

    Args:
        entry: The entry.
        now: The moment (timezone-aware).

    Returns:
        "Tried again after HH:MM"; empty when it is not pending, not held
        back, already due, or its stored time cannot be read (logged).
    """
    if entry.state is not RescueState.PENDING or entry.retry_after is None:
        return ""
    if entry.retry_after <= stamp(now):
        return ""
    try:
        moment = datetime.fromisoformat(entry.retry_after)
    except ValueError:
        logger.warning("Rescue entry %s's retry time %r could not be read", entry.id, entry.retry_after)
        return ""
    return _RETRY_PREFIX + moment.astimezone().strftime(_CLOCK_FORMAT)


def walk_end_text(end: WalkEnd) -> str:
    """How *Walk the queue* ended, as a sentence.

    Args:
        end: How it ended.

    Returns:
        The sentence; empty when the reader stopped it and no page failed,
        which leaves what the window says about the reader's own act.
    """
    failed = (
        f" {end.failed} page{'s' if end.failed != 1 else ''} could not be opened here; the log says why."
        if end.failed
        else ""
    )
    match end.reason:
        case WalkEndReason.FINISHED:
            return _WALK_FINISHED + failed
        case WalkEndReason.UNREADABLE:
            return _WALK_UNREADABLE + failed
        case WalkEndReason.STOPPED:
            return failed.strip()
        case unknown:
            assert_never(unknown)
