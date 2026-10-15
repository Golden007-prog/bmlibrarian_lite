# Browser Rescue, Stage 2: the Queue and the Hidden Pass — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the desktop, queue every open-access PDF a bot wall kept from our client (`challenged`, stage 1). Fetch it later in a hidden QtWebEngine page on the GUI thread, save it where the next discovery finds it, and re-analyse that document's transparency. Retire the Playwright fallback (#483).

**Architecture:** Pure rules in `browser_rescue.py` (what is queued, what a visit concluded, how an entry moves). SQL in `rescue_queue_store.py`, behind thin `LiteStorage` methods. One visit is a `RescueAttempt` (`gui/rescue_attempt.py`); the queue driver is a `BrowserRescueService` (`gui/browser_rescue_service.py`). Both are `QObject`s on the GUI thread, driven only by signals and timers. Analyses enqueue through `transparency/assessment.py` (shared by both analysis paths) and the MCP server. A rescue emits `rescued(entry)`, and the main window asks the review tab's `TransparencyManager` to re-analyse.

**Tech Stack:** Python 3.12, PySide6 6.10 (QtWebEngineCore, already in the wheel), SQLite (UPSERT), pytest; engine tests in a subprocess against a loopback `http.server`.

**Spec:** `docs/superpowers/specs/2026-10-12-browser-rescue-design.md` (sections 2, 3, 5, Testing, Stages: stage 2). Stage 1 (the `challenged` kind) landed in PR #521.

## Global Constraints

- Golden rules: `doc/llm/golden_rules.md`. Type hints and Google docstrings on everything; no magic numbers (constants in `constants.py`); errors handled, logged, reported; tests for everything; no inline stylesheets; `scaled()` for pixels.
- No impersonation (spec decision 5): QtWebEngine's own User-Agent, no automation protocol, no stealth flags.
- One recovery path (spec decision 4): the browser lives on the GUI thread and is driven only by the queue. No worker thread ever waits on it.
- A run never waits for the browser (spec decision 1).
- `CHALLENGE_BODY_PREFIX_BYTES` (64 KiB) bounds every page read. That is a lookup page, not research content, so golden rule 13 does not apply (as in stage 1).
- Engine tests run **in their own subprocess**, never in the suite's shared `QApplication` (#516, #517). They **fail rather than skip** when QtWebEngine cannot start.
- Never record an absolute ruff/mypy count. The gate is `python .github/scripts/lint_delta.py --base-ref origin/master` (no new findings).
- No closing keyword before a deferred issue number (write "Deferred: #N"). Only #483 is closed by this stage's PR. #480 stays open for stage 3.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Decisions for the maintainer's review

The spec leaves these open or implies them; the plan takes the position shown. Each can be reversed before implementation.

