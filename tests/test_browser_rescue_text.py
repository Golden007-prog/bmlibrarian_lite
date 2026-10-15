"""What the reader reads about the browser rescue queue (#480, stage 3)."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from bmlibrarian_lite.browser_rescue import (
    RescueEntry,
    RescueOutcome,
    RescueRequest,
    RescueState,
    RescueVerdict,
    WalkEnd,
    WalkEndReason,
)
from bmlibrarian_lite.browser_rescue_text import (
    STATE_LABELS,
    last_visit_text,
    retry_text,
    service_label,
    state_label,
    verdict_text,
    walk_end_text,
)
from bmlibrarian_lite.constants import SERVICE_OPENALEX_PDF, SERVICE_UNPAYWALL_PDF


def test_every_state_has_a_label() -> None:
    """A state with no label would show its stored value."""
    assert set(STATE_LABELS) == set(RescueState)


def test_a_pending_entry_says_whether_anything_will_visit_it() -> None:
    """With the hidden pass off, pending means waiting for the reader."""
    assert state_label(RescueState.PENDING, True) == "Waiting for the hidden browser"
    assert state_label(RescueState.PENDING, False) == "Waiting (the hidden browser is off)"
    assert state_label(RescueState.NEEDS_PERSON, False) == "Needs you"


def test_the_services_are_named_as_the_reader_knows_them() -> None:
    """Who named the address, by name; an unknown service by its own value."""
    assert service_label(SERVICE_UNPAYWALL_PDF) == "Unpaywall"
    assert service_label(SERVICE_OPENALEX_PDF) == "OpenAlex"
    assert service_label("newer_service") == "newer_service"


@pytest.mark.parametrize(
    "verdict, text",
    [
        (None, "Not visited yet"),
        (RescueVerdict(RescueOutcome.PDF), "The PDF arrived"),
        (RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare",)), "A robot check (cloudflare)"),
        (RescueVerdict(RescueOutcome.CHALLENGE, 403, ()), "Refused (HTTP 403)"),
        (RescueVerdict(RescueOutcome.CHALLENGE, None, ("captcha",)), "A robot check (captcha)"),
        (RescueVerdict(RescueOutcome.NO_PDF, 200), "The page served no PDF (HTTP 200)"),
        (RescueVerdict(RescueOutcome.NO_PDF), "The page served no PDF"),
        (RescueVerdict(RescueOutcome.GONE, 404), "Not found (HTTP 404)"),
        (RescueVerdict(RescueOutcome.TOO_LARGE), "The PDF is larger than the app downloads"),
        (
            RescueVerdict(RescueOutcome.WRONG_PDF),
            "A PDF arrived whose first page does not carry this article's title; it was not kept",
        ),
        (RescueVerdict(RescueOutcome.TRANSPORT, 503), "Nothing was learnt: the page or its download failed (HTTP 503)"),
        (RescueVerdict(RescueOutcome.TRANSPORT), "Nothing was learnt: the page or its download failed"),
    ],
)
def test_each_verdict_reads_as_a_sentence(verdict: RescueVerdict | None, text: str) -> None:
    """Each outcome, with its status when one was seen."""
    assert verdict_text(verdict) == text


def _pending(retry_after: str | None) -> RescueEntry:
    request = RescueRequest("d", "10.1/x", None, "T", "https://a.example/x.pdf", "a.example",
                            SERVICE_UNPAYWALL_PDF)
    return RescueEntry(1, request, RescueState.PENDING, 1, None, 0, "t", "t", retry_after)


NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def test_a_held_back_entry_says_when_it_is_tried_again() -> None:
    """In the reader's local time, to the minute."""
    text = retry_text(_pending("2026-10-15T09:30:00.000000+00:00"), NOW)
    assert text.startswith("Tried again after ")
    assert len(text) == len("Tried again after HH:MM")


@pytest.mark.parametrize("retry_after", [None, "not a time"])
def test_no_retry_time_says_nothing(retry_after: str | None) -> None:
    """None, or a stored value this build cannot read, says nothing rather than guessing."""
    assert retry_text(_pending(retry_after), NOW) == ""


def test_a_retry_time_already_past_says_nothing() -> None:
    """Due now: "tried again after 08:30" would read as a retry already made."""
    assert retry_text(_pending("2026-10-15T08:30:00.000000+00:00"), NOW) == ""


def test_only_a_pending_entry_has_a_retry_time() -> None:
    """A retry time left on a settled entry (stage 2's supersession) is never shown."""
    entry = replace(_pending("2026-10-15T09:30:00.000000+00:00"), state=RescueState.SUPERSEDED)
    assert retry_text(entry, NOW) == ""


def test_an_unreadable_last_visit_is_not_called_no_visit() -> None:
    """A visit was counted but its verdict cannot be read: say so, never "Not visited yet"."""
    assert last_visit_text(_pending(None)) == "The last visit's result could not be read"
    assert last_visit_text(replace(_pending(None), attempts=0)) == "Not visited yet"
    pdf = replace(_pending(None), verdict=RescueVerdict(RescueOutcome.PDF))
    assert last_visit_text(pdf) == "The PDF arrived"


def test_a_walk_says_how_it_ended() -> None:
    """Finished or unreadable, always said; stopped by the reader, only its failures."""
    assert walk_end_text(WalkEnd(WalkEndReason.FINISHED)) == "The walk is over."
    assert "could not be read" in walk_end_text(WalkEnd(WalkEndReason.UNREADABLE))
    assert walk_end_text(WalkEnd(WalkEndReason.STOPPED)) == ""
    assert walk_end_text(WalkEnd(WalkEndReason.FINISHED, 2)).endswith(
        "2 pages could not be opened here; the log says why."
    )
    assert walk_end_text(WalkEnd(WalkEndReason.STOPPED, 1)) == (
        "1 page could not be opened here; the log says why."
    )


def test_the_offer_claims_no_attempt_that_may_not_have_been_made() -> None:
    """The offer also follows a hidden pass that was off, too slow, or unreachable: it never says it tried."""
    from bmlibrarian_lite.browser_rescue_text import REVIEW_QUEUE_OFFER

    assert "could not pass" not in REVIEW_QUEUE_OFFER
    assert "hidden browser" not in REVIEW_QUEUE_OFFER
    assert "robot check" in REVIEW_QUEUE_OFFER
    # After a PDF that was not the article's (#532) there may be no box to tick
    assert "tick the box if it asks" in REVIEW_QUEUE_OFFER
    assert "download the article's PDF" in REVIEW_QUEUE_OFFER
