"""What the reader reads about the browser rescue queue (#480, stage 3)."""

import pytest

from bmlibrarian_lite.browser_rescue import (
    RescueEntry,
    RescueOutcome,
    RescueRequest,
    RescueState,
    RescueVerdict,
)
from bmlibrarian_lite.browser_rescue_text import (
    STATE_LABELS,
    retry_text,
    service_label,
    state_label,
    verdict_text,
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
        (RescueVerdict(RescueOutcome.TRANSPORT, 503), "The page could not be reached (HTTP 503)"),
        (RescueVerdict(RescueOutcome.TRANSPORT), "The page could not be reached"),
    ],
)
def test_each_verdict_reads_as_a_sentence(verdict: RescueVerdict | None, text: str) -> None:
    """Each outcome, with its status when one was seen."""
    assert verdict_text(verdict) == text


def _pending(retry_after: str | None) -> RescueEntry:
    request = RescueRequest("d", "10.1/x", None, "T", "https://a.example/x.pdf", "a.example",
                            SERVICE_UNPAYWALL_PDF)
    return RescueEntry(1, request, RescueState.PENDING, 1, None, 0, "t", "t", retry_after)


def test_a_held_back_entry_says_when_it_is_tried_again() -> None:
    """In the reader's local time, to the minute."""
    text = retry_text(_pending("2026-10-15T09:30:00.000000+00:00"))
    assert text.startswith("Tried again after ")
    assert len(text) == len("Tried again after HH:MM")


@pytest.mark.parametrize("retry_after", [None, "not a time"])
def test_no_retry_time_says_nothing(retry_after: str | None) -> None:
    """None, or a stored value this build cannot read, says nothing rather than guessing."""
    assert retry_text(_pending(retry_after)) == ""