- **D1. A challenged copy is no longer a paywall.** Stage 1 kept a challenged 401/403 (and a challenged `text/html` 200 at an address containing "access", "pay" …) on the paywall branch: `is_paywall=True`, "A source refused access…", and the interrogation tab's OpenAthens offer. Now that the queue takes those copies, `_try_download` returns the challenge before the paywall sniff. The reader gets the tried-sources sentence ("… named by Unpaywall (the host asked for a robot check). A freely available copy may exist. …"). A plain 401/403 with no challenge stays a paywall. This settles the HANDOVER item "decide this when the queue takes those copies".
- **D2. A seventh state, `too_large`.** The spec's verdicts are rescued, needs_person, no_pdf and gone. A download cancelled past `MAX_PDF_SIZE` is none of these: the copy exists and is larger than we download, as the discovery's `OVER_SIZE_LIMIT` skip says. It settles as `too_large`, and stage 3's window will show it.
- **D3. A retry delay for transport failures.** A visit that fails in transit stays `pending` with `attempts + 1` (spec). Without a delay the next tick would take the same entry at once, so one network blip would spend all `MAX_RESCUE_ATTEMPTS` in seconds. An extra column, `retry_after`, holds the entry back for `RESCUE_RETRY_DELAY_SECONDS` (15 min).
- **D4. The browser's verdict reads the serialised DOM (`toHtml`) plus the main frame's status and headers**, not "title and text". The markers include script names (`_cf_chl_opt`) that `toPlainText` drops, and Cloudflare's rule needs the `Server` header. Probe (QtWebEngine 6.10.1): `QWebEngineLoadingInfo.responseHeaders()` is a `{QByteArray: [QByteArray]}` multimap, and every main-frame answer reports its status through `HttpStatusCodeDomain`, a 200 included.
- **D5. A 404/410 ends a visit at once** instead of waiting out the 25 s, because it is gone whatever happens next.
- **D6. The interrogation tab enqueues in stage 3, with its priority path.** It would otherwise be changed twice. Stage 2 enqueues from the transparency analysis (both callers of `assess_document`: the review's manager and the Research Questions re-analysis, #373) and from the MCP server.
- **D7. Waking the service.** In-process enqueues run on executor threads. Instead of a new cross-thread signal from each site, the service wakes on the two tabs' existing `transparency_outcome_ready` signals (emitted on the GUI thread after an analysis), plus the spec's slow poll.
- **D8. Stage 2 says a rescue happened in a transient status-bar message.** The persistent "Walled PDFs: N" indicator is stage 3's.

## Review Focus

The five uncovered inputs most likely to bite a person, each pinned by a test in the task named:

1. **The laptop is offline for a while.** Every visit fails in transit. Each entry waits `RESCUE_RETRY_DELAY_SECONDS` between tries, becomes `needs_person` only after `MAX_RESCUE_ATTEMPTS`, and is never retried for ever. (Task 2: `test_a_transport_failure_waits_before_the_next_try`, `test_the_attempt_cap_hands_the_entry_to_a_person`.)
2. **The reader quits during a visit.** The entry stays `pending` with its attempts unchanged, the page is deleted before the profile, and QtWebEngine prints no "Release of profile requested but WebEnginePage still not deleted". (Task 9: `test_a_quit_mid_visit_leaves_the_entry_pending`, `test_quit_releases_the_page_before_the_profile`.)
3. **The MCP server rescues a document storage does not hold** (its id is `doi-…`). The PDF is saved, nothing is re-analysed, and no transparency row is written for a phantom document. (Task 10: `test_a_rescue_of_a_document_storage_does_not_hold_is_not_reanalysed`.)
4. **A later run analyses the same document again** while its entry is open or settled. No duplicate row appears, and a settled entry is not reopened. (Task 2: `test_enqueue_is_idempotent_and_never_reopens_a_settled_entry`.)
5. **A challenged copy's address is not a web address** (`ftp:`, relative, blank host). It is not queued. (Task 1: `test_rescue_requests_skip_addresses_a_browser_cannot_visit`.)

---

## File structure

| File | Responsibility |
|---|---|
| `src/bmlibrarian_lite/browser_rescue.py` (new) | Pure rules: states, outcomes, verdicts, `next_state`, subject/request/entry types, `rescue_requests`, `page_verdict`, `download_verdict`, `rescue_pdf_path`, `rescue_delay_ms`, `queue_challenged_copies`, `reanalyse_rescued` |
| `src/bmlibrarian_lite/rescue_queue_store.py` (new) | The `browser_rescue_queue` table's SQL |
| `src/bmlibrarian_lite/storage.py` | Creates the table; `enqueue_rescues`, `next_rescue_entry`, `record_rescue_verdict`, `rescue_entries` |
| `src/bmlibrarian_lite/rate_limit.py` | `RateLimiter.claim()`: a slot without sleeping |
| `src/bmlibrarian_lite/constants.py` | `RESCUE_*`, `MAX_RESCUE_ATTEMPTS`, `BROWSER_PROFILE_*`, `MS_PER_SECOND` |
| `src/bmlibrarian_lite/pdf_discovery.py` | D1; the Playwright fallback removed |
| `src/bmlibrarian_lite/fulltext_discovery.py`, `study_transparency_analyzer/…`, `transparency/assessment.py`, `mcp_server.py`, `__init__.py` | Fallback parameters removed; `TransparencyReport.fulltext_lookups`; the enqueue sites |
| `src/bmlibrarian_lite/config.py` | `DiscoveryConfig.browser_rescue_hidden`; `StorageConfig.browser_profile_dir` |
| `src/bmlibrarian_lite/gui/web_engine.py` (new) | `prepare_web_engine()`: QtWebEngine's before-`QApplication` requirement, in one place |
| `src/bmlibrarian_lite/gui/rescue_attempt.py` (new) | `rescue_profile()`, `response_headers()`, `RescueAttempt` (one hidden visit) |
| `src/bmlibrarian_lite/gui/browser_rescue_service.py` (new) | `BrowserRescueService` (works the queue) |
| `src/bmlibrarian_lite/gui/settings_dialog.py` | The "Try bot-walled PDFs in a hidden browser" checkbox |
| `src/bmlibrarian_lite/gui/app.py`, `gui/systematic_review_tab.py`, `transparency/transparency_manager.py` | Start-up, wiring, `reanalyse` |
| `tests/conftest.py`, `.github/scripts/qt_preflight.py` (new), `.github/workflows/python-tests.yml` | QtWebEngine before any `QApplication`; CI libraries and preflight |
| `tests/browser_rescue_harness.py` (new, not collected) | Engine and service scenarios against a loopback server, in its own process |
| `scripts/browser_rescue_acceptance.py` (new) | Live acceptance over the spike's 41 addresses |
| Docs | `doc/developer/browser_rescue.md` (new), `doc/user/guide.md`, `doc/developer/guide.md`, `doc/cross_platform/polite_request_pacing.md`, `doc/cross_platform/fulltext_retrieval.md`, the analyser's README/DOCUMENTATION, `CLAUDE.md`, spikes README, `HANDOVER.md` |

---

### Task 1: The queue's pure rules

**Files:**
- Modify: `src/bmlibrarian_lite/constants.py` (after `PDF_PARTIAL_SUFFIX`, ~line 1270)
- Create: `src/bmlibrarian_lite/browser_rescue.py`
- Test: `tests/test_browser_rescue.py`

**Interfaces:**
- Consumes: `bot_walls.challenge_markers(status, headers, body_prefix)`, `LookupRecord`, `SourceLookupFailure`, `RequestFailureKind.CHALLENGED`, `pdf_utils.generate_pdf_path(doc_dict, base_dir)`.
- Produces (later tasks rely on these exact names):
  - `RescueState` (enum: `PENDING`, `NEEDS_PERSON`, `RESCUED`, `NO_PDF`, `GONE`, `TOO_LARGE`, `DISMISSED`, `SUPERSEDED`), `OPEN_RESCUE_STATES`
  - `RescueOutcome` (enum: `PDF`, `CHALLENGE`, `NO_PDF`, `GONE`, `TOO_LARGE`, `TRANSPORT`)
  - `RescueVerdict(outcome, http_status=None, markers=())`, `.to_dict()`, `RescueVerdict.from_dict(data) -> RescueVerdict | None`
  - `next_state(state, verdict, attempts) -> RescueState`
  - `RescueSubject(document_id, doi, pmid=None, title="")`
  - `RescueRequest(document_id, doi, pmid, title, address, host, service)`
  - `RescueEntry(id, request, state, attempts, verdict, priority, created_at, updated_at)`
  - `rescue_requests(subject, record) -> tuple[RescueRequest, ...]`
  - `page_verdict(status, headers, html) -> RescueVerdict`, `download_verdict(first_bytes) -> RescueVerdict`
  - `rescue_pdf_path(doi, base_dir=None) -> Path`
  - `RescueQueue` (Protocol with `enqueue_rescues`), `queue_challenged_copies(queue, subject, record, priority=RESCUE_PRIORITY_DEFAULT) -> int`

- [ ] **Step 1: Add the constants**

In `src/bmlibrarian_lite/constants.py`, after `PDF_PARTIAL_SUFFIX = ".part"`:

```python
# ---------------------------------------------------------------------------
# Browser rescue (#480, stage 2): an open-access PDF a bot wall kept from our
# client, fetched later by a real embedded browser driven from a queue --
# never inline in a run.
# ---------------------------------------------------------------------------

#: How long a hidden visit waits for a download before it reads the page:
#: the spike's 25 s, which passed PMC's wall 12 times in 12.
RESCUE_HIDDEN_TIMEOUT_MS = 25_000
#: A started download that receives nothing for this long is cancelled: the
#: survey's IEEE download stalled for ever.
RESCUE_DOWNLOAD_STALL_MS = 20_000
#: How long a visit waits for the page it stayed on to hand over its HTML.
RESCUE_PAGE_READ_GRACE_MS = 5_000
#: Visits that failed in transit before an entry waits for a person, so no
#: entry is retried for ever.
MAX_RESCUE_ATTEMPTS = 3
#: A visit that failed in transit is not retried sooner than this, so one
#: network blip does not spend every attempt at once.
RESCUE_RETRY_DELAY_SECONDS = 15 * 60
#: How often the queue is read for entries another process wrote (the MCP
#: server, the CLI) or that arrived while the hidden pass was off.
RESCUE_POLL_INTERVAL_MS = 60_000
#: The priority of an entry an analysis queued.
RESCUE_PRIORITY_DEFAULT = 0
#: The priority of an entry a reader is waiting on (stage 3's interrogation
#: tab): visited before every default one.
RESCUE_PRIORITY_WAITING = 1
#: The browser's persistent profile, under the data directory: its cookies
#: are kept, so a clearance or a person's tick carries over between pages
#: and sessions.
BROWSER_PROFILE_DIRNAME = "browser_profile"
#: The profile's name; QtWebEngine keys its on-disk store by it.
BROWSER_PROFILE_STORAGE_NAME = "browser-rescue"
#: The profile's HTTP cache, inside the profile directory.
BROWSER_PROFILE_CACHE_DIRNAME = "cache"
#: Milliseconds in a second.
MS_PER_SECOND = 1000
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_browser_rescue.py`:

```python
"""The browser rescue queue's pure rules (#480, stage 2)."""

import sqlite3
from pathlib import Path

import pytest

from bmlibrarian_lite.browser_rescue import (
    OPEN_RESCUE_STATES,
    RescueEntry,
    RescueOutcome,
    RescueRequest,
    RescueState,
    RescueSubject,
    RescueVerdict,
    download_verdict,
    next_state,
    page_verdict,
    queue_challenged_copies,
    rescue_pdf_path,
    rescue_requests,
)
from bmlibrarian_lite.constants import (
    MAX_RESCUE_ATTEMPTS,
    SERVICE_OPENALEX_PDF,
    SERVICE_PDF_DOWNLOAD,
    SERVICE_UNPAYWALL_PDF,
)
from bmlibrarian_lite.data_models import (
    LookupRecord,
    RequestFailure,
    RequestFailureKind,
    SourceLookupFailure,
)
from bmlibrarian_lite.exceptions import SQLiteError

DOI = "10.1000/xyz"
ADDRESS = "https://www.sciencedirect.com/science/article/pii/S1/pdfft"
CHALLENGED = RequestFailure(RequestFailureKind.CHALLENGED, 403)
SUBJECT = RescueSubject("doc-1", DOI, "123", "A title")


def _record(*failures: SourceLookupFailure) -> LookupRecord:
    return LookupRecord(failures=failures)


def _challenged(address: str | None = ADDRESS, service: str = SERVICE_UNPAYWALL_PDF) -> SourceLookupFailure:
    return SourceLookupFailure(service, CHALLENGED, address)


# -- what is queued ----------------------------------------------------------


def test_a_challenged_open_access_copy_is_queued_with_its_host() -> None:
    assert rescue_requests(SUBJECT, _record(_challenged())) == (
        RescueRequest("doc-1", DOI, "123", "A title", ADDRESS, "www.sciencedirect.com", SERVICE_UNPAYWALL_PDF),
    )


def test_openalex_copies_are_queued_too() -> None:
    (request,) = rescue_requests(SUBJECT, _record(_challenged(service=SERVICE_OPENALEX_PDF)))
    assert request.service == SERVICE_OPENALEX_PDF


@pytest.mark.parametrize(
    "failure",
    [
        # An ordinary refusal is the host's answer, not a wall
        SourceLookupFailure(SERVICE_UNPAYWALL_PDF, RequestFailure(RequestFailureKind.HTTP_STATUS, 403), ADDRESS),
        # Not an open-access copy's PDF
        SourceLookupFailure(SERVICE_PDF_DOWNLOAD, CHALLENGED, ADDRESS),
        # No address to visit
        SourceLookupFailure(SERVICE_UNPAYWALL_PDF, CHALLENGED, None),
    ],
)
def test_only_a_challenged_open_access_copy_with_an_address_is_queued(failure: SourceLookupFailure) -> None:
    assert rescue_requests(SUBJECT, _record(failure)) == ()


@pytest.mark.parametrize(
    "address", ["ftp://repo.example.org/a.pdf", "/relative/a.pdf", "https:///a.pdf", "javascript:alert(1)"]
)
def test_rescue_requests_skip_addresses_a_browser_cannot_visit(address: str) -> None:
    assert rescue_requests(SUBJECT, _record(_challenged(address))) == ()


def test_each_address_is_queued_once_in_record_order() -> None:
    other = "https://pubs.rsc.org/a.pdf"
    requests = rescue_requests(SUBJECT, _record(_challenged(), _challenged(other), _challenged()))
    assert [r.address for r in requests] == [ADDRESS, other]


def test_a_document_without_a_doi_queues_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """The DOI names the saved file; without one nothing could find it."""
    assert rescue_requests(RescueSubject("doc-1", None), _record(_challenged())) == ()
    assert "no DOI" in caplog.text


def test_a_subject_names_its_document() -> None:
    with pytest.raises(ValueError):
        RescueSubject("  ", DOI)


def test_a_subject_strips_what_it_is_given() -> None:
    assert RescueSubject(" doc-1 ", "  ", " 1 ", None) == RescueSubject("doc-1", None, "1", "")  # type: ignore[arg-type]


# -- what a visit concluded --------------------------------------------------


def test_a_page_still_on_its_challenge_needs_a_person() -> None:
    html = "<html><head><title>Just a moment...</title></head><script>window._cf_chl_opt={}</script></html>"
    assert page_verdict(403, {"Server": "cloudflare"}, html) == RescueVerdict(
        RescueOutcome.CHALLENGE, 403, ("cloudflare",)
    )


def test_cloudflares_refusal_is_a_challenge_whatever_its_page_says() -> None:
    verdict = page_verdict(403, {"Server": "cloudflare"}, "<html><style></style></html>")
    assert verdict.outcome is RescueOutcome.CHALLENGE


def test_an_unchallenged_page_has_no_pdf() -> None:
    assert page_verdict(200, {}, "<html><title>Abstract</title></html>") == RescueVerdict(RescueOutcome.NO_PDF, 200)


@pytest.mark.parametrize("status", [404, 410])
def test_a_gone_status_is_gone_whatever_the_page_says(status: int) -> None:
    assert page_verdict(status, {}, "Just a moment...") == RescueVerdict(RescueOutcome.GONE, status)


def test_a_page_with_no_status_is_read_for_markers_alone() -> None:
    assert page_verdict(None, {}, "Verify you are human").outcome is RescueOutcome.CHALLENGE
    assert page_verdict(None, {}, "<p>nothing</p>") == RescueVerdict(RescueOutcome.NO_PDF, None)


def test_only_the_first_64_kib_of_a_page_are_read() -> None:
    html = "x" * (64 * 1024) + "Just a moment..."
    assert page_verdict(200, {}, html).outcome is RescueOutcome.NO_PDF


def test_a_download_is_the_pdf_only_when_it_begins_with_the_magic_bytes() -> None:
    assert download_verdict(b"%PDF-1.7") == RescueVerdict(RescueOutcome.PDF)
    assert download_verdict(b"<htm") == RescueVerdict(RescueOutcome.NO_PDF)
    assert download_verdict(b"") == RescueVerdict(RescueOutcome.NO_PDF)


# -- how an entry moves -------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (RescueOutcome.PDF, RescueState.RESCUED),
        (RescueOutcome.CHALLENGE, RescueState.NEEDS_PERSON),
        (RescueOutcome.NO_PDF, RescueState.NO_PDF),
        (RescueOutcome.GONE, RescueState.GONE),
        (RescueOutcome.TOO_LARGE, RescueState.TOO_LARGE),
    ],
)
@pytest.mark.parametrize("state", sorted(OPEN_RESCUE_STATES, key=lambda s: s.value))
def test_an_answer_settles_an_open_entry(state: RescueState, outcome: RescueOutcome, expected: RescueState) -> None:
    assert next_state(state, RescueVerdict(outcome), 1) is expected


def test_a_transport_failure_keeps_a_pending_entry_pending_until_the_cap() -> None:
    transport = RescueVerdict(RescueOutcome.TRANSPORT)
    assert next_state(RescueState.PENDING, transport, MAX_RESCUE_ATTEMPTS - 1) is RescueState.PENDING
    assert next_state(RescueState.PENDING, transport, MAX_RESCUE_ATTEMPTS) is RescueState.NEEDS_PERSON


def test_a_transport_failure_leaves_an_entry_waiting_for_a_person_where_it_is() -> None:
    assert next_state(RescueState.NEEDS_PERSON, RescueVerdict(RescueOutcome.TRANSPORT), 1) is RescueState.NEEDS_PERSON


@pytest.mark.parametrize("state", sorted(set(RescueState) - OPEN_RESCUE_STATES, key=lambda s: s.value))
def test_a_settled_entry_never_moves(state: RescueState) -> None:
    assert next_state(state, RescueVerdict(RescueOutcome.PDF), 1) is state


# -- a stored verdict ---------------------------------------------------------


def test_a_verdict_round_trips() -> None:
    verdict = RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare", "captcha"))
    assert RescueVerdict.from_dict(verdict.to_dict()) == verdict


@pytest.mark.parametrize(
    "data", [None, [], "pdf", {"outcome": "teleported"}, {"outcome": ["pdf"]}, {}]
)
def test_an_unreadable_verdict_is_none(data: object) -> None:
    assert RescueVerdict.from_dict(data) is None


def test_a_stored_verdict_keeps_only_what_it_can_vouch_for() -> None:
    data = {"outcome": "no_pdf", "http_status": True, "markers": ["captcha", 7, None]}
    assert RescueVerdict.from_dict(data) == RescueVerdict(RescueOutcome.NO_PDF, None, ("captcha",))
    assert RescueVerdict.from_dict({"outcome": "gone", "http_status": 99}) == RescueVerdict(RescueOutcome.GONE)


# -- where a rescued PDF goes -------------------------------------------------


def test_a_rescued_pdf_lands_where_the_analysis_looks_first(tmp_path: Path) -> None:
    """The analysis asks discovery by DOI with no year: its expected path."""
    from bmlibrarian_lite.pdf_utils import find_existing_pdf

    path = rescue_pdf_path(DOI, tmp_path)
    path.write_bytes(b"%PDF-1.4")
    assert find_existing_pdf({"doi": DOI}, tmp_path) == path
    assert find_existing_pdf({"doi": DOI, "year": 2021}, tmp_path) == path


# -- queueing ------------------------------------------------------------------


class _Queue:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[tuple[RescueRequest, ...], int]] = []
        self.error = error

    def enqueue_rescues(self, requests, priority=0):  # type: ignore[no-untyped-def]
        if self.error is not None:
            raise self.error
        self.calls.append((tuple(requests), priority))
        return len(requests)


def test_challenged_copies_are_handed_to_the_queue() -> None:
    queue = _Queue()
    assert queue_challenged_copies(queue, SUBJECT, _record(_challenged())) == 1
    assert queue.calls == [(rescue_requests(SUBJECT, _record(_challenged())), 0)]


def test_nothing_challenged_asks_the_queue_nothing() -> None:
    queue = _Queue()
    assert queue_challenged_copies(queue, SUBJECT, LookupRecord()) == 0
    assert queue.calls == []


@pytest.mark.parametrize("error", [SQLiteError("locked"), sqlite3.OperationalError("locked")])
def test_a_queue_that_cannot_be_written_is_logged_not_raised(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    """The reader's sentence for the copy is already true; only a later try is lost."""
    assert queue_challenged_copies(_Queue(error), SUBJECT, _record(_challenged())) == 0
    assert "Could not queue" in caplog.text


def test_an_entry_carries_its_request() -> None:
    request = rescue_requests(SUBJECT, _record(_challenged()))[0]
    entry = RescueEntry(1, request, RescueState.PENDING, 0, None, 0, "t", "t")
    assert entry.request.address == ADDRESS
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_browser_rescue.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'bmlibrarian_lite.browser_rescue'`.

- [ ] **Step 4: Write `browser_rescue.py`**

Create `src/bmlibrarian_lite/browser_rescue.py` (licence header as in `bot_walls.py`):

```python
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
import sqlite3
from collections.abc import Mapping, Sequence
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
    PDF_MAGIC_BYTES,
    RESCUE_PRIORITY_DEFAULT,
    SERVICE_OPENALEX_PDF,
    SERVICE_UNPAYWALL_PDF,
)
from .data_models import LookupRecord, RequestFailureKind
from .exceptions import SQLiteError
from .pdf_utils import generate_pdf_path

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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_browser_rescue.py -q`
Expected: all pass.

- [ ] **Step 6: Lint and commit**

```bash
ruff check src/bmlibrarian_lite/browser_rescue.py tests/test_browser_rescue.py
mypy src/bmlibrarian_lite/browser_rescue.py
git add src/bmlibrarian_lite/constants.py src/bmlibrarian_lite/browser_rescue.py tests/test_browser_rescue.py
git commit -m "feat(fulltext): the browser rescue queue's pure rules (#480)"
```

---

### Task 2: The queue's table

**Files:**
- Create: `src/bmlibrarian_lite/rescue_queue_store.py`
- Modify: `src/bmlibrarian_lite/storage.py` (`_init_sqlite` ~line 218; new methods after the transparency section, before `create_benchmark_run`)
- Test: `tests/test_rescue_queue_store.py`

**Interfaces:**
- Consumes: Task 1's `RescueEntry`, `RescueRequest`, `RescueState`, `RescueVerdict`, `OPEN_RESCUE_STATES`, `next_state`; `RESCUE_RETRY_DELAY_SECONDS`.
- Produces:
  - `rescue_queue_store.SCHEMA`, `TABLE`, `stamp(moment) -> str`, `enqueue(conn, requests, priority, now) -> int`, `next_pending(conn, now) -> RescueEntry | None`, `record_verdict(conn, entry_id, verdict, now) -> RescueEntry | None`, `entries(conn, states) -> list[RescueEntry]`, `entry_from_row(row) -> RescueEntry | None`
  - `LiteStorage.enqueue_rescues(requests, priority=RESCUE_PRIORITY_DEFAULT) -> int`, `LiteStorage.next_rescue_entry() -> RescueEntry | None`, `LiteStorage.record_rescue_verdict(entry_id, verdict) -> RescueEntry | None`, `LiteStorage.rescue_entries(states) -> list[RescueEntry]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rescue_queue_store.py`:

```python
"""The browser rescue queue's table (#480, stage 2)."""

import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bmlibrarian_lite import rescue_queue_store as store
from bmlibrarian_lite.browser_rescue import (
    RescueOutcome,
    RescueRequest,
    RescueState,
    RescueVerdict,
)
from bmlibrarian_lite.config import LiteConfig
from bmlibrarian_lite.constants import (
    MAX_RESCUE_ATTEMPTS,
    RESCUE_PRIORITY_WAITING,
    RESCUE_RETRY_DELAY_SECONDS,
    SERVICE_UNPAYWALL_PDF,
)
from bmlibrarian_lite.storage import LiteStorage

T0 = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)
PDF = RescueVerdict(RescueOutcome.PDF)
TRANSPORT = RescueVerdict(RescueOutcome.TRANSPORT)


def _request(document_id: str = "doc-1", address: str = "https://a.example/1.pdf") -> RescueRequest:
    return RescueRequest(document_id, "10.1/x", None, "Title", address, "a.example", SERVICE_UNPAYWALL_PDF)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(store.SCHEMA)
    yield connection
    connection.close()


def _states(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(f"SELECT document_id, address, state FROM {store.TABLE}").fetchall()
    return {f"{r['document_id']} {r['address']}": r["state"] for r in rows}


def test_a_new_request_is_queued_pending(conn: sqlite3.Connection) -> None:
    assert store.enqueue(conn, [_request()], 0, T0) == 1
    entry = store.next_pending(conn, T0)
    assert entry is not None
    assert (entry.request, entry.state, entry.attempts, entry.verdict, entry.priority) == (
        _request(), RescueState.PENDING, 0, None, 0
    )


def test_enqueue_is_idempotent_and_never_reopens_a_settled_entry(conn: sqlite3.Connection) -> None:
    store.enqueue(conn, [_request()], 0, T0)
    assert store.enqueue(conn, [_request()], 0, T0) == 0
    entry = store.next_pending(conn, T0)
    assert entry is not None
    store.record_verdict(conn, entry.id, RescueVerdict(RescueOutcome.NO_PDF, 200), T0)
    assert store.enqueue(conn, [_request()], RESCUE_PRIORITY_WAITING, T0) == 0
    assert _states(conn) == {"doc-1 https://a.example/1.pdf": "no_pdf"}
    assert conn.execute(f"SELECT COUNT(*) FROM {store.TABLE}").fetchone()[0] == 1


def test_a_higher_priority_raises_an_open_entry(conn: sqlite3.Connection) -> None:
    store.enqueue(conn, [_request()], 0, T0)
    assert store.enqueue(conn, [_request()], RESCUE_PRIORITY_WAITING, T0) == 1
    entry = store.next_pending(conn, T0)
    assert entry is not None and entry.priority == RESCUE_PRIORITY_WAITING
    # and a lower one never lowers it
    assert store.enqueue(conn, [_request()], 0, T0) == 0


def test_the_next_entry_is_priority_first_then_oldest(conn: sqlite3.Connection) -> None:
    store.enqueue(conn, [_request("old")], 0, T0)
    store.enqueue(conn, [_request("new")], 0, T0 + timedelta(seconds=1))
    store.enqueue(conn, [_request("urgent")], RESCUE_PRIORITY_WAITING, T0 + timedelta(seconds=2))
    entry = store.next_pending(conn, T0 + timedelta(seconds=3))
    assert entry is not None and entry.request.document_id == "urgent"
    store.record_verdict(conn, entry.id, PDF, T0)
    entry = store.next_pending(conn, T0 + timedelta(seconds=3))
    assert entry is not None and entry.request.document_id == "old"


def test_a_transport_failure_waits_before_the_next_try(conn: sqlite3.Connection) -> None:
    store.enqueue(conn, [_request()], 0, T0)
    entry = store.next_pending(conn, T0)
    assert entry is not None
    updated = store.record_verdict(conn, entry.id, TRANSPORT, T0)
    assert updated is not None and (updated.state, updated.attempts) == (RescueState.PENDING, 1)
    assert store.next_pending(conn, T0 + timedelta(seconds=RESCUE_RETRY_DELAY_SECONDS - 1)) is None
    assert store.next_pending(conn, T0 + timedelta(seconds=RESCUE_RETRY_DELAY_SECONDS)) is not None


def test_the_attempt_cap_hands_the_entry_to_a_person(conn: sqlite3.Connection) -> None:
    store.enqueue(conn, [_request()], 0, T0)
    entry = store.next_pending(conn, T0)
    assert entry is not None
    now = T0
    for _ in range(MAX_RESCUE_ATTEMPTS):
        updated = store.record_verdict(conn, entry.id, TRANSPORT, now)
        now += timedelta(seconds=RESCUE_RETRY_DELAY_SECONDS)
    assert updated is not None
    assert (updated.state, updated.attempts) == (RescueState.NEEDS_PERSON, MAX_RESCUE_ATTEMPTS)
    assert store.next_pending(conn, now) is None
    row = conn.execute(f"SELECT retry_after FROM {store.TABLE}").fetchone()
    assert row["retry_after"] is None


def test_a_rescue_supersedes_the_documents_other_open_entries(conn: sqlite3.Connection) -> None:
    store.enqueue(
        conn,
        [_request("doc-1", "https://a.example/1.pdf"), _request("doc-1", "https://b.example/1.pdf"),
         _request("doc-2", "https://a.example/2.pdf")],
        0,
        T0,
    )
    entry = store.next_pending(conn, T0)
    assert entry is not None and entry.request.address == "https://a.example/1.pdf"
    store.record_verdict(conn, entry.id, PDF, T0)
    assert _states(conn) == {
        "doc-1 https://a.example/1.pdf": "rescued",
        "doc-1 https://b.example/1.pdf": "superseded",
        "doc-2 https://a.example/2.pdf": "pending",
    }


def test_a_settled_sibling_is_not_superseded(conn: sqlite3.Connection) -> None:
    store.enqueue(conn, [_request("doc-1", "https://a.example/1.pdf")], 0, T0)
    store.enqueue(conn, [_request("doc-1", "https://b.example/1.pdf")], 0, T0 + timedelta(seconds=1))
    first = store.next_pending(conn, T0 + timedelta(seconds=2))
    assert first is not None
    store.record_verdict(conn, first.id, RescueVerdict(RescueOutcome.GONE, 404), T0)
    second = store.next_pending(conn, T0 + timedelta(seconds=2))
    assert second is not None
    store.record_verdict(conn, second.id, PDF, T0)
    assert _states(conn)["doc-1 https://a.example/1.pdf"] == "gone"


def test_a_verdict_for_an_entry_no_longer_open_changes_nothing(conn: sqlite3.Connection) -> None:
    store.enqueue(conn, [_request()], 0, T0)
    entry = store.next_pending(conn, T0)
    assert entry is not None
    conn.execute(f"UPDATE {store.TABLE} SET state = 'dismissed'")
    conn.commit()
    unchanged = store.record_verdict(conn, entry.id, PDF, T0)
    assert unchanged is not None and unchanged.state is RescueState.DISMISSED
    assert _states(conn) == {"doc-1 https://a.example/1.pdf": "dismissed"}


def test_a_verdict_for_a_missing_entry_is_none(conn: sqlite3.Connection) -> None:
    assert store.record_verdict(conn, 999, PDF, T0) is None


def test_the_verdict_is_stored_as_json(conn: sqlite3.Connection) -> None:
    store.enqueue(conn, [_request()], 0, T0)
    entry = store.next_pending(conn, T0)
    assert entry is not None
    verdict = RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare",))
    store.record_verdict(conn, entry.id, verdict, T0)
    row = conn.execute(f"SELECT verdict FROM {store.TABLE}").fetchone()
    assert json.loads(row["verdict"]) == verdict.to_dict()
    (stored,) = store.entries(conn, [RescueState.NEEDS_PERSON])
    assert stored.verdict == verdict


def test_a_newer_builds_state_is_left_alone(conn: sqlite3.Connection) -> None:
    store.enqueue(conn, [_request()], 0, T0)
    conn.execute(f"UPDATE {store.TABLE} SET state = 'teleported'")
    conn.commit()
    assert store.next_pending(conn, T0) is None
    assert store.entries(conn, list(RescueState)) == []


def test_a_damaged_verdict_reads_as_none(conn: sqlite3.Connection) -> None:
    store.enqueue(conn, [_request()], 0, T0)
    conn.execute(f"UPDATE {store.TABLE} SET verdict = '{{not json'")
    conn.commit()
    entry = store.next_pending(conn, T0)
    assert entry is not None and entry.verdict is None


def test_stamps_sort_as_time_does() -> None:
    assert store.stamp(T0) < store.stamp(T0 + timedelta(microseconds=1))
    assert store.stamp(T0.astimezone(timezone(timedelta(hours=10)))) == store.stamp(T0)


# -- through LiteStorage --------------------------------------------------------


@pytest.fixture
def storage(tmp_path: Path) -> LiteStorage:
    config = LiteConfig()
    config.storage.data_dir = tmp_path
    return LiteStorage(config)


def test_storage_creates_the_table_and_round_trips_an_entry(storage: LiteStorage) -> None:
    assert storage.enqueue_rescues([_request()]) == 1
    entry = storage.next_rescue_entry()
    assert entry is not None
    updated = storage.record_rescue_verdict(entry.id, PDF)
    assert updated is not None and updated.state is RescueState.RESCUED
    assert [e.state for e in storage.rescue_entries([RescueState.RESCUED])] == [RescueState.RESCUED]


def test_a_second_storage_on_the_same_file_sees_the_queue(storage: LiteStorage) -> None:
    """The MCP server writes from another process; the GUI reads the same file."""
    LiteStorage(storage.config).enqueue_rescues([_request()])
    assert storage.next_rescue_entry() is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_rescue_queue_store.py -q`
Expected: collection error, `No module named 'bmlibrarian_lite.rescue_queue_store'`.

- [ ] **Step 3: Write `rescue_queue_store.py`**

```python
"""The browser rescue queue's table (#480, stage 2).

SQL only; the rules are ``browser_rescue``'s. Every function takes an open
connection whose ``row_factory`` is ``sqlite3.Row``. ``LiteStorage`` opens
it and commits after :func:`enqueue`. :func:`record_verdict` runs its own
transaction, because the MCP server and the CLI write this table from other
processes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .browser_rescue import (
    OPEN_RESCUE_STATES,
    RescueEntry,
    RescueRequest,
    RescueState,
    RescueVerdict,
    next_state,
)
from .constants import RESCUE_RETRY_DELAY_SECONDS

logger = logging.getLogger(__name__)

TABLE = "browser_rescue_queue"

# No foreign key to documents: the MCP server queues under its own document
# ID, which storage may not hold (spec section 2).
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    doi TEXT NOT NULL,
    pmid TEXT,
    title TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL,
    host TEXT NOT NULL,
    service TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    verdict TEXT,      -- JSON, RescueVerdict.to_dict(); NULL before a visit
    priority INTEGER NOT NULL DEFAULT 0,
    retry_after TEXT,  -- NULL: may be visited now
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (document_id, address)
);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_next
    ON {TABLE}(state, priority, created_at);
"""

#: The open states' stored values, for SQL.
_OPEN = tuple(state.value for state in (RescueState.PENDING, RescueState.NEEDS_PERSON))


def stamp(moment: datetime) -> str:
    """A moment as stored: UTC ISO 8601 to the microsecond, so text order is time order.

    Args:
        moment: A timezone-aware moment.

    Returns:
        The stored text.
    """
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def entry_from_row(row: sqlite3.Row) -> RescueEntry | None:
    """Read one stored entry.

    Args:
        row: A row of the table.

    Returns:
        The entry, its verdict ``None`` when unreadable; ``None`` for a
        state this build does not know (a newer build's), which is left
        alone.
    """
    try:
        state = RescueState(row["state"])
    except ValueError:
        logger.debug("Leaving rescue entry %s alone: state %r is a newer build's", row["id"], row["state"])
        return None
    verdict = None
    if row["verdict"] is not None:
        try:
            verdict = RescueVerdict.from_dict(json.loads(row["verdict"]))
        except (TypeError, ValueError):
            verdict = None
    return RescueEntry(
        id=row["id"],
        request=RescueRequest(
            document_id=row["document_id"],
            doi=row["doi"],
            pmid=row["pmid"],
            title=row["title"],
            address=row["address"],
            host=row["host"],
            service=row["service"],
        ),
        state=state,
        attempts=row["attempts"],
        verdict=verdict,
        priority=row["priority"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def enqueue(conn: sqlite3.Connection, requests: Sequence[RescueRequest], priority: int, now: datetime) -> int:
    """Queue walled PDFs, idempotently; the caller commits.

    A new (document, address) is added ``pending``. An existing one is left
    as it is, open or settled, so a later run never reopens a settled entry.
    The one exception: a higher priority raises an open entry's.

    Args:
        conn: The connection.
        requests: What to queue.
        priority: Their priority.
        now: The moment, as ``created_at`` and ``updated_at``.

    Returns:
        How many entries were added or raised.
    """
    at = stamp(now)
    changed = 0
    for request in requests:
        cursor = conn.execute(
            f"""
            INSERT INTO {TABLE} (
                document_id, doi, pmid, title, address, host, service,
                state, priority, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (document_id, address) DO UPDATE
                SET priority = excluded.priority, updated_at = excluded.updated_at
                WHERE {TABLE}.priority < excluded.priority
                  AND {TABLE}.state IN (?, ?)
            """,
            (
                request.document_id, request.doi, request.pmid, request.title,
                request.address, request.host, request.service,
                RescueState.PENDING.value, priority, at, at, *_OPEN,
            ),
        )
        changed += cursor.rowcount
    return changed


def next_pending(conn: sqlite3.Connection, now: datetime) -> RescueEntry | None:
    """The entry the hidden browser visits next.

    Args:
        conn: The connection.
        now: The moment; an entry held back past it is not due.

    Returns:
        The due ``pending`` entry with the highest priority, then the
        oldest; ``None`` when none is due.
    """
    rows = conn.execute(
        f"""
        SELECT * FROM {TABLE}
        WHERE state = ? AND (retry_after IS NULL OR retry_after <= ?)
        ORDER BY priority DESC, created_at, id
        """,
        (RescueState.PENDING.value, stamp(now)),
    )
    for row in rows:
        entry = entry_from_row(row)
        if entry is not None:
            return entry
    return None


def record_verdict(
    conn: sqlite3.Connection, entry_id: int, verdict: RescueVerdict, now: datetime
) -> RescueEntry | None:
    """Apply one visit's verdict to its entry; on a rescue, supersede its siblings.

    One immediate transaction, taken before the row is read, so a dismissal
    or rescue another process wrote in between is not overwritten. A
    transport failure that leaves the entry ``pending`` holds it back for
    ``RESCUE_RETRY_DELAY_SECONDS``.

    Args:
        conn: The connection; no transaction may be open on it.
        entry_id: The entry visited.
        verdict: What the visit concluded.
        now: The moment.

    Returns:
        The entry as it now stands. It is unchanged when no longer open
        (dismissed or settled meanwhile). ``None`` when it is gone or its
        state is a newer build's.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(f"SELECT * FROM {TABLE} WHERE id = ?", (entry_id,)).fetchone()
        entry = entry_from_row(row) if row is not None else None
        if entry is None or entry.state not in OPEN_RESCUE_STATES:
            conn.rollback()
            return entry
        attempts = entry.attempts + 1
        state = next_state(entry.state, verdict, attempts)
        at = stamp(now)
        retry_after = (
            stamp(now + timedelta(seconds=RESCUE_RETRY_DELAY_SECONDS))
            if state is RescueState.PENDING
            else None
        )
        conn.execute(
            f"""
            UPDATE {TABLE}
            SET state = ?, attempts = ?, verdict = ?, retry_after = ?, updated_at = ?
            WHERE id = ?
            """,
            (state.value, attempts, json.dumps(verdict.to_dict()), retry_after, at, entry_id),
        )
        if state is RescueState.RESCUED:
            conn.execute(
                f"""
                UPDATE {TABLE} SET state = ?, updated_at = ?
                WHERE document_id = ? AND id != ? AND state IN (?, ?)
                """,
                (RescueState.SUPERSEDED.value, at, entry.request.document_id, entry_id, *_OPEN),
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return replace(entry, state=state, attempts=attempts, verdict=verdict, updated_at=at)


def entries(conn: sqlite3.Connection, states: Iterable[RescueState]) -> list[RescueEntry]:
    """The entries in the given states, as the queue window orders them.

    Args:
        conn: The connection.
        states: The states wanted.

    Returns:
        Highest priority first, then oldest; rows this build cannot read
        are left out.
    """
    values = [state.value for state in states]
    if not values:
        return []
    marks = ", ".join("?" for _ in values)
    rows = conn.execute(
        f"SELECT * FROM {TABLE} WHERE state IN ({marks}) ORDER BY priority DESC, created_at, id",
        values,
    )
    return [entry for entry in map(entry_from_row, rows) if entry is not None]
```

- [ ] **Step 4: Wire it into `LiteStorage`**

In `src/bmlibrarian_lite/storage.py`:

1. Imports: change `from datetime import datetime` to `from datetime import datetime, timezone`. Add `from collections.abc import Iterable, Sequence` (merge with any existing `collections.abc` import). Add:

```python
from . import rescue_queue_store
from .browser_rescue import RescueEntry, RescueRequest, RescueState, RescueVerdict
```

and `RESCUE_PRIORITY_DEFAULT` to the existing `from .constants import (...)`.

2. In `_init_sqlite`, directly after `conn.executescript(self._get_sqlite_schema())`:

```python
                # The browser rescue queue (#480, stage 2), kept beside its SQL
                conn.executescript(rescue_queue_store.SCHEMA)
```

3. New section before `def create_benchmark_run(`:

```python
    # =========================================================================
    # Browser rescue queue (#480, stage 2)
    # =========================================================================

    def enqueue_rescues(
        self,
        requests: Sequence[RescueRequest],
        priority: int = RESCUE_PRIORITY_DEFAULT,
    ) -> int:
        """Queue walled PDFs for the embedded browser, idempotently.

        Args:
            requests: What to queue.
            priority: Their priority; a higher one raises an open entry's.

        Returns:
            How many entries were added or raised.
        """
        with self._sqlite_connection() as conn:
            changed = rescue_queue_store.enqueue(
                conn, requests, priority, datetime.now(timezone.utc)
            )
            conn.commit()
        return changed

    def next_rescue_entry(self) -> RescueEntry | None:
        """The entry the hidden browser visits next.

        Returns:
            The due pending entry with the highest priority, then the
            oldest; ``None`` when none is due.
        """
        with self._sqlite_connection() as conn:
            return rescue_queue_store.next_pending(conn, datetime.now(timezone.utc))

    def record_rescue_verdict(
        self, entry_id: int, verdict: RescueVerdict
    ) -> RescueEntry | None:
        """Apply one browser visit's verdict to its entry.

        Args:
            entry_id: The entry visited.
            verdict: What the visit concluded.

        Returns:
            The entry as it now stands; ``None`` when it is gone.
        """
        with self._sqlite_connection() as conn:
            return rescue_queue_store.record_verdict(
                conn, entry_id, verdict, datetime.now(timezone.utc)
            )

    def rescue_entries(self, states: Iterable[RescueState]) -> list[RescueEntry]:
        """The queue's entries in the given states.

        Args:
            states: The states wanted.

        Returns:
            Highest priority first, then oldest.
        """
        with self._sqlite_connection() as conn:
            return rescue_queue_store.entries(conn, states)
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_rescue_queue_store.py tests/test_browser_rescue.py -q`
Expected: all pass. If `test_enqueue_is_idempotent…` fails because `cursor.rowcount` reports 1 for an UPSERT whose `WHERE` was false, count the change instead with `conn.total_changes` before and after each statement. Then re-run.

Then run the storage suites for regressions: `pytest tests/ -q -k "storage or transparency_row or checkpoint"`. Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/bmlibrarian_lite/rescue_queue_store.py src/bmlibrarian_lite/storage.py tests/test_rescue_queue_store.py
git commit -m "feat(storage): the browser rescue queue table (#480)"
```

---

### Task 3: A pacing slot without sleeping

**Files:**
- Modify: `src/bmlibrarian_lite/rate_limit.py` (after `acquire`, ~line 237)
- Modify: `src/bmlibrarian_lite/browser_rescue.py` (add `rescue_delay_ms`)
- Test: `tests/test_rate_limit.py`, `tests/test_browser_rescue.py`

**Interfaces:**
- Produces: `RateLimiter.claim() -> float` (seconds to wait), `browser_rescue.rescue_delay_ms(host: str) -> int`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rate_limit.py` (it already imports `RateLimiter` and `HostPolicy`; add whatever of these is missing):

```python
def test_claim_takes_the_next_slot_without_sleeping() -> None:
    """The browser rescue schedules its visit for the slot (#480, stage 2)."""
    now = [100.0]
    slept: list[float] = []
    limiter = RateLimiter(HostPolicy(1.0), clock=lambda: now[0], sleep=slept.append)
    assert limiter.claim() == 0.0
    assert limiter.claim() == pytest.approx(1.0)
    assert limiter.claim() == pytest.approx(2.0)
    assert slept == []


def test_a_claim_counts_against_the_budget_acquire_shares() -> None:
    now = [100.0]
    slept: list[float] = []
    limiter = RateLimiter(HostPolicy(1.0), clock=lambda: now[0], sleep=slept.append)
    limiter.claim()
    limiter.acquire()
    assert slept == [pytest.approx(1.0)]
```

Append to `tests/test_browser_rescue.py`:

```python
def test_a_loopback_visit_is_never_paced() -> None:
    from bmlibrarian_lite.browser_rescue import rescue_delay_ms

    assert rescue_delay_ms("127.0.0.1") == 0
    assert rescue_delay_ms("localhost") == 0


def test_a_visit_takes_its_hosts_shared_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    from bmlibrarian_lite import browser_rescue

    claimed: list[str] = []

    class _Limiter:
        def claim(self) -> float:
            return 1.2345

    def _limiter_for(host: str) -> _Limiter:
        claimed.append(host)
        return _Limiter()

    monkeypatch.setattr(browser_rescue, "limiter_for", _limiter_for)
    assert browser_rescue.rescue_delay_ms("pubs.rsc.org") == 1235
    assert claimed == ["pubs.rsc.org"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_rate_limit.py tests/test_browser_rescue.py -q -k "claim or paced or slot"`
Expected: FAIL, `AttributeError: 'RateLimiter' object has no attribute 'claim'` and `ImportError: cannot import name 'rescue_delay_ms'`.

- [ ] **Step 3: Implement**

In `rate_limit.py`, after `acquire`:

```python
    def claim(self) -> float:
        """Claim the next slot for this host without waiting for it.

        For a caller that cannot sleep -- the browser rescue on the GUI
        thread, which schedules its visit for the slot instead (#480, stage
        2). The slot is taken from the same budget :meth:`acquire` spends.
        Unlike :meth:`acquire`, a ``Retry-After`` that arrives after the claim
        does not move it; the browser asks one address at a time, so at most
        one visit departs into such a pause.

        Returns:
            Seconds until the claimed slot; 0 when it is now.
        """
        _, wait = self._claim_slot()
        return wait
```

In `browser_rescue.py`, add `import math` to the imports, `MS_PER_SECOND` to the constants import, and:

```python
from .polite_session import is_loopback_host
from .rate_limit import limiter_for
```

Then:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_rate_limit.py tests/test_browser_rescue.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/bmlibrarian_lite/rate_limit.py src/bmlibrarian_lite/browser_rescue.py tests/test_rate_limit.py tests/test_browser_rescue.py
git commit -m "feat(pacing): claim a slot without sleeping, for the browser rescue (#480)"
```

---

### Task 4: A challenged copy is not a paywall (D1)

**Files:**
- Modify: `src/bmlibrarian_lite/pdf_discovery.py` (`_try_download`, ~lines 2085–2156)
- Modify: `tests/test_challenged_pdf_download.py:115`
- Test: `tests/test_unobtained_unpaywall_pdf.py`

**Interfaces:**
- Changes: `PDFDiscoverer._try_download` returns `DiscoveryResult(success=False, error=challenge.describe(), failure=challenge)` with `is_paywall=False` for every challenged answer.

- [ ] **Step 1: Write the failing tests**

In `tests/test_challenged_pdf_download.py`, change line 115 from

```python
    assert result.is_paywall is True  # the branch it took is unchanged
```

to

```python
    # A wall's challenge is not a paywall: the browser queue takes the copy (#480, stage 2)
    assert result.is_paywall is False
```

Append to `tests/test_unobtained_unpaywall_pdf.py`:

```python
CHALLENGED_SENTENCE = (
    "Failed to obtain a PDF from the following tried sources: repo.example.org, "
    "named by Unpaywall (the host asked for a robot check). A freely available "
    "copy may exist. Whether this document is open access was not established."
)


@pytest.mark.parametrize(
    ("status", "url"),
    [
        (403, UNPAYWALL_PDF),
        (401, UNPAYWALL_PDF),
        # A 200 page at an address the paywall sniff matches ("access")
        (200, "https://repo.example.org/openaccess/a.pdf"),
    ],
)
def test_a_challenged_copy_is_not_a_paywall(tmp_path: Path, status: int, url: str) -> None:
    """The reader is told the copy could not be asked, and offered no login (#480, stage 2)."""
    page = b"<html><title>Just a moment...</title></html>"
    session = _Session({url: _response(status, page, "text/html", url)})

    result = _discover([_unpaywall(url)], tmp_path, session)

    assert not result.is_paywall
    assert result.paywall_url is None
    assert result.lookups == _pdf_failure(RequestFailure(RequestFailureKind.CHALLENGED, status), url)
    assert result.error == CHALLENGED_SENTENCE


def test_a_plain_refusal_is_still_a_paywall(tmp_path: Path) -> None:
    """Control: a 403 that names no wall keeps the paywall branch."""
    page = b"<html><title>Forbidden</title></html>"
    session = _Session({UNPAYWALL_PDF: _response(403, page, "text/html", UNPAYWALL_PDF)})

    result = _discover([_unpaywall()], tmp_path, session)

    assert result.is_paywall
```

(`RequestFailureKind`, `RequestFailure`, `pytest` and `Path` are already imported there. Check with `grep -n "^from\|^import" tests/test_unobtained_unpaywall_pdf.py` and add any that are missing.)

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_challenged_pdf_download.py tests/test_unobtained_unpaywall_pdf.py -q -k "cloudflare_403 or challenged_copy or plain_refusal"`
Expected: the challenged cases FAIL with `assert not True` (`is_paywall`). The control passes.

- [ ] **Step 3: Implement**

In `_try_download`, directly after the block that ends with the `logger.warning(f"A bot wall's challenge answered …")`, add:

```python
            # A bot wall's challenge is no paywall: the copy is free, and the
            # browser queue takes it (#480, stage 2). Returned before the
            # paywall sniff, which matched it by its status (401, 403) or by
            # a word in its address ("access", "pay"), and told the reader "A
            # source refused access" with an OpenAthens offer.
            if challenge is not None:
                return DiscoveryResult(
                    success=False,
                    error=challenge.describe(),
                    failure=challenge,
                )
```

Then simplify what follows, because `challenge` is now always `None` there:
- In the error-status branch, delete the inner `if challenge is not None: return …` block. Keep `response.raise_for_status()`.
- In the paywall branch, `failure=challenge or refused_download_failure(response.status_code)` becomes `failure=refused_download_failure(response.status_code)`.
- In the non-PDF branch, `failure=challenge or RequestFailure(RequestFailureKind.MALFORMED_RESPONSE)` becomes `failure=RequestFailure(RequestFailureKind.MALFORMED_RESPONSE)`.
- Update the comment block above `challenge = None`: drop "for a non-paywall error status (429, 5xx) also the error text, below"; a challenge now ends the attempt there.
- In `refused_download_failure`'s docstring, change the last sentence to "An open-access copy's page that … recognises as a bot wall's challenge never reaches here: it is recorded as ``CHALLENGED`` and is no paywall (#480)."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_challenged_pdf_download.py tests/test_unobtained_unpaywall_pdf.py tests/test_oa_landing_page.py tests/test_openalex_discovery.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/bmlibrarian_lite/pdf_discovery.py tests/test_challenged_pdf_download.py tests/test_unobtained_unpaywall_pdf.py
git commit -m "fix(fulltext): a bot wall's challenge is not a paywall (#480)"
```

---

### Task 5: The enqueue sites

**Files:**
- Modify: `src/bmlibrarian_lite/study_transparency_analyzer/study_transparency_analyzer.py` (`TransparencyReport` ~line 283; `_discover_fulltext` ~line 3024)
- Modify: `src/bmlibrarian_lite/transparency/assessment.py` (`assess_document`)
- Modify: `src/bmlibrarian_lite/mcp_server.py` (`_handle_fulltext`, ~line 646)
- Test: `tests/test_rescue_enqueue_sites.py` (new)

**Interfaces:**
- Consumes: `queue_challenged_copies`, `RescueSubject` (Task 1); `LiteStorage.enqueue_rescues`, `rescue_entries` (Task 2).
- Produces: `TransparencyReport.fulltext_lookups: LookupRecord` (empty unless discovery ran and obtained no full text).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rescue_enqueue_sites.py`:

```python
"""Where walled PDFs are queued for the browser (#480, stage 2)."""

import logging
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bmlibrarian_lite.browser_rescue import OPEN_RESCUE_STATES, RescueState
from bmlibrarian_lite.config import LiteConfig
from bmlibrarian_lite.constants import SERVICE_UNPAYWALL_PDF
from bmlibrarian_lite.data_models import (
    LookupRecord,
    RequestFailure,
    RequestFailureKind,
    SourceLookupFailure,
)
from bmlibrarian_lite.fulltext_discovery import FulltextResult, FulltextSourceType
from bmlibrarian_lite.mcp_server import _AgentsContext, _handle_fulltext
from bmlibrarian_lite.storage import LiteStorage
from bmlibrarian_lite.study_transparency_analyzer.study_transparency_analyzer import (
    StudyTransparencyAnalyzer,
    TransparencyReport,
)
from bmlibrarian_lite.transparency.assessment import assess_document
from bmlibrarian_lite.transparency.transparency_settings import TransparencySettings

DOI = "10.1016/j.example.2021.1"
ADDRESS = "https://www.sciencedirect.com/science/article/pii/S1/pdfft"
RECORD = LookupRecord(
    failures=(
        SourceLookupFailure(
            SERVICE_UNPAYWALL_PDF, RequestFailure(RequestFailureKind.CHALLENGED, 403), ADDRESS
        ),
    )
)


class _Discoverer:
    def __init__(self, result: FulltextResult) -> None:
        self.result = result

    def discover_fulltext(self, **kwargs: object) -> FulltextResult:
        return self.result


def _analyzer(result: FulltextResult) -> StudyTransparencyAnalyzer:
    analyzer = StudyTransparencyAnalyzer(email="test@example.com", auto_discover_fulltext=True)
    analyzer._fetch_basic_metadata = lambda report: None  # type: ignore[method-assign]
    analyzer._fetch_funder_info = lambda report: None  # type: ignore[method-assign]
    analyzer._fetch_trial_info = lambda report: None  # type: ignore[method-assign]
    analyzer._fulltext_discoverer = lambda: _Discoverer(result)  # type: ignore[method-assign,assignment,return-value]
    return analyzer


def test_the_report_carries_what_an_unsuccessful_discovery_left() -> None:
    result = FulltextResult(success=False, source_type=FulltextSourceType.NOT_ASSESSED, lookups=RECORD)
    assert _analyzer(result).analyze(doi=DOI).fulltext_lookups == RECORD


def test_a_successful_discovery_leaves_nothing_to_queue() -> None:
    result = FulltextResult(
        success=True,
        source_type=FulltextSourceType.EUROPEPMC_XML,
        markdown_content="# Methods\n\nText.",
        lookups=RECORD,
    )
    assert _analyzer(result).analyze(doi=DOI).fulltext_lookups == LookupRecord()


def test_a_report_without_discovery_has_an_empty_record() -> None:
    assert TransparencyReport().fulltext_lookups == LookupRecord()


@pytest.fixture
def storage(tmp_path: Path) -> LiteStorage:
    config = LiteConfig()
    config.storage.data_dir = tmp_path
    return LiteStorage(config)


def _report_analyzer(report: TransparencyReport) -> MagicMock:
    analyzer = MagicMock()
    analyzer.analyze.return_value = report
    return analyzer


def test_an_assessment_queues_its_walled_copies(storage: LiteStorage) -> None:
    report = TransparencyReport(doi=DOI, pmid="123", title="A trial", fulltext_lookups=RECORD)
    assess_document(_report_analyzer(report), storage, TransparencySettings(), "doc-1", "123", None)
    (entry,) = storage.rescue_entries(OPEN_RESCUE_STATES)
    assert (entry.request.document_id, entry.request.doi, entry.request.address, entry.state) == (
        "doc-1", DOI, ADDRESS, RescueState.PENDING
    )
    assert storage.get_transparency_result("doc-1") is not None


def test_an_assessment_that_cannot_queue_still_stores_its_result(
    storage: LiteStorage, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _locked(*args: object, **kwargs: object) -> int:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(storage, "enqueue_rescues", _locked)
    report = TransparencyReport(doi=DOI, fulltext_lookups=RECORD)
    with caplog.at_level(logging.ERROR):
        result = assess_document(_report_analyzer(report), storage, TransparencySettings(), "doc-1", None, DOI)
    assert result.document_id == "doc-1"
    assert storage.get_transparency_result("doc-1") is not None
    assert "Could not queue" in caplog.text


def _context(result: FulltextResult) -> _AgentsContext:
    discoverer = MagicMock()
    discoverer.discover_fulltext.return_value = result
    storage = MagicMock()
    storage.enqueue_rescues.return_value = 1  # a count, as LiteStorage returns
    return _AgentsContext(
        config=MagicMock(), storage=storage, llm_client=MagicMock(), search_agent=MagicMock(),
        scoring_agent=MagicMock(), citation_agent=MagicMock(), reporting_agent=MagicMock(),
        interrogation_agent=MagicMock(), fulltext_discoverer=discoverer,
    )


def test_the_mcp_server_queues_its_walled_copies_under_its_own_id() -> None:
    ctx = _context(FulltextResult(success=False, source_type=FulltextSourceType.NOT_ASSESSED, lookups=RECORD))
    payload = _handle_fulltext({"doi": DOI}, ctx)
    assert payload["success"] is False
    ((requests, priority), _) = ctx.storage.enqueue_rescues.call_args
    assert [(r.document_id, r.address) for r in requests] == [(f"doi-{DOI}", ADDRESS)]
    assert priority == 0


def test_the_mcp_server_queues_nothing_after_a_success() -> None:
    ctx = _context(
        FulltextResult(success=True, source_type=FulltextSourceType.EUROPEPMC_XML, markdown_content="text")
    )
    _handle_fulltext({"doi": DOI}, ctx)
    ctx.storage.enqueue_rescues.assert_not_called()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_rescue_enqueue_sites.py -q`
Expected: FAIL, `TypeError: TransparencyReport.__init__() got an unexpected keyword argument 'fulltext_lookups'` (and the analyser tests fail on `AttributeError`).

- [ ] **Step 3: Implement**

1. `TransparencyReport`: add as its last field (`LookupRecord` is already imported in that module):

```python
    # What full-text discovery left unsettled, when it ran and obtained no
    # full text: the browser rescue queues its walled copies (#480, stage 2).
    fulltext_lookups: LookupRecord = field(default_factory=LookupRecord)
```

2. `_discover_fulltext`: directly after the `if result.success and result.markdown_content:` block returns, before `if result.is_paywall:`, add:

```python
            # Kept for the caller, which queues the walled copies for the
            # embedded browser (#480, stage 2). Only here: a discovery that
            # obtained the text has nothing to rescue.
            report.fulltext_lookups = result.lookups
```

3. `assessment.py`: import

```python
from ..browser_rescue import RescueSubject, queue_challenged_copies
```

and in `assess_document`, after `storage.save_transparency_result(result)`:

```python
    # Walled open-access copies go to the browser rescue queue, after the
    # result is stored: a queue that cannot be written is logged and does
    # not fail the analysis (#480, stage 2).
    queue_challenged_copies(
        storage,
        RescueSubject(document_id, report.doi or doi, report.pmid or pmid, report.title or ""),
        report.fulltext_lookups,
    )
```

Update `assess_document`'s docstring summary to "Analyse one document, store the result, and queue its walled PDFs."

4. `mcp_server.py`: import `from bmlibrarian_lite.browser_rescue import RescueSubject, queue_challenged_copies`. In `_handle_fulltext`, move the document ID line above the failure branch:

```python
    # Built from the identifiers asked with; also the ID a walled copy is
    # queued under, which storage may not hold (#480, stage 2)
    doc_id = f"pmid-{pmid}" if pmid else f"doi-{doi}" if doi else f"pmc-{pmc_id}"
```

and as the first statement inside `if not result.success or not result.markdown_content:`:

```python
        # The GUI's hidden browser tries the walled copies; a later request
        # finds the PDF in the cache (#480, stage 2)
        queue_challenged_copies(
            ctx.storage,
            RescueSubject(
                doc_id, doi, pmid, result.article_info.title if result.article_info else ""
            ),
            result.lookups,
        )
```

Delete the old `doc_id = …` line further down.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_rescue_enqueue_sites.py tests/test_mcp_server.py tests/test_transparency_manager.py tests/test_full_text_analyzed.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/bmlibrarian_lite/study_transparency_analyzer/study_transparency_analyzer.py src/bmlibrarian_lite/transparency/assessment.py src/bmlibrarian_lite/mcp_server.py tests/test_rescue_enqueue_sites.py
git commit -m "feat(fulltext): queue walled PDFs from the analysis and the MCP server (#480)"
```

---

### Task 6: Retire the Playwright fallback (#483)

**Files:**
- Modify: `src/bmlibrarian_lite/pdf_discovery.py` (delete lines ~231–438; the parameters; the `blocked_oa_sources` routing; `_try_browser_download`)
- Modify: `src/bmlibrarian_lite/fulltext_discovery.py`, `study_transparency_analyzer/study_transparency_analyzer.py` (+ its `README.md`, `DOCUMENTATION.md`), `transparency/assessment.py`, `mcp_server.py`, `__init__.py`
- Modify: every test passing `use_browser_fallback=False` (about 60 sites), plus `tests/test_transparency_manager.py`'s `assert_called_once_with`
- Test: `tests/test_playwright_fallback_retired.py` (new)

- [ ] **Step 1: Write the failing guard test**

```python
"""The Playwright fallback is gone (#483); the browser rescue replaces it (#480)."""

import inspect

import pytest

from bmlibrarian_lite import pdf_discovery
from bmlibrarian_lite.fulltext_discovery import FulltextDiscoverer
from bmlibrarian_lite.pdf_discovery import PDFDiscoverer
from bmlibrarian_lite.study_transparency_analyzer import study_transparency_analyzer as sta


@pytest.mark.parametrize("cls", [PDFDiscoverer, FulltextDiscoverer, sta.StudyTransparencyAnalyzer])
def test_no_discovery_takes_a_browser_fallback(cls: type) -> None:
    parameters = inspect.signature(cls).parameters
    assert "use_browser_fallback" not in parameters
    assert "browser_headless" not in parameters


@pytest.mark.parametrize(
    "name", ["BrowserSession", "get_browser_session", "close_browser_session"]
)
def test_the_playwright_session_is_gone(name: str) -> None:
    assert not hasattr(pdf_discovery, name)
    assert not hasattr(PDFDiscoverer, "_try_browser_download")


def test_the_analyser_cli_has_no_browser_flag() -> None:
    assert "--no-browser" not in inspect.getsource(sta)
```

Run: `pytest tests/test_playwright_fallback_retired.py -q`. Expected: FAIL.

- [ ] **Step 2: Remove the code**

- `pdf_discovery.py`: delete `_browser_session`, `_browser_lock`, `BrowserSession`, `get_browser_session`, `close_browser_session` and `_try_browser_download`. Drop `use_browser_fallback`/`browser_headless` from `PDFDiscoverer.__init__` (signature, docstring, attributes). In `discover_and_download`, delete `blocked_oa_sources` and the whole "If we have blocked OA sources and browser fallback is enabled" block. The open-access paywall branch becomes:

```python
                if source.is_open_access:
                    # A refusal of an open-access copy is held, and the other
                    # sources are tried first
                    logger.info(f"Source {source.url} refused us; trying the next source")
                    last_paywall_result = result
                    continue
```

  Remove "Browser-based download (Playwright) for bot-protected sites" from the module docstring and "Browser-based download for bot-protected sites" from the class docstring. In the comment block near line 136, change "The download paths (``_try_download``, ``_try_browser_download``)" to "The download path (``_try_download``)". Remove imports left unused (`threading`, and `Tuple` if nothing else uses it; let `ruff check` say).
- `fulltext_discovery.py`: drop the two parameters, their docstring lines, `self.use_browser_fallback`, `self.browser_headless`, and the two keyword arguments to `PDFDiscoverer(...)`. The module-level `discover_fulltext()` helper (~line 1093) forwards them too: drop them there.
- `study_transparency_analyzer.py`: drop the two parameters, docstring lines, attributes and the arguments in `_fulltext_discoverer`. Delete the `--no-browser` argument and `use_browser_fallback=not args.no_browser`. In `analyze`'s docstring, step 5 becomes "PDF download via Unpaywall/PMC/publisher". In `_discover_fulltext`'s docstring, drop "and optionally browser fallback". In `_fulltext_discoverer`'s docstring, "browser settings" becomes nothing.
- `transparency/assessment.py`: drop `use_browser_fallback=False,` and the docstring sentence "Browser fallback is disabled, so no worker thread blocks on Playwright; …". Replace it with "No worker thread waits on a browser: a walled PDF is queued for the GUI's embedded browser instead (#480)."
- `mcp_server.py`: drop `use_browser_fallback=False,`.
- `__init__.py`: drop `close_browser_session` from the import and from `__all__`.
- `study_transparency_analyzer/README.md` and `DOCUMENTATION.md`: delete the Playwright/browser-fallback passages and the `--no-browser` flag. Where they list the full-text chain, end it with: "A walled open-access PDF is queued for the desktop's embedded browser (see `doc/developer/browser_rescue.md`)."

- [ ] **Step 3: Remove the argument from the tests**

Run from the repository root:

```bash
python - <<'PY'
import pathlib, re
for path in pathlib.Path("tests").glob("*.py"):
    text = path.read_text()
    new = re.sub(r",\s*use_browser_fallback=False", "", text)
    new = re.sub(r"use_browser_fallback=False,\s*", "", new)
    new = re.sub(r"use_browser_fallback=False", "", new)
    if new != text:
        path.write_text(new)
        print("rewrote", path)
PY
grep -rn "use_browser_fallback\|browser_headless\|close_browser_session\|BrowserSession" src tests && echo "LEFT OVER" || echo "clean"
```

Expected: a list of rewritten files, then `clean`. Read `git diff tests/` to check the rewrite left no `X(, …)` or a dangling comma on its own line. Then format any line the edit left over-long or oddly broken, by hand.

- [ ] **Step 4: Run the whole suite**

Run: `pytest tests/ -q -m "not integration"`
Expected: pass, including `tests/test_playwright_fallback_retired.py`.

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "refactor(fulltext): retire the Playwright fallback (#483)

The browser rescue queue (#480) replaces it: no discovery waits on a
browser, and no worker thread drives one."
```

---

### Task 7: QtWebEngine start-up, the setting, CI

**Files:**
- Create: `src/bmlibrarian_lite/gui/web_engine.py`, `.github/scripts/qt_preflight.py`
- Modify: `src/bmlibrarian_lite/gui/app.py` (`run_lite_app`), `tests/conftest.py` (top), `.github/workflows/python-tests.yml`
- Modify: `src/bmlibrarian_lite/config.py` (`DiscoveryConfig`, `StorageConfig`, load ~line 825, `_to_dict_without_secrets` ~line 982)
- Modify: `src/bmlibrarian_lite/gui/settings_dialog.py` (Full Text tab ~line 815; `_load_config` ~line 1184; `_save_config` ~line 1304)
- Test: `tests/test_browser_rescue_settings.py` (new)

**Interfaces:**
- Produces: `prepare_web_engine() -> None`; `DiscoveryConfig.browser_rescue_hidden: bool = True`; `StorageConfig.browser_profile_dir: Path` (property); `SettingsDialog.browser_rescue_hidden_check: QCheckBox`.

- [ ] **Step 1: Write the failing tests**

```python
"""The browser rescue's setting and start-up (#480, stage 2)."""

import json
from pathlib import Path

import pytest

from bmlibrarian_lite.config import LiteConfig
from bmlibrarian_lite.constants import BROWSER_PROFILE_DIRNAME


def test_the_hidden_pass_is_on_by_default() -> None:
    assert LiteConfig().discovery.browser_rescue_hidden is True


def test_the_setting_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = LiteConfig()
    config.discovery.browser_rescue_hidden = False
    config.save(path)
    assert LiteConfig.load(path).discovery.browser_rescue_hidden is False


@pytest.mark.parametrize("stored", ["false", 0, None, [1]])
def test_a_setting_that_is_not_a_boolean_is_the_default(tmp_path: Path, stored: object) -> None:
    path = tmp_path / "config.json"
    LiteConfig().save(path)
    data = json.loads(path.read_text())
    data["discovery"]["browser_rescue_hidden"] = stored
    path.write_text(json.dumps(data))
    assert LiteConfig.load(path).discovery.browser_rescue_hidden is True


def test_the_profile_lives_under_the_data_directory(tmp_path: Path) -> None:
    config = LiteConfig()
    config.storage.data_dir = tmp_path
    assert config.storage.browser_profile_dir == tmp_path / BROWSER_PROFILE_DIRNAME


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_the_dialog_shows_and_saves_the_setting(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    from bmlibrarian_lite.gui.settings_dialog import SettingsDialog

    config = LiteConfig()
    monkeypatch.setattr(LiteConfig, "save", lambda self, *a, **k: None)
    dialog = SettingsDialog(config)
    assert dialog.browser_rescue_hidden_check.isChecked()
    dialog.browser_rescue_hidden_check.setChecked(False)
    dialog._save_config()
    assert config.discovery.browser_rescue_hidden is False
    config.discovery.browser_rescue_hidden = True
    assert SettingsDialog(config).browser_rescue_hidden_check.isChecked()


def test_the_web_engine_is_prepared_before_any_application() -> None:
    """conftest prepared it, so a page can be made in this suite's QApplication."""
    from PySide6.QtCore import QCoreApplication, Qt

    assert QCoreApplication.testAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)


def test_preparing_too_late_says_so(qapp) -> None:
    from bmlibrarian_lite.gui.web_engine import prepare_web_engine

    with pytest.raises(RuntimeError, match="before the QApplication"):
        prepare_web_engine()
```

Run: `pytest tests/test_browser_rescue_settings.py -q`. Expected: FAIL (`AttributeError`, `ModuleNotFoundError`).

- [ ] **Step 2: `gui/web_engine.py`**

```python
"""QtWebEngine's start-up requirement, in one place (#480, stage 2).

QtWebEngine must be imported, with OpenGL contexts shared, before the
``QApplication`` exists. Otherwise its first page fails, and Qt only warns.
The application, the test suite's preflight, the engine harness and CI's
preflight all call :func:`prepare_web_engine`.
"""

from PySide6.QtCore import QCoreApplication, Qt


def prepare_web_engine() -> None:
    """Make QtWebEngine usable by the ``QApplication`` about to be created.

    Raises:
        RuntimeError: If a ``QApplication`` already exists: it is too late.
    """
    if QCoreApplication.instance() is not None:
        raise RuntimeError("QtWebEngine must be prepared before the QApplication exists")
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    import PySide6.QtWebEngineCore  # noqa: F401  (registers the engine)
```

- [ ] **Step 3: Call it**

- `gui/app.py`, `run_lite_app`: the first statement becomes `prepare_web_engine()` (import `from .web_engine import prepare_web_engine`).
- `tests/conftest.py`: at the top, after the module docstring and before any other import that could create a `QApplication`:

```python
# QtWebEngine must be prepared before any QApplication exists (#480, stage
# 2): gui/app.py imports the browser rescue, and a test that builds the
# window would otherwise get a broken engine.
from bmlibrarian_lite.gui.web_engine import prepare_web_engine

prepare_web_engine()
```

- [ ] **Step 4: The setting**

`config.py`:
- `DiscoveryConfig`: add after `elsevier_insttoken`:

```python
    # Try bot-walled open-access PDFs in a hidden embedded browser (#480,
    # stage 2). Off: entries still collect for the review queue window.
    browser_rescue_hidden: bool = True
```

- In `load`, inside `if "discovery" in data:`, before `config.discovery = DiscoveryConfig(`:

```python
            hidden = discovery_data.get("browser_rescue_hidden", True)
```

  and pass `browser_rescue_hidden=hidden if isinstance(hidden, bool) else True,`. A stored value that is not a boolean is not trusted; the default applies (golden rule 1).
- In `_to_dict_without_secrets`'s `"discovery"` block, add `"browser_rescue_hidden": self.discovery.browser_rescue_hidden,`.
- `StorageConfig`: add after its other path properties, and add `BROWSER_PROFILE_DIRNAME` to the constants import:

```python
    @property
    def browser_profile_dir(self) -> Path:
        """The embedded browser's persistent profile (#480, stage 2)."""
        return self.data_dir / BROWSER_PROFILE_DIRNAME
```

`settings_dialog.py`, Full Text tab, before `self.tab_widget.addTab(tab, "Full Text")`:

```python
        self.browser_rescue_hidden_check = QCheckBox(
            "Try bot-walled PDFs in a hidden browser"
        )
        self.browser_rescue_hidden_check.setToolTip(BROWSER_RESCUE_EXPLANATION)
        layout.addRow(self.browser_rescue_hidden_check)
        browser_rescue_note = QLabel(f"<small>{BROWSER_RESCUE_EXPLANATION}</small>")
        browser_rescue_note.setWordWrap(True)
        layout.addRow(browser_rescue_note)
```

Define the text beside the other explanations (find `ELSEVIER_INSTTOKEN_EXPLANATION` with `grep -rn "ELSEVIER_INSTTOKEN_EXPLANATION =" src/` and put it in the same module):

```python
BROWSER_RESCUE_EXPLANATION = (
    "Some open-access PDFs sit behind a robot check that blocks the app's "
    "downloads. With this on, the app later opens each such PDF in a hidden "
    "built-in browser, saves it if it arrives, and re-checks that article's "
    "transparency. Nothing is sent but the page request itself."
)
```

`_load_config`: `self.browser_rescue_hidden_check.setChecked(self.config.discovery.browser_rescue_hidden)`.
`_save_config`, under "Save Full Text keys": `self.config.discovery.browser_rescue_hidden = self.browser_rescue_hidden_check.isChecked()`.

- [ ] **Step 5: CI**

Create `.github/scripts/qt_preflight.py`:

```python
"""Fail loudly when the GUI stack, QtWebEngine included, cannot start.

The widget suites skip without PySide6, and the engine harness would fail
with a less direct message, so CI checks the stack first: a widget, then a
QtWebEngine page that must load ``about:blank`` (#480, stage 2).
"""

import sys

from bmlibrarian_lite.gui.web_engine import prepare_web_engine

prepare_web_engine()

from PySide6.QtCore import QTimer, QUrl  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402
from shiboken6 import Shiboken  # noqa: E402

#: How long the blank page may take to load on a cold runner.
PREFLIGHT_TIMEOUT_MS = 60_000


def main() -> int:
    """Start the stack and load one page.

    Returns:
        0 when a page loaded; 1 otherwise.
    """
    app = QApplication([])
    QLabel("ci preflight")
    profile = QWebEngineProfile()
    page = QWebEnginePage(profile)
    loaded: list[bool] = []
    page.loadFinished.connect(lambda ok: (loaded.append(ok), app.quit()))
    QTimer.singleShot(PREFLIGHT_TIMEOUT_MS, app.quit)
    page.load(QUrl("about:blank"))
    app.exec()
    Shiboken.delete(page)  # before the profile, as QtWebEngine requires
    Shiboken.delete(profile)
    if loaded != [True]:
        print("QtWebEngine could not load a page", file=sys.stderr)
        return 1
    print("Qt offscreen platform and QtWebEngine are usable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

In `.github/workflows/python-tests.yml`:
- Under the top-level `env:`, add:

```yaml
  # Chromium's sandbox inside QtWebEngine needs unprivileged user namespaces,
  # which the Ubuntu 24.04 runner's AppArmor forbids. CI only: the
  # application keeps its sandbox (#480, stage 2).
  QTWEBENGINE_DISABLE_SANDBOX: "1"
```

- Extend the apt line with QtWebEngine's runtime libraries:

```yaml
            libegl1 libgl1 libdbus-1-3 libfontconfig1 \
            libxkbcommon0 libxkbcommon-x11-0 \
            libnss3 libxcomposite1 libxdamage1 libxrandr2 libxtst6 \
            libxkbfile1 libasound2t64 libgbm1 libxshmfence1 libxi6 libxcursor1
```

- Replace the "Verify the GUI stack really works" step's script with `run: python .github/scripts/qt_preflight.py`, keeping its comment and adding "QtWebEngine included". If the job later fails with a missing shared library, add the package that provides it (`apt-file search <lib>`), never a skip.

- [ ] **Step 6: Run the tests and the preflight**

Run:

```bash
pytest tests/test_browser_rescue_settings.py tests/test_core_config.py tests/test_elsevier_config.py -q
QT_QPA_PLATFORM=offscreen python .github/scripts/qt_preflight.py
pytest tests/ -q -m "not integration"
```

Expected: pass; the preflight prints "Qt offscreen platform and QtWebEngine are usable"; the whole suite stays green with the engine imported first.

- [ ] **Step 7: Commit**

```bash
git add src/bmlibrarian_lite/gui/web_engine.py src/bmlibrarian_lite/gui/app.py src/bmlibrarian_lite/config.py src/bmlibrarian_lite/gui/settings_dialog.py tests/conftest.py tests/test_browser_rescue_settings.py .github/scripts/qt_preflight.py .github/workflows/python-tests.yml
git commit -m "feat(gui): QtWebEngine start-up and the hidden-browser setting (#480)"
```

---

### Task 8: One hidden visit (`RescueAttempt`)

**Files:**
- Create: `src/bmlibrarian_lite/gui/rescue_attempt.py`
- Create: `tests/browser_rescue_harness.py` (not collected: no `test_` prefix)
- Test: `tests/test_browser_rescue_engine.py`

**Interfaces:**
- Consumes: `page_verdict`, `download_verdict`, `RescueVerdict`, `RescueOutcome` (Task 1); `partial_download_path`, `discard_partial_download`; `prepare_web_engine` (Task 7); `RESCUE_PAGE_READ_GRACE_MS`, `BROWSER_PROFILE_*`, `CONFIG_DIR_PERMISSIONS`.
- Produces:
  - `rescue_profile(profile_dir: Path) -> QWebEngineProfile` (parentless; the caller connects `downloadRequested` and deletes it)
  - `response_headers(raw) -> dict[str, str]`
  - `RescueAttempt(profile, address, pdf_path, timeout_ms, stall_ms, max_pdf_bytes, parent=None)` with `.start()`, `.accept_download(request)`, `.abort()`, signal `finished(RescueVerdict)`

Behaviour this pins (probe of QtWebEngine 6.10.1, 2026-10-13):
- A PDF with the viewer off arrives through `profile.downloadRequested`. The navigation then ends `LoadStoppedStatus` with Internal error −3 and `info.isDownload() == True`. That is not a failure.
- Every main-frame answer reports its HTTP status through `HttpStatusCodeDomain`, a 200 included. A 403 challenge reports `LoadFailedStatus`, which is not a network error either.
- A JavaScript cookie-and-reload challenge passes in a hidden page.

- [ ] **Step 1: Write the harness**

Create `tests/browser_rescue_harness.py`:

```python
"""Drives the browser rescue engine against a loopback server, in its own process.

Run by ``tests/test_browser_rescue_engine.py`` and never collected by pytest:
QtWebEngine needs its own ``QApplication``, and pumping the suite's shared
one inherits earlier tests' backlog (#516, #517). Prints one JSON object as
the last line of standard output.
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from bmlibrarian_lite.gui.web_engine import prepare_web_engine

prepare_web_engine()

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from shiboken6 import Shiboken  # noqa: E402

from bmlibrarian_lite.browser_rescue import RescueVerdict  # noqa: E402
from bmlibrarian_lite.gui.rescue_attempt import RescueAttempt, rescue_profile  # noqa: E402

TIMEOUT_MS = 3_000
STALL_MS = 1_500
MAX_PDF_BYTES = 1_024
SCENARIO_LIMIT_SECONDS = 30
STALL_HOLD_SECONDS = 8
PDF_BODY = b"%PDF-1.4\n" + b"0" * 500
OVERSIZE_BODY = b"%PDF-1.4\n" + b"0" * 4_096
CHALLENGE_PAGE = (
    b"<html><head><title>Just a moment...</title></head><body><script>"
    b"window._cf_chl_opt={};setTimeout(function(){document.cookie='cleared=1; path=/';"
    b"location.reload()},300)</script></body></html>"
)
WALL_PAGE = (
    b"<html><head><title>Just a moment...</title></head><body>"
    b"<script>window._cf_chl_opt={};</script></body></html>"
)


class Handler(BaseHTTPRequestHandler):
    """The loopback publisher: one route per scenario."""

    visits: list[str] = []

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Keep the harness's output to its JSON line."""

    def _send(self, status: int, content_type: str, body: bytes, headers: dict[str, str] | None = None,
              announce: bool = True) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if announce:
            self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        """Answer one route."""
        Handler.visits.append(self.path)
        route = self.path.split("?")[0]
        if route == "/pdf":
            self._send(200, "application/pdf", PDF_BODY)
        elif route == "/challenge":
            if "cleared=1" in (self.headers.get("Cookie") or ""):
                self.send_response(302)
                self.send_header("Location", "/pdf")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._send(403, "text/html", CHALLENGE_PAGE, {"Server": "cloudflare"})
        elif route == "/walled":
            self._send(403, "text/html", WALL_PAGE, {"Server": "cloudflare"})
        elif route == "/gone":
            self._send(404, "text/html", b"<html><title>Not found</title>gone</html>")
        elif route == "/abstract":
            self._send(200, "text/html", b"<html><title>Abstract</title><p>An abstract.</p></html>")
        elif route == "/fakepdf":
            self._send(200, "application/pdf", b"<html>not a pdf</html>")
        elif route == "/oversize":
            self._send(200, "application/pdf", OVERSIZE_BODY)
        elif route == "/oversize-unannounced":
            self._send(200, "application/pdf", OVERSIZE_BODY, announce=False)
        elif route == "/stall":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(MAX_PDF_BYTES - 1))
            self.end_headers()
            self.wfile.write(b"%PDF-1.4\n")
            self.wfile.flush()
            time.sleep(STALL_HOLD_SECONDS)
        else:
            self._send(404, "text/html", b"no such route")


