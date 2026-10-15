# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What the reader reads about the browser rescue queue (#480, stage 3).

The review queue window's labels and sentences, and the interrogation tab's.
No Qt: pure text from the rules' types.
"""

from __future__ import annotations

from datetime import datetime

from .browser_rescue import RescueEntry, RescueOutcome, RescueState, RescueVerdict
from .constants import SERVICE_OPENALEX_PDF, SERVICE_UNPAYWALL_PDF

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
    "not pass. Select one to open its page here; if it asks, tick the box. The "
    "PDF is saved when it arrives, and the article's transparency is re-checked."
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
#: Said when a person's visit could not start.
SHOWN_VISIT_NOT_STARTED = "The page could not be opened here; the log says why."

#: The interrogation tab's wait, and its offer.
BROWSER_RESCUE_WAITING = "Trying the publisher's page in a browser…"
REVIEW_QUEUE_OFFER_TITLE = "Robot check"
REVIEW_QUEUE_OFFER = (
    "The publisher's copy of this article is behind a robot check that the "
    "hidden browser could not pass, so the abstract is loaded for now. In the "
    "review queue you can open the page and tick the box; the PDF is then "
    "saved, and opening the citation again loads it."
)
REVIEW_QUEUE_OFFER_OPEN = "Open the review queue"
REVIEW_QUEUE_OFFER_LATER = "Not now"

_RETRY_PREFIX = "Tried again after "
_CLOCK_FORMAT = "%H:%M"


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
    """The last visit's verdict as a sentence.

    Args:
        verdict: The verdict; ``None`` before a visit or when unreadable.

    Returns:
        The sentence.
    """
    if verdict is None:
        return "Not visited yet"
    outcome = verdict.outcome
    if outcome is RescueOutcome.PDF:
        return "The PDF arrived"
    if outcome is RescueOutcome.CHALLENGE:
        if verdict.markers:
            return f"A robot check ({', '.join(verdict.markers)})"
        return _with_status("Refused", verdict)
    if outcome is RescueOutcome.NO_PDF:
        return _with_status("The page served no PDF", verdict)
    if outcome is RescueOutcome.GONE:
        return _with_status("Not found", verdict)
    if outcome is RescueOutcome.TOO_LARGE:
        return "The PDF is larger than the app downloads"
    return _with_status("The page could not be reached", verdict)


def retry_text(entry: RescueEntry) -> str:
    """When a held-back entry is tried again, in the reader's local time.

    Args:
        entry: The entry.

    Returns:
        "Tried again after HH:MM"; empty when it is not held back, or its
        stored time cannot be read.
    """
    if entry.retry_after is None:
        return ""
    try:
        moment = datetime.fromisoformat(entry.retry_after)
    except ValueError:
        return ""
    return _RETRY_PREFIX + moment.astimezone().strftime(_CLOCK_FORMAT)