def run_until(app: QApplication, done: Callable[[], bool], limit_seconds: float) -> bool:
    """Pump the event loop until ``done`` or the limit."""
    deadline = time.monotonic() + limit_seconds
    while not done():
        if time.monotonic() > deadline:
            return False
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)
    return True


def closed_port() -> int:
    """A loopback port nothing listens on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


ATTEMPTS = {
    "pdf": "/pdf",
    "challenge": "/challenge",
    "walled": "/walled",
    "gone": "/gone",
    "abstract": "/abstract",
    "fakepdf": "/fakepdf",
    "oversize": "/oversize",
    "oversize_unannounced": "/oversize-unannounced",
    "stall": "/stall",
}


def attempt_scenarios(app: QApplication, base: str, root: Path) -> dict[str, Any]:
    """Run each visit once, in one profile, and report what came of it."""
    profile = rescue_profile(root / "attempt-profile")
    current: list[RescueAttempt] = []
    profile.downloadRequested.connect(lambda request: current[0].accept_download(request))
    results: dict[str, Any] = {}
    addresses = {name: base + path for name, path in ATTEMPTS.items()}
    addresses["refused"] = f"http://127.0.0.1:{closed_port()}/x.pdf"
    for name, address in addresses.items():
        out = root / "attempts" / name
        out.mkdir(parents=True)
        pdf_path = out / "article.pdf"
        verdicts: list[RescueVerdict] = []
        attempt = RescueAttempt(profile, address, pdf_path, TIMEOUT_MS, STALL_MS, MAX_PDF_BYTES)
        attempt.finished.connect(verdicts.append)
        current[:] = [attempt]
        started = time.monotonic()
        attempt.start()
        finished = run_until(app, lambda: bool(verdicts), SCENARIO_LIMIT_SECONDS)
        results[name] = {
            "finished": finished,
            "verdict": verdicts[0].to_dict() if verdicts else None,
            "saved": pdf_path.exists() and pdf_path.read_bytes().startswith(b"%PDF"),
            "left": sorted(p.name for p in out.iterdir() if p.name != "article.pdf"),
            "seconds": round(time.monotonic() - started, 2),
        }
        attempt.deleteLater()
    results["visits"] = list(Handler.visits)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    Shiboken.delete(profile)
    return results


def main() -> int:
    """Run the scenarios and print the results."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    app = QApplication(sys.argv)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        output = {"attempts": attempt_scenarios(app, base, root)}
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_browser_rescue_engine.py`:

```python
"""The browser rescue engine against a loopback publisher (#480, stage 2).

Everything that pumps QtWebEngine runs in ``browser_rescue_harness.py``, in
its own process (#516, #517). A QtWebEngine that cannot start fails these
tests; it never skips them.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("browser_rescue_harness.py")
HARNESS_TIMEOUT_SECONDS = 300
TIMEOUT_SECONDS = 3


@pytest.fixture(scope="module")
def harness() -> dict[str, Any]:
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.run(
        [sys.executable, str(HARNESS)],
        capture_output=True, text=True, timeout=HARNESS_TIMEOUT_SECONDS, env=env, check=False,
    )
    assert proc.returncode == 0, f"harness failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    return {"out": json.loads(proc.stdout.strip().splitlines()[-1]), "stderr": proc.stderr}


def _attempt(harness: dict[str, Any], name: str) -> dict[str, Any]:
    result = harness["out"]["attempts"][name]
    assert result["finished"], f"{name} never finished"
    return result


def test_a_direct_pdf_is_saved_whole(harness: dict[str, Any]) -> None:
    result = _attempt(harness, "pdf")
    assert result["verdict"]["outcome"] == "pdf"
    assert result["saved"] and result["left"] == []


def test_a_javascript_challenge_is_passed_to_the_pdf(harness: dict[str, Any]) -> None:
    result = _attempt(harness, "challenge")
    assert result["verdict"]["outcome"] == "pdf"
    assert result["saved"]
    visits = harness["out"]["attempts"]["visits"]
    assert visits.count("/challenge") == 2


def test_a_wall_that_stays_needs_a_person(harness: dict[str, Any]) -> None:
    result = _attempt(harness, "walled")
    assert result["verdict"] == {"outcome": "challenge", "http_status": 403, "markers": ["cloudflare"]}
    assert not result["saved"]


def test_a_404_is_gone_at_once(harness: dict[str, Any]) -> None:
    result = _attempt(harness, "gone")
    assert result["verdict"] == {"outcome": "gone", "http_status": 404, "markers": []}
    assert result["seconds"] < TIMEOUT_SECONDS


def test_an_abstract_page_has_no_pdf(harness: dict[str, Any]) -> None:
    assert _attempt(harness, "abstract")["verdict"] == {"outcome": "no_pdf", "http_status": 200, "markers": []}


def test_a_pdf_that_is_not_one_is_discarded(harness: dict[str, Any]) -> None:
    result = _attempt(harness, "fakepdf")
    assert result["verdict"]["outcome"] == "no_pdf"
    assert not result["saved"] and result["left"] == []


@pytest.mark.parametrize("name", ["oversize", "oversize_unannounced"])
def test_an_oversize_pdf_is_refused_and_nothing_kept(harness: dict[str, Any], name: str) -> None:
    result = _attempt(harness, name)
    assert result["verdict"]["outcome"] == "too_large"
    assert not result["saved"] and result["left"] == []


def test_a_stalled_download_is_cancelled_as_a_transport_failure(harness: dict[str, Any]) -> None:
    result = _attempt(harness, "stall")
    assert result["verdict"]["outcome"] == "transport"
    assert not result["saved"] and result["left"] == []


def test_a_refused_connection_is_a_transport_failure(harness: dict[str, Any]) -> None:
    assert _attempt(harness, "refused")["verdict"]["outcome"] == "transport"


def test_response_headers_join_a_multimap() -> None:
    from PySide6.QtCore import QByteArray

    from bmlibrarian_lite.gui.rescue_attempt import response_headers

    raw = {QByteArray(b"Server"): [QByteArray(b"cloudflare")], QByteArray(b"Vary"): [QByteArray(b"a"), QByteArray(b"b")]}
    assert response_headers(raw) == {"Server": "cloudflare", "Vary": "a, b"}
```

Run: `pytest tests/test_browser_rescue_engine.py -q`
Expected: FAIL; the harness exits non-zero with `No module named 'bmlibrarian_lite.gui.rescue_attempt'`.

- [ ] **Step 3: Write `gui/rescue_attempt.py`**

Add `RESCUE_PAGE_READ_GRACE_MS` (Task 1 defined it) and `CONFIG_DIR_PERMISSIONS` to the constants import.

```python
"""One visit of one walled PDF's address, in a web page with no view (#480, stage 2).

Driven only by signals and timers, so nothing here blocks the GUI thread.
The page is QtWebEngine's own, with its own User-Agent and no automation
protocol (no impersonation, spec decision 5). The profile's PDF viewer is
off, so a PDF arrives as a download. It is written beside its cache path
as a partial file, and renamed into place only once it is whole, within
the size limit, and begins with ``%PDF``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEngineLoadingInfo,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from shiboken6 import Shiboken

from ..browser_rescue import RescueOutcome, RescueVerdict, download_verdict, page_verdict
from ..constants import (
    BROWSER_PROFILE_CACHE_DIRNAME,
    BROWSER_PROFILE_STORAGE_NAME,
    CHALLENGE_GONE_STATUSES,
    CONFIG_DIR_PERMISSIONS,
    PDF_MAGIC_BYTES,
    RESCUE_PAGE_READ_GRACE_MS,
)
from ..pdf_download import discard_partial_download, partial_download_path

logger = logging.getLogger(__name__)

_Domain = QWebEngineLoadingInfo.ErrorDomain
_LoadStatus = QWebEngineLoadingInfo.LoadStatus
_DownloadState = QWebEngineDownloadRequest.DownloadState

#: Load failures that are the network's: the visit learnt nothing.
_TRANSPORT_DOMAINS = frozenset(
    {
        _Domain.ConnectionErrorDomain,
        _Domain.DnsErrorDomain,
        _Domain.CertificateErrorDomain,
        _Domain.HttpErrorDomain,
        _Domain.FtpErrorDomain,
    }
)

#: How header bytes become text for the challenge rules; no byte is lost.
_HEADER_ENCODING = "latin-1"


def rescue_profile(profile_dir: Path) -> QWebEngineProfile:
    """The browser's profile, configured as the spike that passed the walls.

    Persistent and owner-only, because its cookies are the reader's
    clearances and sessions with publishers. QtWebEngine's own User-Agent;
    the PDF viewer off, so a PDF arrives through ``downloadRequested``.
    Parentless: the caller connects ``downloadRequested`` and deletes the
    profile after every page made with it.

    Args:
        profile_dir: Where the profile lives.

    Returns:
        The profile.

    Raises:
        OSError: If the directory cannot be made private.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(profile_dir, CONFIG_DIR_PERMISSIONS)
    profile = QWebEngineProfile(BROWSER_PROFILE_STORAGE_NAME)
    profile.setPersistentStoragePath(str(profile_dir))
    profile.setCachePath(str(profile_dir / BROWSER_PROFILE_CACHE_DIRNAME))
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    )
    profile.settings().setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)
    return profile


def response_headers(raw: Mapping[Any, Sequence[Any]]) -> dict[str, str]:
    """A loading info's response headers, as the challenge rules take them.

    Args:
        raw: QtWebEngine's multimap, ``{QByteArray: [QByteArray, ...]}``.

    Returns:
        Each name with its values joined as HTTP joins a repeated header.
    """
    return {
        bytes(name).decode(_HEADER_ENCODING): ", ".join(
            bytes(value).decode(_HEADER_ENCODING) for value in values
        )
        for name, values in raw.items()
    }


class RescueAttempt(QObject):
    """One hidden visit of one address, ending in exactly one verdict.

    Signals:
        finished: The visit's :class:`RescueVerdict`, emitted once, and never
            after :meth:`abort`.
    """

    finished = Signal(object)

    def __init__(
        self,
        profile: QWebEngineProfile,
        address: str,
        pdf_path: Path,
        timeout_ms: int,
        stall_ms: int,
        max_pdf_bytes: int,
        parent: QObject | None = None,
    ) -> None:
        """Prepare the visit; :meth:`start` makes it.

        Args:
            profile: The rescue profile (:func:`rescue_profile`).
            address: The PDF's address.
            pdf_path: Where a PDF that arrives is saved.
            timeout_ms: How long to wait for a download before reading the page.
            stall_ms: How long a started download may receive nothing.
            max_pdf_bytes: The largest PDF kept.
            parent: The owning object.
        """
        super().__init__(parent)
        self._profile = profile
        self._address = address
        self._pdf_path = pdf_path
        self._timeout_ms = timeout_ms
        self._max_pdf_bytes = max_pdf_bytes
        self._page: QWebEnginePage | None = None
        self._download: QWebEngineDownloadRequest | None = None
        self._status: int | None = None
        self._headers: dict[str, str] = {}
        self._too_large = False
        self._reading_page = False
        self._done = False
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)
        self._stall = QTimer(self)
        self._stall.setSingleShot(True)
        self._stall.setInterval(stall_ms)
        self._stall.timeout.connect(self._on_stall)

    def start(self) -> None:
        """Load the address in a page with no view, and start waiting."""
        # Parentless, so it is deleted when this visit says, always before
        # the profile, as QtWebEngine requires (_release_page)
        page = QWebEnginePage(self._profile)
        page.loadingChanged.connect(self._on_loading_changed)
        page.renderProcessTerminated.connect(self._on_render_process_terminated)
        self._page = page
        self._timeout.start(self._timeout_ms)
        page.load(QUrl(self._address))

    def accept_download(self, request: QWebEngineDownloadRequest) -> None:
        """Take the download this visit started, into the partial file.

        Only the first: a page that starts a second is not believed twice.
        A size announced over the limit is refused before a byte is written.

        Args:
            request: The profile's download request.
        """
        if self._done or self._download is not None:
            request.cancel()
            return
        self._timeout.stop()
        self._download = request
        if request.totalBytes() > self._max_pdf_bytes:
            request.cancel()
            self._finish(RescueVerdict(RescueOutcome.TOO_LARGE))
            return
        partial = partial_download_path(self._pdf_path)
        try:
            partial.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error("No folder for the browser's download of %s: %s", self._pdf_path.name, e)
            request.cancel()
            self._finish(RescueVerdict(RescueOutcome.TRANSPORT))
            return
        discard_partial_download(partial)
        request.setDownloadDirectory(str(partial.parent))
        request.setDownloadFileName(partial.name)
        request.receivedBytesChanged.connect(self._on_progress)
        request.isFinishedChanged.connect(self._on_download_finished)
        request.accept()
        self._stall.start()

    def abort(self) -> None:
        """End the visit with no verdict, for quit: its entry stays as it was."""
        if self._done:
            return
        self._done = True
        self._timeout.stop()
        self._stall.stop()
        if self._download is not None and not self._download.isFinished():
            self._download.cancel()
            discard_partial_download(partial_download_path(self._pdf_path))
        self._release_page(at_once=True)

    def _on_loading_changed(self, info: QWebEngineLoadingInfo) -> None:
        """Note each main-frame answer; end the visit on a gone or a network failure.

        Args:
            info: The load's state.
        """
        if self._done or info.isDownload() or info.status() == _LoadStatus.LoadStartedStatus:
            return
        domain = info.errorDomain()
        if domain == _Domain.HttpStatusCodeDomain:
            # Every main-frame answer reports its status here, a 200 included
            self._status = info.errorCode()
            self._headers = response_headers(info.responseHeaders())
            if self._status in CHALLENGE_GONE_STATUSES:
                self._finish(page_verdict(self._status, self._headers, ""))
            return
        if info.status() == _LoadStatus.LoadFailedStatus and domain in _TRANSPORT_DOMAINS:
            self._finish(RescueVerdict(RescueOutcome.TRANSPORT, self._status))

    def _on_render_process_terminated(self, *_args: object) -> None:
        """The page's renderer died: nothing was learnt."""
        self._finish(RescueVerdict(RescueOutcome.TRANSPORT, self._status))

    def _on_timeout(self) -> None:
        """No download began in time: read the page the visit stayed on."""
        if self._done or self._download is not None or self._page is None:
            return
        if self._reading_page:
            # The page never handed its HTML over: nothing was learnt
            self._finish(RescueVerdict(RescueOutcome.TRANSPORT, self._status))
            return
        self._reading_page = True
        self._timeout.start(RESCUE_PAGE_READ_GRACE_MS)
        self._page.toHtml(self._on_html)

    def _on_html(self, html: str) -> None:
        """Judge the page by its serialised DOM.

        Args:
            html: The page's HTML.
        """
        if not self._done and self._download is None:
            self._finish(page_verdict(self._status, self._headers, html))

    def _on_progress(self) -> None:
        """A download moved: refuse it past the limit, else wait on."""
        if self._done or self._download is None:
            return
        if self._download.receivedBytes() > self._max_pdf_bytes:
            self._too_large = True
            self._download.cancel()
            return
        self._stall.start()

    def _on_stall(self) -> None:
        """A download received nothing for too long: give it up."""
        if not self._done and self._download is not None:
            self._download.cancel()

    def _on_download_finished(self) -> None:
        """Keep a whole PDF; anything else ends the visit without one."""
        request = self._download
        if self._done or request is None or not request.isFinished():
            return
        written = Path(request.downloadDirectory()) / request.downloadFileName()
        if request.state() != _DownloadState.DownloadCompleted:
            discard_partial_download(written)
            outcome = RescueOutcome.TOO_LARGE if self._too_large else RescueOutcome.TRANSPORT
            self._finish(RescueVerdict(outcome))
            return
        self._finish(self._settle(written))

    def _settle(self, written: Path) -> RescueVerdict:
        """Rename a finished download into place if it is a PDF within the limit.

        Args:
            written: Where QtWebEngine wrote it.

        Returns:
            The verdict; the partial file is gone either way.
        """
        try:
            if written.stat().st_size > self._max_pdf_bytes:
                return RescueVerdict(RescueOutcome.TOO_LARGE)
            with open(written, "rb") as f:
                verdict = download_verdict(f.read(len(PDF_MAGIC_BYTES)))
            if verdict.outcome is RescueOutcome.PDF:
                written.replace(self._pdf_path)
            return verdict
        except OSError as e:
            logger.error("The browser's download of %s could not be kept: %s", self._pdf_path.name, e)
            return RescueVerdict(RescueOutcome.TRANSPORT)
        finally:
            discard_partial_download(written)

    def _finish(self, verdict: RescueVerdict) -> None:
        """End the visit with its one verdict.

        Args:
            verdict: What it concluded.
        """
        if self._done:
            return
        self._done = True
        self._timeout.stop()
        self._stall.stop()
        self._release_page(at_once=False)
        self.finished.emit(verdict)

    def _release_page(self, at_once: bool) -> None:
        """Let the page go: later when inside its own signal, at once at quit.

        Args:
            at_once: Delete it now (quit), rather than from the event loop.
        """
        page, self._page = self._page, None
        if page is None:
            return
        page.loadingChanged.disconnect(self._on_loading_changed)
        page.renderProcessTerminated.disconnect(self._on_render_process_terminated)
        if at_once:
            Shiboken.delete(page)
        else:
            page.deleteLater()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_browser_rescue_engine.py -q`
Expected: pass. If a scenario fails, run the harness directly (`QT_QPA_PLATFORM=offscreen python tests/browser_rescue_harness.py`) and read its JSON and stderr. Fix the engine, never the expectation, unless the probe facts above are wrong for this Qt (then record that in the module docstring).

Then: `pytest tests/ -q -m "not integration"`. Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/bmlibrarian_lite/gui/rescue_attempt.py tests/browser_rescue_harness.py tests/test_browser_rescue_engine.py
git commit -m "feat(gui): one hidden browser visit of a walled PDF (#480)"
```

---

### Task 9: The hidden pass (`BrowserRescueService`)

**Files:**
- Create: `src/bmlibrarian_lite/gui/browser_rescue_service.py`
- Modify: `tests/browser_rescue_harness.py` (service scenarios), `tests/test_browser_rescue_engine.py`

**Interfaces:**
- Consumes: `RescueAttempt`, `rescue_profile` (Task 8); `LiteStorage.next_rescue_entry`, `record_rescue_verdict` (Task 2); `rescue_delay_ms`, `rescue_pdf_path` (Tasks 1, 3); `config.discovery.browser_rescue_hidden` (Task 7).
- Produces: `BrowserRescueService(storage, config, profile_dir, *, pdf_path_for=None, timeout_ms=RESCUE_HIDDEN_TIMEOUT_MS, stall_ms=RESCUE_DOWNLOAD_STALL_MS, poll_ms=RESCUE_POLL_INTERVAL_MS, max_pdf_bytes=MAX_PDF_SIZE, parent=None)` with `.start()`, `.wake(*_args)`, `.shutdown()`, property `.busy`, signal `rescued(RescueEntry)`.

- [ ] **Step 1: Add the service scenarios to the harness**

In `tests/browser_rescue_harness.py`, add imports:

```python
from urllib.parse import urlparse  # noqa: E402

from bmlibrarian_lite.browser_rescue import RescueRequest, RescueState  # noqa: E402
from bmlibrarian_lite.config import LiteConfig  # noqa: E402
from bmlibrarian_lite.constants import RESCUE_PRIORITY_WAITING, SERVICE_UNPAYWALL_PDF  # noqa: E402
from bmlibrarian_lite.gui.browser_rescue_service import BrowserRescueService  # noqa: E402
from bmlibrarian_lite.storage import LiteStorage  # noqa: E402
```

and `POLL_MS = 500` beside the other constants. Then add:

```python
def pump(app: QApplication, seconds: float) -> None:
    """Run the event loop for a while."""
    run_until(app, lambda: False, seconds)


def service_scenarios(app: QApplication, base: str, root: Path) -> dict[str, Any]:
    """Work a queue through the real service, then quit mid-visit."""
    config = LiteConfig()
    config.storage.data_dir = root / "data"
    storage = LiteStorage(config)
    out = root / "service-pdfs"

    def request(doc: str, path: str) -> RescueRequest:
        return RescueRequest(doc, f"10.1/{doc}", None, f"Title {doc}", base + path, "127.0.0.1",
                             SERVICE_UNPAYWALL_PDF)

    def states() -> dict[str, str]:
        rows = storage.rescue_entries(list(RescueState))
        return {f"{urlparse(e.request.address).path}?{urlparse(e.request.address).query}": e.state.value
                for e in rows}

    def entry_of(tag: str) -> Any:
        return next(e for e in storage.rescue_entries(list(RescueState)) if e.request.address.endswith(tag))

    storage.enqueue_rescues([request("doc-a", "/pdf?a"), request("doc-a", "/walled?a")])
    storage.enqueue_rescues([request("doc-b", "/abstract?b")])
    storage.enqueue_rescues([request("doc-c", "/gone?c")], priority=RESCUE_PRIORITY_WAITING)
    service = BrowserRescueService(
        storage, config, root / "service-profile",
        pdf_path_for=lambda e: out / f"{e.request.document_id}.pdf",
        timeout_ms=TIMEOUT_MS, stall_ms=STALL_MS, poll_ms=POLL_MS, max_pdf_bytes=MAX_PDF_BYTES,
    )
    rescued: list[str] = []
    service.rescued.connect(lambda e: rescued.append(e.request.document_id))
    Handler.visits.clear()
    service.start()
    drained = run_until(
        app, lambda: storage.next_rescue_entry() is None and not service.busy, SCENARIO_LIMIT_SECONDS
    )
    result: dict[str, Any] = {
        "drained": drained,
        "visits": list(Handler.visits),
        "states": states(),
        "rescued": list(rescued),
        "doc_a_saved": (out / "doc-a.pdf").exists(),
    }

    # Written by another process: the poll finds it
    LiteStorage(config).enqueue_rescues([request("doc-d", "/pdf?d")])
    result["polled"] = run_until(app, lambda: "doc-d" in rescued, SCENARIO_LIMIT_SECONDS)

    # The hidden pass switched off: entries collect, unvisited
    config.discovery.browser_rescue_hidden = False
    storage.enqueue_rescues([request("doc-e", "/pdf?e")])
    pump(app, 3 * POLL_MS / 1000)
    result["off"] = {"visited": any(v.endswith("?e") for v in Handler.visits),
                     "state": entry_of("?e").state.value}
    config.discovery.browser_rescue_hidden = True
    result["back_on"] = run_until(app, lambda: "doc-e" in rescued, SCENARIO_LIMIT_SECONDS)

    # Quit mid-visit: the entry stays pending, its attempts unchanged
    storage.enqueue_rescues([request("doc-f", "/abstract?f")])
    result["visiting"] = run_until(app, lambda: "/abstract?f" in Handler.visits, SCENARIO_LIMIT_SECONDS)
    service.shutdown()
    pump(app, 0.5)
    quit_entry = entry_of("?f")
    result["quit"] = {"state": quit_entry.state.value, "attempts": quit_entry.attempts}
    return result
```

Change `main()` to run both:

```python
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        output = {
            "attempts": attempt_scenarios(app, base, root),
            "service": service_scenarios(app, base, root),
        }
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_browser_rescue_engine.py`:

```python
PROFILE_RELEASE_WARNING = "Release of profile requested but WebEnginePage still not deleted"


def _service(harness: dict[str, Any]) -> dict[str, Any]:
    result = harness["out"]["service"]
    assert result["drained"], "the queue was never drained"
    return result


def test_the_waiting_entry_goes_first_then_the_oldest(harness: dict[str, Any]) -> None:
    visits = _service(harness)["visits"]
    assert visits[:3] == ["/gone?c", "/pdf?a", "/abstract?b"]


def test_a_rescue_supersedes_its_documents_other_address(harness: dict[str, Any]) -> None:
    service = _service(harness)
    assert service["states"]["/pdf?a"] == "rescued"
    assert service["states"]["/walled?a"] == "superseded"
    assert "/walled?a" not in service["visits"]
    assert service["doc_a_saved"]


def test_each_entry_settles_by_its_verdict(harness: dict[str, Any]) -> None:
    states = _service(harness)["states"]
    assert (states["/abstract?b"], states["/gone?c"]) == ("no_pdf", "gone")


def test_a_rescue_is_announced_once(harness: dict[str, Any]) -> None:
    assert _service(harness)["rescued"][:1] == ["doc-a"]
    assert _service(harness)["rescued"].count("doc-a") == 1


def test_an_entry_another_process_wrote_is_found_by_the_poll(harness: dict[str, Any]) -> None:
    assert _service(harness)["polled"]


def test_with_the_hidden_pass_off_entries_collect_unvisited(harness: dict[str, Any]) -> None:
    service = _service(harness)
    assert service["off"] == {"visited": False, "state": "pending"}
    assert service["back_on"]


def test_a_quit_mid_visit_leaves_the_entry_pending(harness: dict[str, Any]) -> None:
    service = _service(harness)
    assert service["visiting"]
    assert service["quit"] == {"state": "pending", "attempts": 0}


def test_quit_releases_the_page_before_the_profile(harness: dict[str, Any]) -> None:
    assert PROFILE_RELEASE_WARNING not in harness["stderr"]
```

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_browser_rescue_engine.py -q`
Expected: every test fails, because the harness exits non-zero (`No module named 'bmlibrarian_lite.gui.browser_rescue_service'`).

- [ ] **Step 3: Write `gui/browser_rescue_service.py`**

```python
"""The browser rescue's hidden pass: works the queue on the GUI thread (#480, stage 2).

One visit at a time, unattended, in a page with no view. Driven by signals
and timers only: no thread waits on it, and a run never waits for it (spec
decisions 1 and 4). Each visit takes its host's pacing slot first, from the
limiter our requests use. A rescue is announced with ``rescued`` and the
main window re-analyses that document.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, Signal
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEngineProfile
from shiboken6 import Shiboken

from ..browser_rescue import (
    RescueEntry,
    RescueState,
    RescueVerdict,
    rescue_delay_ms,
    rescue_pdf_path,
)
from ..constants import (
    MAX_PDF_SIZE,
    RESCUE_DOWNLOAD_STALL_MS,
    RESCUE_HIDDEN_TIMEOUT_MS,
    RESCUE_POLL_INTERVAL_MS,
)
from ..exceptions import SQLiteError
from .rescue_attempt import RescueAttempt, rescue_profile

if TYPE_CHECKING:
    from ..config import LiteConfig
    from ..storage import LiteStorage

logger = logging.getLogger(__name__)


def _default_pdf_path(entry: RescueEntry) -> Path:
    """Where a rescued PDF goes in the reader's library.

    Args:
        entry: The entry visited.

    Returns:
        The path the next discovery's cached-PDF tier looks at first.
    """
    return rescue_pdf_path(entry.request.doi)


class BrowserRescueService(QObject):
    """Visits queued walled PDFs one at a time, hidden and unattended.

    Signals:
        rescued: A :class:`RescueEntry` whose PDF arrived and was saved.
    """

    rescued = Signal(object)

    def __init__(
        self,
        storage: LiteStorage,
        config: LiteConfig,
        profile_dir: Path,
        *,
        pdf_path_for: Callable[[RescueEntry], Path] | None = None,
        timeout_ms: int = RESCUE_HIDDEN_TIMEOUT_MS,
        stall_ms: int = RESCUE_DOWNLOAD_STALL_MS,
        poll_ms: int = RESCUE_POLL_INTERVAL_MS,
        max_pdf_bytes: int = MAX_PDF_SIZE,
        parent: QObject | None = None,
    ) -> None:
        """Prepare the pass; :meth:`start` begins it.

        Args:
            storage: The queue.
            config: The live configuration; its hidden-pass setting is read
                before every visit.
            profile_dir: The browser's persistent profile.
            pdf_path_for: Where an entry's PDF is saved; the reader's library
                by default.
            timeout_ms: How long a visit waits for a download.
            stall_ms: How long a download may receive nothing.
            poll_ms: How often the queue is read for entries written
                elsewhere.
            max_pdf_bytes: The largest PDF kept.
            parent: The owning object.
        """
        super().__init__(parent)
        self._storage = storage
        self._config = config
        self._profile_dir = profile_dir
        self._pdf_path_for = pdf_path_for or _default_pdf_path
        self._timeout_ms = timeout_ms
        self._stall_ms = stall_ms
        self._max_pdf_bytes = max_pdf_bytes
        self._profile: QWebEngineProfile | None = None
        self._attempt: RescueAttempt | None = None
        self._busy = False
        self._started = False
        self._stopped = False
        self._poll = QTimer(self)
        self._poll.setInterval(poll_ms)
        self._poll.timeout.connect(self.wake)

    @property
    def busy(self) -> bool:
        """Whether a visit is scheduled or under way.

        Returns:
            ``True`` from taking an entry until its verdict is recorded.
        """
        return self._busy

    def start(self) -> None:
        """Begin: look at the queue now, then every poll interval."""
        if self._started or self._stopped:
            return
        self._started = True
        self._poll.start()
        self.wake()

    def wake(self, *_args: object) -> None:
        """Look for work soon, unless a visit is under way.

        Connected to signals with arguments (an analysis's outcome), which
        are ignored.

        Args:
            *_args: Ignored.
        """
        if not self._started or self._stopped or self._busy:
            return
        self._busy = True
        QTimer.singleShot(0, self, self._tick)

    def shutdown(self) -> None:
        """Stop, for quit.

        A visit under way ends with no verdict, so its entry stays pending.
        The page goes before the profile, as QtWebEngine requires: pages let
        go earlier are deleted first, then the profile.
        """
        if self._stopped:
            return
        self._stopped = True
        self._poll.stop()
        attempt, self._attempt = self._attempt, None
        if attempt is not None:
            attempt.abort()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        profile, self._profile = self._profile, None
        if profile is not None:
            Shiboken.delete(profile)

    def _tick(self) -> None:
        """Take the next due entry and schedule its visit for its host's slot."""
        if self._stopped:
            return
        entry = self._next_entry()
        if entry is None:
            self._busy = False
            return
        delay = rescue_delay_ms(entry.request.host)
        QTimer.singleShot(delay, self, lambda: self._visit(entry))

    def _next_entry(self) -> RescueEntry | None:
        """The next due entry, when the hidden pass is on.

        Returns:
            The entry; ``None`` when off, when none is due, or when the
            queue cannot be read (logged; the next poll tries again).
        """
        if not self._config.discovery.browser_rescue_hidden:
            return None
        try:
            return self._storage.next_rescue_entry()
        except (sqlite3.Error, SQLiteError) as e:
            logger.error("The browser rescue queue could not be read: %s", e)
            return None

    def _visit(self, entry: RescueEntry) -> None:
        """Start one visit.

        Args:
            entry: The entry to visit.
        """
        if self._stopped:
            return
        try:
            profile = self._ensure_profile()
            pdf_path = self._pdf_path_for(entry)
        except OSError as e:
            logger.error("The hidden browser cannot start a visit: %s", e)
            self._busy = False
            return
        attempt = RescueAttempt(
            profile, entry.request.address, pdf_path,
            self._timeout_ms, self._stall_ms, self._max_pdf_bytes, parent=self,
        )
        attempt.finished.connect(lambda verdict: self._on_finished(entry, verdict))
        self._attempt = attempt
        logger.info("Trying a walled PDF at %s in the hidden browser", entry.request.host)
        attempt.start()

    def _ensure_profile(self) -> QWebEngineProfile:
        """The profile, made on the first visit.

        Returns:
            The profile.

        Raises:
            OSError: If its directory cannot be made private.
        """
        if self._profile is None:
            profile = rescue_profile(self._profile_dir)
            profile.downloadRequested.connect(self._on_download_requested)
            self._profile = profile
        return self._profile

    def _on_download_requested(self, request: QWebEngineDownloadRequest) -> None:
        """Hand a download to the visit under way; refuse one with no visit.

        Args:
            request: The download.
        """
        if self._attempt is None:
            request.cancel()
            return
        self._attempt.accept_download(request)

    def _on_finished(self, entry: RescueEntry, verdict: RescueVerdict) -> None:
        """Record a visit's verdict, announce a rescue, and look for more.

        Args:
            entry: The entry visited.
            verdict: What the visit concluded.
        """
        attempt, self._attempt = self._attempt, None
        if attempt is not None:
            attempt.deleteLater()
        try:
            updated = self._storage.record_rescue_verdict(entry.id, verdict)
        except (sqlite3.Error, SQLiteError) as e:
            # The PDF, if any, is saved; the next discovery finds it anyway
            logger.error("The verdict for a walled PDF at %s could not be recorded: %s",
                         entry.request.host, e)
            updated = None
        logger.info("The hidden browser's visit to %s: %s", entry.request.host, verdict.outcome.value)
        self._busy = False
        if updated is not None and updated.state is RescueState.RESCUED:
            self.rescued.emit(updated)
        self.wake()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_browser_rescue_engine.py -q`, then `pytest tests/ -q -m "not integration"`.
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/bmlibrarian_lite/gui/browser_rescue_service.py tests/browser_rescue_harness.py tests/test_browser_rescue_engine.py
git commit -m "feat(gui): the hidden browser works the rescue queue (#480)"
```

---

### Task 10: A rescue re-analyses; the app runs the service

**Files:**
- Modify: `src/bmlibrarian_lite/browser_rescue.py` (`reanalyse_rescued`)
- Modify: `src/bmlibrarian_lite/transparency/transparency_manager.py` (`analyze_document`, new `reanalyse`)
- Modify: `src/bmlibrarian_lite/gui/systematic_review_tab.py` (`reanalyse_transparency`)
- Modify: `src/bmlibrarian_lite/gui/app.py` (`attach_rescue_service`, `_on_pdf_rescued`, `run_lite_app`)
- Test: `tests/test_transparency_manager.py`, `tests/test_browser_rescue.py`

**Interfaces:**
- Produces: `reanalyse_rescued(entry, stored_document, reanalyse) -> bool`; `TransparencyManager.reanalyse(document_id, pmid=None, doi=None) -> None`; `analyze_document(..., reuse_stored: bool = True)`; `SystematicReviewTab.reanalyse_transparency(document_id, pmid, doi) -> None`; `LiteMainWindow.attach_rescue_service(service) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_browser_rescue.py`:

```python
def _rescued_entry(document_id: str = "doc-1") -> RescueEntry:
    request = RescueRequest(document_id, DOI, "123", "A title", ADDRESS, "www.sciencedirect.com", SERVICE_UNPAYWALL_PDF)
    return RescueEntry(1, request, RescueState.RESCUED, 1, RescueVerdict(RescueOutcome.PDF), 0, "t", "t")


def test_a_rescued_document_is_reanalysed_by_its_doi() -> None:
    from bmlibrarian_lite.browser_rescue import reanalyse_rescued

    asked: list[tuple[str, str | None, str | None]] = []
    assert reanalyse_rescued(_rescued_entry(), lambda doc_id: object(), lambda *a: asked.append(a))
    assert asked == [("doc-1", "123", DOI)]


def test_a_rescue_of_a_document_storage_does_not_hold_is_not_reanalysed() -> None:
    """The MCP server queues under its own ID; analysing it would store a row for nothing."""
    from bmlibrarian_lite.browser_rescue import reanalyse_rescued

    asked: list[object] = []
    assert not reanalyse_rescued(_rescued_entry(f"doi-{DOI}"), lambda doc_id: None, lambda *a: asked.append(a))
    assert asked == []
```

Append to `tests/test_transparency_manager.py`, in the class holding `test_cached_result_returned`:

```python
    def test_a_rescue_reanalyses_past_this_builds_stored_result(self, manager, mock_storage):
        """A rescued PDF re-runs the analysis though a final row is stored (#480, stage 2)."""
        mock_storage.get_transparency_result.return_value = TransparencyResult(
            document_id="doc1", transparency_score=80, risk_level=TransparencyRisk.LOW
        )
        served = []
        manager.analysis_complete.connect(lambda _id, r: served.append(r))
        with patch.object(manager, "start"), patch.object(manager, "_executor") as executor:
            manager.reanalyse("doc1", pmid="12345678", doi="10.1/x")
        assert served == []
        assert executor.submit.called
        assert executor.submit.call_args.args[1:4] == ("doc1", "12345678", "10.1/x")

    def test_a_rescue_never_overwrites_a_newer_builds_row(self, manager, mock_storage):
        """Control: may_replace_stored still decides (#374)."""
        newer = TransparencyResult(
            document_id="doc1", transparency_score=80, risk_level=TransparencyRisk.LOW,
            analyzer_version="999.0",
        )
        assert not may_replace_stored(newer)
        mock_storage.get_transparency_result.return_value = newer
        served = []
        manager.analysis_complete.connect(lambda _id, r: served.append(r))
        with patch.object(manager, "start"), patch.object(manager, "_executor") as executor:
            manager.reanalyse("doc1", pmid="12345678")
        assert served == [newer]
        assert not executor.submit.called
```

Add `may_replace_stored` to that file's import from `bmlibrarian_lite.transparency.transparency_models`. If `TransparencyResult` takes the version under another field name, use the one `may_replace_stored` reads (`grep -n "def may_replace_stored" -A25 src/bmlibrarian_lite/transparency/transparency_models.py`).

Run: `pytest tests/test_browser_rescue.py tests/test_transparency_manager.py -q -k "rescue or reanalys"`
Expected: FAIL (`ImportError: reanalyse_rescued`, `AttributeError: reanalyse`).

- [ ] **Step 2: Implement `reanalyse_rescued`**

In `browser_rescue.py` (add `from collections.abc import Callable` to the imports):

```python
def reanalyse_rescued(
    entry: RescueEntry,
    stored_document: Callable[[str], object | None],
    reanalyse: Callable[[str, str | None, str | None], None],
) -> bool:
    """Re-analyse the document a rescued PDF belongs to, when storage holds it.

    By the entry's DOI, which names the saved PDF, so the analysis's
    cached-PDF tier finds it. The MCP server queues under its own document
    ID (``doi-…``), which storage need not hold: such a rescue is not
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
```

- [ ] **Step 3: Implement `reanalyse`**

In `transparency_manager.py`, add a keyword parameter to `analyze_document`:

```python
        reuse_stored: bool = True,
```

documented as:

```
            reuse_stored: Serve this build's stored final result instead of
                analysing. ``False`` for a document whose PDF was just
                rescued (#480, stage 2): its stored result was made without
                that full text. A newer build's row is never overwritten
                either way.
```

and change `if self.settings.cache_results:` to `if self.settings.cache_results and reuse_stored:`. Then add after `analyze_batch`:

```python
    def reanalyse(
        self,
        document_id: str,
        pmid: Optional[str] = None,
        doi: Optional[str] = None,
    ) -> None:
        """Analyse a document again now that its PDF is held (#480, stage 2).

        Ignores this build's stored result, still refuses to overwrite a
        newer build's (``may_replace_stored``), and queues the analysis as
        usual: discovery finds the PDF at the cached-PDF tier, and
        ``analysis_complete`` updates the badge.

        Args:
            document_id: Internal document ID.
            pmid: PubMed ID.
            doi: The DOI the rescued PDF is saved under.
        """
        self.analyze_document(document_id, pmid=pmid, doi=doi, reuse_stored=False)
```

- [ ] **Step 4: Wire the app**

`systematic_review_tab.py`, beside `_on_transparency_result`:

```python
    def reanalyse_transparency(
        self, document_id: str, pmid: str | None, doi: str | None
    ) -> None:
        """Re-run a document's transparency analysis now that its PDF is held.

        The outcome arrives through ``transparency_outcome_ready`` as any
        analysis's does (#480, stage 2).

        Args:
            document_id: Internal document ID.
            pmid: PubMed ID.
            doi: The DOI the rescued PDF is saved under.
        """
        self._transparency_manager.reanalyse(document_id, pmid=pmid, doi=doi)
```

`gui/app.py`: import `from ..browser_rescue import RescueEntry, reanalyse_rescued` and `from .browser_rescue_service import BrowserRescueService`. Add `QTimer` (already imported). In `LiteMainWindow.__init__`, set `self._rescue_service: BrowserRescueService | None = None` before `_setup_ui()`. Add the methods:

```python
    def attach_rescue_service(self, service: BrowserRescueService) -> None:
        """Run the browser rescue beside this window (#480, stage 2).

        A rescue re-analyses its document; an analysis's outcome wakes the
        service, since an analysis is where walled PDFs are queued.

        Args:
            service: The service, not yet started.
        """
        self._rescue_service = service
        service.rescued.connect(self._on_pdf_rescued)
        self.systematic_review_tab.transparency_outcome_ready.connect(service.wake)
        self.research_questions_tab.transparency_outcome_ready.connect(service.wake)

    def _on_pdf_rescued(self, entry: RescueEntry) -> None:
        """Re-analyse a document whose walled PDF the browser fetched, and say so.

        Args:
            entry: The rescued entry.
        """
        asked = reanalyse_rescued(
            entry,
            self.storage.get_document,
            self.systematic_review_tab.reanalyse_transparency,
        )
        title = entry.request.title or entry.request.doi
        self.status_bar.showMessage(
            f"Fetched the PDF of “{title}” in the hidden browser"
            + ("; re-checking its transparency." if asked else "."),
            STATUS_MESSAGE_TIMEOUT_MS,
        )
```

`run_lite_app` becomes:

```python
def run_lite_app() -> int:
    """
    Run the BMLibrarian Lite application.

    Returns:
        Application exit code
    """
    prepare_web_engine()
    app = QApplication(sys.argv)
    app.setApplicationName("BMLibrarian Lite")
    app.setOrganizationName("BMLibrarian")

    window = LiteMainWindow()
    window.show()

    # The hidden browser starts once the window is shown, and stops before
    # the event loop ends: its pages go before its profile (#480, stage 2)
    rescue = BrowserRescueService(
        window.storage, window.config, window.config.storage.browser_profile_dir
    )
    window.attach_rescue_service(rescue)
    app.aboutToQuit.connect(rescue.shutdown)
    QTimer.singleShot(0, rescue.start)

    return app.exec()
```

(`LiteStorage.get_document` returns `None` for an ID it does not hold.)

- [ ] **Step 5: Run the tests, then the app**

Run: `pytest tests/test_browser_rescue.py tests/test_transparency_manager.py -q`, then `pytest tests/ -q -m "not integration"`. Expected: pass.

Then launch the real app (use the `run` skill, or run `bmlibrarian-lite-gui` and quit it from the menu after the window appears). Expected: it starts and quits cleanly. The log shows no "QtWebEngine must be imported" warning, no "Release of profile requested but WebEnginePage still not deleted", and no traceback. `~/.bmlibrarian_lite/browser_profile/` is created only once an entry is visited. Note what you saw in the PR description.

- [ ] **Step 6: Commit**

```bash
git add src/bmlibrarian_lite/browser_rescue.py src/bmlibrarian_lite/transparency/transparency_manager.py src/bmlibrarian_lite/gui/systematic_review_tab.py src/bmlibrarian_lite/gui/app.py tests/test_browser_rescue.py tests/test_transparency_manager.py
git commit -m "feat(gui): a rescued PDF re-analyses its document (#480)"
```

---

### Task 11: Documentation

**Files:**
- Create: `doc/developer/browser_rescue.md`
- Modify: `doc/user/guide.md` (Full Text settings, after the Elsevier section ~line 110), `doc/developer/guide.md` (directory structure; CI's preflight; GUI testing: the engine harness), `doc/cross_platform/polite_request_pacing.md` (~line 266), `doc/cross_platform/fulltext_retrieval.md` (section "A challenged PDF (#480)": a desktop paragraph), `CLAUDE.md` (Python structure)

- [ ] **Step 1: Write `doc/developer/browser_rescue.md`**

Sections, each short, written for a developer new to this code:
1. **What it does.** A challenged open-access PDF is queued, visited later in a hidden QtWebEngine page, saved where discovery's cached-PDF tier finds it, and its document re-analysed. Link the spec and the spikes README.
2. **Modules.** One line per module: `browser_rescue.py`, `rescue_queue_store.py`, `gui/rescue_attempt.py`, `gui/browser_rescue_service.py`, `gui/web_engine.py`.
3. **States.** The diagram from the spec, plus `too_large`. Which outcome leads where. The attempt cap and the retry delay (D2, D3).
4. **Who queues.** `assess_document` (both analysis paths) and the MCP server. Stage 3 adds the interrogation tab. A challenged copy is not a paywall (D1).
5. **QtWebEngine facts the code depends on.** The probe list from Task 8's header. `prepare_web_engine()` before any `QApplication`. Page before profile at quit.
6. **Testing.** Why the engine runs in `tests/browser_rescue_harness.py` in a subprocess (#516/#517). How to run it alone: `QT_QPA_PLATFORM=offscreen python tests/browser_rescue_harness.py`. Why it fails rather than skips. CI's `QTWEBENGINE_DISABLE_SANDBOX`.
7. **Accepted limits.** A `no_pdf`/`gone` verdict does not rewrite the stored sentence (lodged, Task 12). Only one visit runs at a time. The reader sees no persistent indicator before stage 3.

- [ ] **Step 2: The other docs**

- `doc/user/guide.md`: a "#### Bot-walled PDFs (hidden browser)" subsection after the Elsevier one. It explains the setting in the words of `BROWSER_RESCUE_EXPLANATION`. It says rescued PDFs land in the PDF library, and that a rescued article's transparency badge is re-checked. It says the browser keeps its cookies in `~/.bmlibrarian_lite/browser_profile/` (readable by you alone). It says the "could not be asked … (the host asked for a robot check)" sentence is what an unrescued copy reads.
- `doc/cross_platform/polite_request_pacing.md`: replace the `BrowserSession`/Playwright bullet with one for the browser rescue. It does not go through `requests`, so it claims its slot with `limiter_for(host).claim()` without sleeping and schedules the visit for then (`browser_rescue.rescue_delay_ms`).
- `doc/cross_platform/fulltext_retrieval.md`, section "A challenged PDF (#480)": add "On the desktop a challenged copy is not a paywall, and is queued for the embedded browser (`doc/developer/browser_rescue.md`); the apps have no browser yet."
- `doc/developer/guide.md`: add the new modules to the directory structure. In the CI section, say the preflight is `.github/scripts/qt_preflight.py` and checks QtWebEngine. Under GUI testing, describe the harness rule.
- `CLAUDE.md`, Python structure: add `browser_rescue.py # Walled PDFs queued for the embedded browser: states, verdicts, what is queued`, `rescue_queue_store.py # The browser rescue queue's table`, and under `gui/`: `rescue_attempt / browser_rescue_service (the hidden browser pass), web_engine (QtWebEngine start-up)`.

- [ ] **Step 3: Commit**

```bash
git add doc CLAUDE.md
git commit -m "docs: the browser rescue (#480)"
```

---

### Task 12: Live acceptance, the lodged issue, handover, PR

**Files:**
- Create: `scripts/browser_rescue_acceptance.py`
- Create: `doc/developer/unpaywall_pdf_survey/spikes/2026-10-13-browser-rescue-hidden.jsonl` (the run's rows)
- Modify: `doc/developer/unpaywall_pdf_survey/spikes/README.md`, `HANDOVER.md`

- [ ] **Step 1: Write the acceptance script**

```python
"""Live acceptance: the spike's 41 walled addresses through the real hidden pass (#480, stage 2).

Reads the QtWebEngine spike's rows (DOI, group, host, result) and, from the
survey's rows, each DOI's PDF address. Queues them in a throwaway data
directory with a fresh profile, and runs the real ``BrowserRescueService``
with production timeouts and pacing until nothing is due. Writes one row per
address. PDFs go to a temporary folder, never the reader's library.

Usage:
    python scripts/browser_rescue_acceptance.py [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from bmlibrarian_lite.gui.web_engine import prepare_web_engine

prepare_web_engine()

from PySide6.QtCore import QEventLoop  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from bmlibrarian_lite.browser_rescue import RescueRequest, RescueState  # noqa: E402
from bmlibrarian_lite.config import LiteConfig  # noqa: E402
from bmlibrarian_lite.constants import SERVICE_UNPAYWALL_PDF  # noqa: E402
from bmlibrarian_lite.gui.browser_rescue_service import BrowserRescueService  # noqa: E402
from bmlibrarian_lite.storage import LiteStorage  # noqa: E402

SURVEY = Path("doc/developer/unpaywall_pdf_survey")
SPIKE_ROWS = SURVEY / "spikes/2026-10-04-qtwebengine-hidden.jsonl"
SURVEY_ROWS = (SURVEY / "2026-10-04-dev.jsonl", SURVEY / "2026-10-04-heldout.jsonl")
DEFAULT_OUT = SURVEY / "spikes/2026-10-13-browser-rescue-hidden.jsonl"
#: The longest the whole run may take: 41 visits of at most ~50 s each.
RUN_LIMIT_SECONDS = 45 * 60
IDLE_SECONDS = 5


def addresses() -> dict[str, str]:
    """Each surveyed DOI's PDF address."""
    found: dict[str, str] = {}
    for path in SURVEY_ROWS:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row.get("doi") and row.get("pdf_url"):
                found[row["doi"]] = row["pdf_url"]
    return found


def main() -> int:
    """Run the 41 addresses and write the rows."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    spike = [json.loads(line) for line in SPIKE_ROWS.read_text().splitlines()]
    pdf_urls = addresses()
    app = QApplication(sys.argv)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = LiteConfig()
        config.storage.data_dir = root / "data"
        storage = LiteStorage(config)
        for index, row in enumerate(spike):
            address = pdf_urls[row["doi"]]
            storage.enqueue_rescues([RescueRequest(
                f"spike-{index}", row["doi"], None, row.get("title", ""), address,
                urlparse(address).hostname or "", SERVICE_UNPAYWALL_PDF,
            )])
        service = BrowserRescueService(storage, config, root / "profile",
                                       pdf_path_for=lambda e: root / "pdfs" / f"{e.id}.pdf")
        started = time.monotonic()
        service.start()
        idle_since = None
        while time.monotonic() - started < RUN_LIMIT_SECONDS:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 100)
            idle = not service.busy and storage.next_rescue_entry() is None
            idle_since = (idle_since or time.monotonic()) if idle else None
            if idle_since and time.monotonic() - idle_since > IDLE_SECONDS:
                break
        service.shutdown()
        entries = {e.request.document_id: e for e in storage.rescue_entries(list(RescueState))}
        rows = []
        for index, row in enumerate(spike):
            entry = entries[f"spike-{index}"]
            rows.append({
                "doi": row["doi"], "group": row["group"], "host": row["host"],
                "spike_result": row["result"], "state": entry.state.value,
                "attempts": entry.attempts,
                "verdict": entry.verdict.to_dict() if entry.verdict else None,
            })
    args.out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    for group in ("pmc", "sciencedirect", "wall", "control"):
        counts = Counter(r["state"] for r in rows if r["group"] == group)
        print(f"{group:14} {dict(counts)}")
    print(f"minutes: {(time.monotonic() - started) / 60:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it on the live network**

Run (about 15–30 minutes; it is paced per host, so it is polite):

```bash
python scripts/browser_rescue_acceptance.py
```

Expected: PMC near 12/12 `rescued`, ScienceDirect `needs_person`, controls `no_pdf`. Other walls are around the spike's 5/14 hidden, so many `needs_person` (the shown window, stage 3, clears those). If PMC falls well short of the spike, stop and investigate before going on: compare one failing PMC address by hand in the harness profile (systematic-debugging). Do not tune the timeouts to fit.

- [ ] **Step 3: Record the result**

Add a section to `doc/developer/unpaywall_pdf_survey/spikes/README.md`:

```markdown
## 4. The real service's hidden pass (`2026-10-13-browser-rescue-hidden.jsonl`)

Stage 2's `BrowserRescueService`, production timeouts and pacing, a fresh
profile, the same 41 addresses (2026-10-13):

| | PMC | ScienceDirect | Other walls | Controls (no PDF) |
|---|---|---|---|---|
| Hidden | <n>/12 | <n>/10 | <n>/14 | <n>/5 `no_pdf` |
```

Fill each `<n>` from the script's printed counts. Below the table, write one sentence comparing the result with the spike's hidden row.

- [ ] **Step 4: Lodge the accepted limit, check the gates**

```bash
gh issue create --title "Desktop: a browser's no_pdf/gone verdict leaves the stored 'could not be asked' sentence" --body "The browser rescue (#480, stage 2) records no_pdf and gone verdicts in its queue, but the document's stored transparency caveat keeps the conservative 'challenged … could not be asked' sentence: only a rescue re-analyses. Spec: docs/superpowers/specs/2026-10-12-browser-rescue-design.md, 'Accepted limit'. A reader-facing kind for these verdicts is out of scope there; decide whether to add one."
pytest tests/ -q -m "not integration"
ruff check .
mypy src/
python .github/scripts/lint_delta.py --base-ref origin/master
```

Expected: 0 test failures; `lint_delta.py` reports no new findings.

- [ ] **Step 5: Update HANDOVER.md**

- Move stage 2 to "Recently landed (context)", compressed to the rules that still bind. A challenged copy is not a paywall. Engine tests run in a subprocess and fail rather than skip. `prepare_web_engine()` runs before any `QApplication`. Page before profile. The rescued PDF's path is `generate_pdf_path({"doi": doi})`. The retry delay and cap. Add the lodged issue's number.
- "In flight" now names stage 3 (review queue window, interrogation priority path, the status-bar indicator). Point at the spec section 4 and at the new developer doc.
- Prune to stay under 500 lines.

- [ ] **Step 6: Commit, push, open the PR**

```bash
git add scripts/browser_rescue_acceptance.py doc/developer/unpaywall_pdf_survey/spikes HANDOVER.md
git commit -m "test(fulltext): live acceptance of the hidden browser pass (#480)"
git push -u origin feat/browser-rescue-stage2-480
gh pr create --base master --title "Browser rescue, stage 2: the queue and the hidden browser pass (#480)" --body "<body>"
```

The PR body covers:
- What stage 2 does, in the reader's terms.
- Decisions D1–D8 as taken.
- The acceptance table.
- How to run the harness.
- The manual launch check from Task 10.
- `Closes #483.` and `Refs #480` (stage 3 follows), and "Deferred: #<lodged issue>".
- It ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

After merging, confirm #483 is closed and #480 and the lodged issue are open.
