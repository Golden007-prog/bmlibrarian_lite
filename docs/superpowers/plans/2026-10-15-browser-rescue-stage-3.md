# Browser Rescue, Stage 3: the Review Queue Window and the Reader's Priority Path — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the desktop, give the reader a window onto the browser rescue queue. Its embedded view lets a person pass the robot checks the hidden pass could not. A status-bar indicator says how many walled PDFs wait for the reader. A citation opened in the interrogation tab whose PDF is walled goes to the front of the queue, and the PDF is loaded into the session when it arrives.

**Architecture:** The rules stay pure (`browser_rescue.py`, and a new `browser_rescue_text.py` for what the reader reads). `BrowserRescueService` stays the one place a visit runs: it owns the profile, routes downloads, and now also runs the *shown* visits the window asks for, one at a time, hidden or shown. `RescueAttempt` gains a shown mode: its page goes into a `QWebEngineView`, and a person's visit is judged only by what it downloads. The window (`gui/review_queue_dialog.py`) never imports QtWebEngine; its view comes from `gui/rescue_view.py`, which only the service path loads. The indicator is its own widget (`gui/rescue_queue_indicator.py`). The interrogation tab enqueues at `RESCUE_PRIORITY_WAITING` and waits on the service's `entry_changed` signal.

**Tech Stack:** Python 3.12, PySide6 6.10 (QtWebEngineCore, QtWebEngineWidgets, already in the wheel), SQLite, pytest; engine scenarios in the existing subprocess harness against a loopback `http.server`.

**Spec:** `docs/superpowers/specs/2026-10-12-browser-rescue-design.md` (section 4, Testing, Stages: stage 3). Stage 1 landed in PR #521, stage 2 in PR #529. The developer notes are in `doc/developer/browser_rescue.md`.

## Global Constraints

- Golden rules: `doc/llm/golden_rules.md`. Type hints and Google docstrings on everything. No magic numbers (constants in `constants.py`). Errors are handled, logged and reported. Tests for everything. No inline stylesheets (`setObjectName` plus the stylesheet generator). `scaled()` or font metrics for pixels.
- No impersonation (spec decision 5): QtWebEngine's own User-Agent, no automation protocol, no stealth.
- One recovery path (spec decision 4): every visit, hidden or shown, runs in `BrowserRescueService` on the GUI thread. No worker thread ever waits on it. **One visit at a time.**
- A run never waits for the browser (spec decision 1). The interrogation tab waits only for an entry *the reader* is waiting on, and never longer than `RESCUE_READER_WAIT_LIMIT_MS`.
- "While the window drives its view, the hidden pass pauses. Both use the same per-host limiters" (spec section 4).
- Engine tests run **in their own subprocess** (`tests/browser_rescue_harness.py`), never in the suite's shared `QApplication` (#516, #517). They **fail rather than skip** when QtWebEngine cannot start. Suite tests call handlers directly and never pump.
- Importing `gui/app.py` never loads QtWebEngine (`test_importing_the_app_does_not_load_qtwebengine` must keep passing).
- Never record an absolute ruff/mypy count. The gate is `python .github/scripts/lint_delta.py --base-ref origin/master`.
- No closing keyword before a deferred issue number: write "Deferred: #N". This stage's PR closes **#480**, because stage 3 is its last desktop stage. The app ports have their own spec.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Decisions for the maintainer's review

The spec leaves these open or implies them. The plan takes the position shown, and each can be reversed before implementation.

- **D1. One visit at a time, hidden or shown; the service runs both.** The profile has one `downloadRequested` signal. With one attempt at a time, the existing routing (`_on_download_requested` → `self._attempt`) stays correct. A shown visit asked for while a hidden one is under way starts when that one ends (at most `RESCUE_HIDDEN_TIMEOUT_MS` plus the read grace). While a shown visit is asked for, under way, or a walk runs, the hidden pass takes no entry. That is the spec's "the hidden pass pauses".
- **D2. A person's visit is judged only by what it downloads.** No timeout and no page verdict. A 404 or a network failure is shown to the reader, not settled, because the page is theirs to read, reload and navigate. It ends with a verdict when a download finishes (PDF, too large, a wall served as a download, a non-PDF) or the renderer dies. Leaving it (another entry selected, *Dismiss*, the window closed, quit) ends it with **no verdict**, so the entry stays as it was and no attempt is counted. *Walk the queue* judges pages exactly as the hidden pass does, with `RESCUE_VISIBLE_TIMEOUT_MS` (60 s) per entry.
- **D3. In a shown visit, a link that opens a new window opens in the same page** (`createWindow` returns the page). A "Download PDF" link with `target=_blank` then arrives as *this* visit's download, which `accept_download`'s own-page check requires. `JavascriptCanOpenWindows` stays at its default (off), so a script with no user gesture cannot open windows. The hidden page is unchanged.
- **D4. The indicator counts what waits for the reader:** `needs_person`, plus `pending` while no hidden pass will visit (the setting is off or QtWebEngine is unavailable). It is hidden at 0 and refreshed on every verdict, dismissal and enqueue, after Settings is saved, and every `RESCUE_POLL_INTERVAL_MS` (which catches the MCP server's entries). The window's default filter, "Waiting for you", lists the same set.
- **D5. A Tools menu.** The app has no menu bar. The spec opens the window "from a status-bar indicator … and from the menu", so a menu bar gets a *Tools* menu with *Walled PDFs…*. Without it, the window could not be opened while the indicator is hidden at 0, so settled entries could never be inspected.
- **D6. Only open entries are visited.** Selecting a settled entry (`no_pdf`, `gone`, `too_large`, `dismissed`, `superseded`, `rescued`) shows its verdict and ends any visit. *Open in system browser* works for every entry (nothing is saved from it). *Dismiss* works for open entries only.
- **D7. The interrogation tab's priority path covers the citation path only** (`load_from_citation` → `FulltextDiscoveryWorker`). The *Fetch PDF* button's path (`PDFDiscoveryWorker`) has no storage document ID: its metadata is typed in or carries no `id`. Queueing it would mint a `doi-…` ID, the identity question already open in #533. That path is lodged as an issue, not built here.
- **D8. How long the tab waits.** It waits while one of the document's entries is `pending`, is due now, and has had no verdict since the wait began. A transport verdict ends the wait (the entry stays pending, but a retry is 15 minutes away). So does `RESCUE_READER_WAIT_LIMIT_MS` (90 s: a hidden visit already under way, then this one, each at most 30 s, plus pacing). With the hidden pass off or unavailable, the tab does not wait: it falls back to the abstract at once and offers the review queue.
- **D9. What the tab says.** On a rescue, the PDF is loaded and labelled "Full Text (PDF, fetched in the built-in browser)". When an entry is still open after the wait, the abstract is loaded with the new degradation "the publisher's copy is behind a robot check", and a message box offers *Open the review queue*, focused on that entry. Otherwise today's fallback applies ("the full text could not be retrieved"). *Cancel* falls back with "retrieving the full text was cancelled", and the entry stays queued. A rescue that lands after the wait ended is not loaded into the tab. The offer says so ("then open the citation again").
- **D10. The worker carries its record out.** `FulltextDiscoveryWorker` keeps the result's `LookupRecord` in `self.lookups`, set before its terminal signal. Signals and `SingleOutcome` are not changed.
- **D11. The status-bar message is no longer "hidden".** `rescued_pdf_message` now says "in the built-in browser", since a rescue may come from the window.
- **D12. Without QtWebEngine, the window still opens.** It shows the list, *Dismiss* and *Open in system browser*, and a note in place of the view. *Walk the queue* is disabled.

## Review Focus

The five inputs no other task pins, most likely to bite a person first. Each is pinned by a test in the task named.

1. **The reader closes the window (or quits) mid-visit.** The visit ends with no verdict, the entry is unchanged, a walk stops, and at quit the shown page goes before the profile with no "Release of profile requested" warning. (Task 6: `test_closing_the_window_ends_the_visit_and_the_walk`. Task 4 harness: `test_quit_during_a_shown_visit_releases_the_page_first`.)
2. **The hidden pass is slow or stuck while the reader waits in the interrogation tab.** The wait ends at `RESCUE_READER_WAIT_LIMIT_MS` with the abstract and the offer, never hanging. (Task 7: `test_the_wait_ends_at_its_limit_with_the_offer`.)
3. **The hidden pass is off or unavailable when the reader's citation hits a wall.** The tab does not wait for a pass that will never come: it falls back at once and offers the queue. (Task 7: `test_with_the_hidden_pass_off_the_tab_offers_the_queue_at_once`.)
4. **The reader clicks through entries quickly.** A start scheduled for an entry the reader has left does nothing, and only one visit ever runs. (Task 4: `test_a_start_scheduled_for_an_entry_the_reader_left_does_nothing`.)
5. **Another process settles an entry, or the reader dismisses one, during a walk.** The walk re-reads each entry before visiting it and skips one no longer open. A verdict for an entry dismissed meanwhile is not applied (the store's existing guard). (Task 5: `test_a_walk_skips_an_entry_settled_meanwhile`.)

---

## File structure

| File | Responsibility |
|---|---|
| `src/bmlibrarian_lite/browser_rescue.py` | + `stamp` (moved from the store), `fetchable_host`, `dismissed_state`, `awaiting_reader_states`, `WaitOutcome`, `wait_watch`, `wait_outcome`, `dismiss_entry` |
| `src/bmlibrarian_lite/browser_rescue_text.py` (new) | What the reader reads about the queue: state, service and verdict labels, the window's and the tab's sentences |
| `src/bmlibrarian_lite/rescue_queue_store.py` | + `entry`, `dismiss`, `count`, `for_document`; `stamp` imported from the rules |
| `src/bmlibrarian_lite/storage.py` | + `rescue_entry`, `dismiss_rescue`, `count_rescue_entries`, `rescue_entries_for_document` |
| `src/bmlibrarian_lite/constants.py` | `RESCUE_VISIBLE_TIMEOUT_MS`, `RESCUE_READER_WAIT_LIMIT_MS`, the window's size in characters |
| `src/bmlibrarian_lite/gui/rescue_attempt.py` | Shown mode: `view=`, `judge_pages=`, `_ShownPage` |
| `src/bmlibrarian_lite/gui/browser_rescue_service.py` | Shown visits, the walk, `entry_changed` |
| `src/bmlibrarian_lite/gui/rescue_view.py` (new) | `make_rescue_view()`: the only module that imports QtWebEngineWidgets |
| `src/bmlibrarian_lite/gui/review_queue_dialog.py` (new) | The window |
| `src/bmlibrarian_lite/gui/rescue_queue_indicator.py` (new) | "Walled PDFs: N" |
| `src/bmlibrarian_lite/gui/app.py` | Indicator, Tools menu, one window, wiring; D11 |
| `src/bmlibrarian_lite/gui/workers.py` | `FulltextDiscoveryWorker.lookups` (D10) |
| `src/bmlibrarian_lite/gui/document_interrogation_tab.py` | The priority path |
| `src/bmlibrarian_lite/resources/styles/stylesheet_generator.py` | Rules for the new object names |
| `tests/browser_rescue_harness.py`, `tests/test_browser_rescue_engine.py` | Shown-visit, walk and quit scenarios |
| Tests (new) | `tests/test_browser_rescue_text.py`, `tests/test_review_queue_dialog.py`, `tests/test_rescue_queue_indicator.py`, `tests/test_interrogation_browser_rescue.py` |
| Tests (extended) | `tests/test_browser_rescue.py`, `tests/test_rescue_queue_store.py`, `tests/test_rescue_attempt_handlers.py`, `tests/test_browser_rescue_service.py`, `tests/test_app_browser_rescue_startup.py`, `tests/test_interrogation_degradation.py` (stub gains `lookups`) |
| Docs | `doc/developer/browser_rescue.md`, `doc/user/guide.md`, `CLAUDE.md`, `HANDOVER.md` |

---

### Task 1: The rules the window and the tab need

**Files:**
- Modify: `src/bmlibrarian_lite/browser_rescue.py`
- Modify: `src/bmlibrarian_lite/rescue_queue_store.py` (import `stamp` instead of defining it)
- Modify: `src/bmlibrarian_lite/constants.py` (after `RESCUE_PRIORITY_WAITING`)
- Create: `src/bmlibrarian_lite/browser_rescue_text.py`
- Test: `tests/test_browser_rescue.py`, `tests/test_browser_rescue_text.py` (new)

**Interfaces:**
- Produces, in `browser_rescue`:
  - `stamp(moment: datetime) -> str` (moved; `rescue_queue_store.stamp` stays importable)
  - `fetchable_host(address: str | None) -> str | None`
  - `dismissed_state(state: RescueState) -> RescueState`
  - `awaiting_reader_states(hidden_pass_runs: bool) -> frozenset[RescueState]`
  - `class WaitOutcome(Enum)`: `WAITING`, `RESCUED`, `NEEDS_READER`, `NOTHING`
  - `wait_watch(entries: Sequence[RescueEntry], hidden_pass_runs: bool, now: datetime) -> frozenset[int]`
  - `wait_outcome(entries: Sequence[RescueEntry], watched: AbstractSet[int], answered: AbstractSet[int]) -> WaitOutcome`
  - `class RescueDismissals(Protocol)` with `dismiss_rescue(entry_id: int) -> RecordedVerdict`
  - `dismiss_entry(queue: RescueDismissals, entry_id: int) -> RescueEntry | None`
- Produces, in `browser_rescue_text`: `state_label(state, hidden_pass_runs) -> str`, `service_label(service) -> str`, `verdict_text(verdict) -> str`, `retry_text(entry) -> str`, and the sentence constants listed in Step 6.
- Produces, in `constants`: `RESCUE_VISIBLE_TIMEOUT_MS = 60_000`, `RESCUE_READER_WAIT_LIMIT_MS = 90_000`, `REVIEW_QUEUE_WIDTH_CHARS = 140`, `REVIEW_QUEUE_HEIGHT_LINES = 40`.

- [ ] **Step 1: Write the failing rule tests** (append to `tests/test_browser_rescue.py`; add the imports to its import block)

```python
from datetime import UTC, datetime, timedelta

from bmlibrarian_lite.browser_rescue import (
    WaitOutcome,
    awaiting_reader_states,
    dismiss_entry,
    dismissed_state,
    fetchable_host,
    stamp,
    wait_outcome,
    wait_watch,
)
from bmlibrarian_lite.rescue_queue_store import RecordedVerdict

T0 = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def _entry(entry_id: int, state: RescueState, retry_after: str | None = None) -> RescueEntry:
    request = RescueRequest("doc-1", DOI, "123", "A title", f"{ADDRESS}?{entry_id}",
                            "www.sciencedirect.com", SERVICE_UNPAYWALL_PDF)
    return RescueEntry(entry_id, request, state, 0, None, 0, "t", "t", retry_after)


# -- addresses -----------------------------------------------------------------


@pytest.mark.parametrize(
    "address, host",
    [
        ("https://a.example/x.pdf", "a.example"),
        ("HTTP://A.example/x.pdf", "a.example"),
        ("ftp://a.example/x.pdf", None),
        ("/relative/x.pdf", None),
        ("https:///x.pdf", None),
        ("javascript:alert(1)", None),
        ("", None),
        (None, None),
    ],
)
def test_only_a_web_address_with_a_host_is_fetchable(address: str | None, host: str | None) -> None:
    """The window opens only what a browser can visit; a stored address is untrusted."""
    assert fetchable_host(address) == host


# -- dismissal -------------------------------------------------------------------


@pytest.mark.parametrize("state", sorted(OPEN_RESCUE_STATES, key=lambda s: s.value))
def test_an_open_entry_is_dismissed(state: RescueState) -> None:
    """An open entry is dismissed."""
    assert dismissed_state(state) is RescueState.DISMISSED


@pytest.mark.parametrize("state", [s for s in RescueState if s not in OPEN_RESCUE_STATES])
def test_a_settled_entry_stays_as_it_is(state: RescueState) -> None:
    """A settled entry is never reopened or rewritten by a dismissal."""
    assert dismissed_state(state) is state


class _Dismissals:
    def __init__(self, result: RecordedVerdict | Exception) -> None:
        self.result = result

    def dismiss_rescue(self, entry_id: int) -> RecordedVerdict:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_dismissing_returns_the_entry_it_changed() -> None:
    """A dismissal that applied returns the dismissed entry."""
    dismissed = _entry(1, RescueState.DISMISSED)
    assert dismiss_entry(_Dismissals(RecordedVerdict(dismissed, applied=True)), 1) == dismissed


def test_dismissing_an_entry_no_longer_open_changes_nothing() -> None:
    """One another process settled first is not reported as dismissed."""
    assert dismiss_entry(_Dismissals(RecordedVerdict(_entry(1, RescueState.RESCUED), applied=False)), 1) is None


def test_a_queue_that_cannot_be_written_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    """The window reports the failure; nothing raises into Qt's slot."""
    assert dismiss_entry(_Dismissals(sqlite3.OperationalError("database is locked")), 1) is None
    assert "could not be dismissed" in caplog.text


# -- who waits for the reader -------------------------------------------------------


def test_with_a_hidden_pass_only_a_person_s_entries_wait_for_the_reader() -> None:
    """The hidden pass visits pending entries; only needs_person waits for the reader."""
    assert awaiting_reader_states(True) == frozenset({RescueState.NEEDS_PERSON})


def test_without_a_hidden_pass_every_open_entry_waits_for_the_reader() -> None:
    """With no hidden pass to visit them, pending entries wait for the reader too."""
    assert awaiting_reader_states(False) == OPEN_RESCUE_STATES


# -- the reader waiting in the interrogation tab --------------------------------------


def test_the_tab_watches_pending_entries_due_now() -> None:
    """Only pending entries the hidden pass will visit now are waited for."""
    later = stamp(T0 + timedelta(minutes=5))
    earlier = stamp(T0 - timedelta(minutes=5))
    entries = [
        _entry(1, RescueState.PENDING),
        _entry(2, RescueState.PENDING, retry_after=later),
        _entry(3, RescueState.PENDING, retry_after=earlier),
        _entry(4, RescueState.NEEDS_PERSON),
    ]
    assert wait_watch(entries, True, T0) == frozenset({1, 3})


def test_without_a_hidden_pass_nothing_is_waited_for() -> None:
    """No pass will come, so the tab does not wait."""
    assert wait_watch([_entry(1, RescueState.PENDING)], False, T0) == frozenset()


def test_a_watched_entry_without_a_verdict_keeps_the_reader_waiting() -> None:
    """Waiting until the watched entry has had its visit."""
    assert wait_outcome([_entry(1, RescueState.PENDING)], {1}, set()) is WaitOutcome.WAITING


def test_a_rescue_during_the_wait_ends_it() -> None:
    """One rescued address is enough, whatever the others do."""
    entries = [_entry(1, RescueState.RESCUED), _entry(2, RescueState.PENDING)]
    assert wait_outcome(entries, {1, 2}, {1}) is WaitOutcome.RESCUED


def test_a_rescue_from_before_the_wait_is_not_this_wait_s() -> None:
    """A PDF rescued earlier is not on disk, or discovery would have found it."""
    assert wait_outcome([_entry(1, RescueState.RESCUED)], set(), set()) is WaitOutcome.NOTHING


def test_a_transport_verdict_ends_the_wait_with_the_entry_still_open() -> None:
    """The entry stays pending, a retry is minutes away: the reader is offered the queue."""
    assert wait_outcome([_entry(1, RescueState.PENDING)], {1}, {1}) is WaitOutcome.NEEDS_READER


def test_a_challenge_ends_the_wait_and_offers_the_queue() -> None:
    """A wall the hidden pass could not pass waits for the reader."""
    assert wait_outcome([_entry(1, RescueState.NEEDS_PERSON)], {1}, {1}) is WaitOutcome.NEEDS_READER


def test_every_entry_settled_without_a_pdf_offers_nothing() -> None:
    """No PDF behind the wall: today's fallback."""
    entries = [_entry(1, RescueState.NO_PDF), _entry(2, RescueState.GONE)]
    assert wait_outcome(entries, {1, 2}, {1, 2}) is WaitOutcome.NOTHING


def test_the_wait_lasts_while_any_watched_entry_is_unvisited() -> None:
    """Two addresses: one answered, one still to visit."""
    entries = [_entry(1, RescueState.NEEDS_PERSON), _entry(2, RescueState.PENDING)]
    assert wait_outcome(entries, {1, 2}, {1}) is WaitOutcome.WAITING
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_browser_rescue.py -q`
Expected: collection error, `ImportError: cannot import name 'WaitOutcome'`.

- [ ] **Step 3: Implement the rules**

In `src/bmlibrarian_lite/constants.py`, after `RESCUE_PRIORITY_WAITING` (and update that constant's comment: "Used by the interrogation tab's priority path"):

```python
#: How long a visit in the review queue window's *Walk the queue* waits for
#: a download before it reads the page: the spike's 60 s, which cleared the
#: Cloudflare publishers 9 times in 9 with the window shown and nobody ticking.
RESCUE_VISIBLE_TIMEOUT_MS = 60_000
#: The longest the interrogation tab waits for the hidden browser on a
#: walled PDF the reader asked for: a visit already under way, then this
#: one, each at most RESCUE_HIDDEN_TIMEOUT_MS plus RESCUE_PAGE_READ_GRACE_MS,
#: and the host's pacing slot. Past it the reader is offered the review queue.
RESCUE_READER_WAIT_LIMIT_MS = 90_000
#: The review queue window's initial size, in average character widths and
#: line heights of its font.
REVIEW_QUEUE_WIDTH_CHARS = 140
REVIEW_QUEUE_HEIGHT_LINES = 40
```

In `src/bmlibrarian_lite/browser_rescue.py`: add `from collections.abc import Callable, Mapping, Sequence, Set as AbstractSet` and `from datetime import UTC, datetime`. Add a `TYPE_CHECKING` import of `RecordedVerdict` from `.rescue_queue_store` (the store imports this module, so a runtime import would be circular). Then add:

```python
def stamp(moment: datetime) -> str:
    """A moment as stored: UTC ISO 8601 to the microsecond, so text order is time order.

    Args:
        moment: A timezone-aware moment.

    Returns:
        The stored text.
    """
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
```

Use it in `rescue_requests`, replacing the inline `urlparse` test:

```python
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
```

Then, after `next_state`:

```python
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
) -> WaitOutcome:
    """Where a reader's wait stands.

    Args:
        entries: The document's entries as they stand now.
        watched: The entries waited for (:func:`wait_watch`).
        answered: The entries that have had a verdict since the wait began.

    Returns:
        ``RESCUED`` when an entry answered during the wait was rescued (one
        rescued earlier is not this wait's: its PDF is not on disk, or
        discovery would have found it); ``WAITING`` while a watched entry is
        pending and unanswered; ``NEEDS_READER`` while any entry is open;
        ``NOTHING`` otherwise.
    """
    if any(e.state is RescueState.RESCUED and e.id in answered for e in entries):
        return WaitOutcome.RESCUED
    if any(
        e.state is RescueState.PENDING and e.id in watched and e.id not in answered
        for e in entries
    ):
        return WaitOutcome.WAITING
    if any(e.state in OPEN_RESCUE_STATES for e in entries):
        return WaitOutcome.NEEDS_READER
    return WaitOutcome.NOTHING


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


def dismiss_entry(queue: RescueDismissals, entry_id: int) -> RescueEntry | None:
    """Dismiss an entry for the reader, logging rather than raising a failure.

    Args:
        queue: The storage.
        entry_id: The entry.

    Returns:
        The dismissed entry; ``None`` when it was no longer open (settled or
        dismissed elsewhere meanwhile) or the queue could not be written
        (logged).
    """
    try:
        recorded = queue.dismiss_rescue(entry_id)
    except (sqlite3.Error, SQLiteError) as e:
        logger.error("Walled PDF %s could not be dismissed: %s", entry_id, e)
        return None
    return recorded.entry if recorded.applied else None
```

In `src/bmlibrarian_lite/rescue_queue_store.py`: delete `def stamp(...)` and import it with the rules (`from .browser_rescue import OPEN_RESCUE_STATES, RescueEntry, RescueRequest, RescueState, RescueVerdict, next_state, stamp`). `store.stamp` stays importable for `test_stamps_sort_as_time_does`.

- [ ] **Step 4: Run them to verify they pass**

Run: `pytest tests/test_browser_rescue.py tests/test_rescue_queue_store.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing text tests** (`tests/test_browser_rescue_text.py`, new)

```python
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
```

- [ ] **Step 6: Implement `src/bmlibrarian_lite/browser_rescue_text.py`**

```python
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
    """A sentence, with the verdict's HTTP status when one was seen."""
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
```

- [ ] **Step 7: Run all of Task 1's tests**

Run: `pytest tests/test_browser_rescue.py tests/test_browser_rescue_text.py tests/test_rescue_queue_store.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/bmlibrarian_lite/browser_rescue.py src/bmlibrarian_lite/browser_rescue_text.py \
  src/bmlibrarian_lite/rescue_queue_store.py src/bmlibrarian_lite/constants.py \
  tests/test_browser_rescue.py tests/test_browser_rescue_text.py
git commit -m "feat(rescue): the rules and wording for the review queue and the reader's wait (#480)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The store: one entry, a dismissal, a count, a document's entries

**Files:**
- Modify: `src/bmlibrarian_lite/rescue_queue_store.py`
- Modify: `src/bmlibrarian_lite/storage.py` (beside `rescue_entries`)
- Test: `tests/test_rescue_queue_store.py`

**Interfaces:**
- Consumes: `dismissed_state`, `stamp` (Task 1).
- Produces, in the store: `entry(conn, entry_id) -> RescueEntry | None`, `dismiss(conn, entry_id, now) -> RecordedVerdict`, `count(conn, states) -> int`, `for_document(conn, document_id) -> list[RescueEntry]`.
- Produces, on `LiteStorage`: `rescue_entry(entry_id: int) -> RescueEntry | None`, `dismiss_rescue(entry_id: int) -> RecordedVerdict`, `count_rescue_entries(states: Iterable[RescueState]) -> int`, `rescue_entries_for_document(document_id: str) -> list[RescueEntry]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_rescue_queue_store.py`)

```python
CHALLENGE = RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare",))


def _queued(conn: sqlite3.Connection, *requests: RescueRequest) -> list[int]:
    store.enqueue(conn, list(requests), 0, T0)
    return [r["id"] for r in conn.execute(f"SELECT id FROM {store.TABLE} ORDER BY id")]


def test_a_person_s_rescue_settles_a_needs_person_entry_and_its_siblings(conn: sqlite3.Connection) -> None:
    """needs_person -> rescued (carried in from stage 2), superseding the document's other open entries."""
    first, second = _queued(conn, _request(), _request(address="https://b.example/2.pdf"))
    store.record_verdict(conn, first, CHALLENGE, T0)
    recorded = store.record_verdict(conn, first, PDF, T0)
    assert recorded.applied
    assert recorded.entry is not None and recorded.entry.state is RescueState.RESCUED
    assert recorded.entry.attempts == 2
    assert _states(conn)[f"doc-1 https://b.example/2.pdf"] == RescueState.SUPERSEDED.value


@pytest.mark.parametrize("before", [None, CHALLENGE])
def test_an_open_entry_is_dismissed_and_never_visited(conn: sqlite3.Connection, before: RescueVerdict | None) -> None:
    """A pending or needs_person entry is dismissed; the hidden pass never takes it."""
    (entry_id,) = _queued(conn, _request())
    if before is not None:
        store.record_verdict(conn, entry_id, before, T0)
    recorded = store.dismiss(conn, entry_id, T0)
    assert recorded.applied
    assert recorded.entry is not None and recorded.entry.state is RescueState.DISMISSED
    assert recorded.entry.retry_after is None
    assert store.next_pending(conn, T0 + timedelta(days=1)) is None


def test_a_dismissal_keeps_the_last_verdict(conn: sqlite3.Connection) -> None:
    """The window still shows what the last visit saw."""
    (entry_id,) = _queued(conn, _request())
    store.record_verdict(conn, entry_id, CHALLENGE, T0)
    store.dismiss(conn, entry_id, T0)
    entry = store.entry(conn, entry_id)
    assert entry is not None and entry.verdict == CHALLENGE


def test_a_settled_entry_is_not_dismissed(conn: sqlite3.Connection) -> None:
    """A rescue another process recorded first stands."""
    (entry_id,) = _queued(conn, _request())
    store.record_verdict(conn, entry_id, PDF, T0)
    recorded = store.dismiss(conn, entry_id, T0)
    assert not recorded.applied
    assert recorded.entry is not None and recorded.entry.state is RescueState.RESCUED


def test_dismissing_a_missing_entry_is_none(conn: sqlite3.Connection) -> None:
    """An entry deleted meanwhile is not dismissed."""
    assert store.dismiss(conn, 999, T0) == store.RecordedVerdict(None, applied=False)


def test_a_dismissed_entry_is_not_reopened_by_a_later_run(conn: sqlite3.Connection) -> None:
    """A later analysis of the same document leaves the dismissal as it is."""
    (entry_id,) = _queued(conn, _request())
    store.dismiss(conn, entry_id, T0)
    assert store.enqueue(conn, [_request()], RESCUE_PRIORITY_WAITING, T0) == 0
    assert _states(conn) == {"doc-1 https://a.example/1.pdf": RescueState.DISMISSED.value}


def test_a_verdict_after_a_dismissal_is_not_applied(conn: sqlite3.Connection) -> None:
    """A visit that ends after the reader dismissed its entry changes nothing."""
    (entry_id,) = _queued(conn, _request())
    store.dismiss(conn, entry_id, T0)
    assert not store.record_verdict(conn, entry_id, PDF, T0).applied


def test_one_entry_is_read_by_id(conn: sqlite3.Connection) -> None:
    """An entry is read by its ID; a missing one is None."""
    (entry_id,) = _queued(conn, _request())
    entry = store.entry(conn, entry_id)
    assert entry is not None and entry.request == _request()
    assert store.entry(conn, entry_id + 1) is None


def test_the_count_is_of_the_states_asked_for(conn: sqlite3.Connection) -> None:
    """Counted by state; no states, no count."""
    first, _second = _queued(conn, _request(), _request(address="https://b.example/2.pdf"))
    store.record_verdict(conn, first, CHALLENGE, T0)
    assert store.count(conn, [RescueState.NEEDS_PERSON]) == 1
    assert store.count(conn, [RescueState.NEEDS_PERSON, RescueState.PENDING]) == 2
    assert store.count(conn, []) == 0


def test_a_document_s_entries_are_its_own_oldest_first(conn: sqlite3.Connection) -> None:
    """Only that document's entries, in the order they were queued, any state."""
    store.enqueue(conn, [_request()], 0, T0)
    store.enqueue(conn, [_request("doc-2")], 0, T0 + timedelta(seconds=1))
    store.enqueue(conn, [_request(address="https://b.example/2.pdf")], 0, T0 + timedelta(seconds=2))
    entries = store.for_document(conn, "doc-1")
    assert [e.request.address for e in entries] == ["https://a.example/1.pdf", "https://b.example/2.pdf"]


def test_storage_reads_counts_and_dismisses(storage: LiteStorage) -> None:
    """LiteStorage's four new methods, end to end on a file."""
    storage.enqueue_rescues([_request()])
    (entry,) = storage.rescue_entries_for_document("doc-1")
    assert storage.rescue_entry(entry.id) == entry
    assert storage.count_rescue_entries([RescueState.PENDING]) == 1
    assert storage.dismiss_rescue(entry.id).applied
    assert storage.count_rescue_entries([RescueState.PENDING]) == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_rescue_queue_store.py -q`
Expected: FAIL with `AttributeError: module 'bmlibrarian_lite.rescue_queue_store' has no attribute 'dismiss'` (and `entry`, `count`, `for_document`). The first test may pass already: it pins existing behaviour that had no test.

- [ ] **Step 3: Implement the store functions** (in `rescue_queue_store.py`; import `dismissed_state` with the other rules)

```python
def entry(conn: sqlite3.Connection, entry_id: int) -> RescueEntry | None:
    """One entry by its ID.

    Args:
        conn: The connection.
        entry_id: The entry.

    Returns:
        The entry; ``None`` when it is gone or its state is a newer build's.
    """
    row = conn.execute(f"SELECT * FROM {TABLE} WHERE id = ?", (entry_id,)).fetchone()
    return entry_from_row(row) if row is not None else None


def dismiss(conn: sqlite3.Connection, entry_id: int, now: datetime) -> RecordedVerdict:
    """Set an open entry aside for the reader; its last verdict is kept.

    One immediate transaction, as :func:`record_verdict` takes, so a rescue
    another process recorded in between is not overwritten.

    Args:
        conn: The connection.
        entry_id: The entry.
        now: The moment.

    Returns:
        The entry as it now stands, and whether it was dismissed; an entry no
        longer open is returned unchanged and not applied.
    """
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = entry(conn, entry_id)
        if current is None or current.state not in OPEN_RESCUE_STATES:
            conn.rollback()
            return RecordedVerdict(current, applied=False)
        state = dismissed_state(current.state)
        at = stamp(now)
        conn.execute(
            f"UPDATE {TABLE} SET state = ?, retry_after = NULL, updated_at = ? WHERE id = ?",
            (state.value, at, entry_id),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return RecordedVerdict(replace(current, state=state, retry_after=None, updated_at=at), applied=True)


def count(conn: sqlite3.Connection, states: Iterable[RescueState]) -> int:
    """How many entries are in the given states.

    Args:
        conn: The connection.
        states: The states counted.

    Returns:
        The count; 0 for no states.
    """
    values = [state.value for state in states]
    if not values:
        return 0
    marks = ", ".join("?" for _ in values)
    row = conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE state IN ({marks})", values).fetchone()
    return int(row[0])


def for_document(conn: sqlite3.Connection, document_id: str) -> list[RescueEntry]:
    """A document's entries, in any state, oldest first.

    Args:
        conn: The connection.
        document_id: The document.

    Returns:
        Its entries; rows this build cannot read are left out.
    """
    rows = conn.execute(
        f"SELECT * FROM {TABLE} WHERE document_id = ? ORDER BY created_at, id", (document_id,)
    )
    return [e for e in map(entry_from_row, rows) if e is not None]
```

Also update `RecordedVerdict`'s docstring: "What recording one visit's verdict, or a dismissal, did."

- [ ] **Step 4: Implement the `LiteStorage` methods** (after `rescue_entries`, same style)

```python
    def rescue_entry(self, entry_id: int) -> RescueEntry | None:
        """One queue entry by its ID.

        Args:
            entry_id: The entry.

        Returns:
            The entry; ``None`` when it is gone or a newer build's.
        """
        with self._sqlite_connection() as conn:
            return rescue_queue_store.entry(conn, entry_id)

    def dismiss_rescue(self, entry_id: int) -> RecordedVerdict:
        """Set an open queue entry aside for the reader.

        Args:
            entry_id: The entry.

        Returns:
            The entry as it now stands, and whether it was dismissed.
        """
        with self._sqlite_connection() as conn:
            return rescue_queue_store.dismiss(conn, entry_id, datetime.now(UTC))

    def count_rescue_entries(self, states: Iterable[RescueState]) -> int:
        """How many queue entries are in the given states.

        Args:
            states: The states counted.

        Returns:
            The count.
        """
        with self._sqlite_connection() as conn:
            return rescue_queue_store.count(conn, states)

    def rescue_entries_for_document(self, document_id: str) -> list[RescueEntry]:
        """A document's queue entries, oldest first.

        Args:
            document_id: The document.

        Returns:
            Its entries, in any state.
        """
        with self._sqlite_connection() as conn:
            return rescue_queue_store.for_document(conn, document_id)
```

- [ ] **Step 5: Run the store tests**

Run: `pytest tests/test_rescue_queue_store.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bmlibrarian_lite/rescue_queue_store.py src/bmlibrarian_lite/storage.py tests/test_rescue_queue_store.py
git commit -m "feat(rescue): read one entry, dismiss, count, and list a document's entries (#480)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: A visit a person sees

**Files:**
- Modify: `src/bmlibrarian_lite/gui/rescue_attempt.py`
- Test: `tests/test_rescue_attempt_handlers.py`; harness `tests/browser_rescue_harness.py` and `tests/test_browser_rescue_engine.py`

**Interfaces:**
- Produces: `RescueAttempt(profile, address, pdf_path, timeout_ms, stall_ms, max_pdf_bytes, parent=None, *, view: QWebEngineView | None = None, judge_pages: bool = True)`. With `judge_pages=False` (D2): no timeout, page answers are noted (status, headers, and the host penalised for 429/503) but never end the visit, and only a download or a renderer crash ends it. With a `view`, the page is a `_ShownPage` (D3), set into the view on `start()` and unmuted. A hidden visit (`view=None`, `judge_pages=True`) behaves exactly as before.
- Produces: `_ShownPage(QWebEnginePage)` whose `createWindow(_type)` returns `self`.

- [ ] **Step 1: Write the failing handler tests** (append to `tests/test_rescue_attempt_handlers.py`)

```python
def _person_attempt(tmp_path: Path) -> tuple[RescueAttempt, list[RescueVerdict]]:
    """A person's visit (judge_pages=False) with a stand-in page, as if started."""
    attempt = RescueAttempt(
        None,  # type: ignore[arg-type]
        ADDRESS, tmp_path / "article.pdf", TIMEOUT_MS, STALL_MS, MAX_PDF_BYTES,
        judge_pages=False,
    )
    attempt._page = _page()  # type: ignore[assignment]
    verdicts: list[RescueVerdict] = []
    attempt.finished.connect(verdicts.append)
    return attempt, verdicts


@pytest.mark.parametrize("status", [404, 410, 200, 403])
def test_a_person_s_page_is_never_judged(qapp, tmp_path: Path, status: int) -> None:
    """The page is the person's to read: no answer ends their visit (D2)."""
    attempt, verdicts = _person_attempt(tmp_path)
    attempt._on_loading_changed(_answered(status))
    assert verdicts == []


@pytest.mark.parametrize("domain", [Domain.ConnectionErrorDomain, Domain.DnsErrorDomain])
def test_a_network_failure_is_shown_to_the_person_not_settled(qapp, tmp_path: Path, domain: Any) -> None:
    """Chromium's error page is shown; the person can reload."""
    attempt, verdicts = _person_attempt(tmp_path)
    attempt._on_loading_changed(_Info(Status.LoadFailedStatus, domain, ERR_UNEXPECTED))
    assert verdicts == []


def test_a_person_s_throttled_page_still_slows_its_host(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pacing is shared whoever visits: a 429 penalises the host's limiter."""
    penalised: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        rescue_attempt, "penalise_host_for_answer",
        lambda address, status, headers: penalised.append((address, status)) or True,
    )
    attempt, _verdicts = _person_attempt(tmp_path)
    attempt._on_loading_changed(_answered(429))
    assert penalised == [(ADDRESS, 429)]


def test_a_person_s_renderer_crash_ends_the_visit(qapp, tmp_path: Path) -> None:
    """A dead page shows nothing: the visit learnt nothing."""
    attempt, verdicts = _person_attempt(tmp_path)
    attempt._on_render_process_terminated()
    assert verdicts == [RescueVerdict(RescueOutcome.TRANSPORT)]


def test_a_person_s_visit_has_no_timeout(qapp, tmp_path: Path) -> None:
    """A timeout that fires anyway (it is never started) does not read the page."""
    attempt, verdicts = _person_attempt(tmp_path)
    attempt._on_timeout()
    assert verdicts == []


def test_a_person_s_download_is_still_judged(qapp, tmp_path: Path) -> None:
    """Only what arrives ends a person's visit: a size announced over the limit is too large."""
    attempt, verdicts = _person_attempt(tmp_path)
    request = _Request(attempt._page, total_bytes=MAX_PDF_BYTES + 1)
    attempt.accept_download(request)  # type: ignore[arg-type]
    assert request.cancelled
    assert verdicts == [RescueVerdict(RescueOutcome.TOO_LARGE)]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_rescue_attempt_handlers.py -q`
Expected: FAIL with `TypeError: RescueAttempt.__init__() got an unexpected keyword argument 'judge_pages'`.

- [ ] **Step 3: Implement the shown mode**

In `rescue_attempt.py`: add `from typing import TYPE_CHECKING`, and under `if TYPE_CHECKING:` import `from PySide6.QtWebEngineWidgets import QWebEngineView`. The import is type-only, so the hidden pass never loads the widgets module. Update the module docstring: "One visit of one walled PDF's address: hidden, or shown in the review queue window (#480, stages 2 and 3)." Add:

```python
class _ShownPage(QWebEnginePage):
    """The page of a visit a person sees.

    A link that opens a new window (``target=_blank``, the usual "Download
    PDF") opens here instead: QtWebEngine would otherwise drop it, and a PDF
    it leads to must be this visit's own download (``accept_download``).
    Scripts still cannot open windows without the person's click
    (``JavascriptCanOpenWindows`` stays off).
    """

    def createWindow(self, _type: QWebEnginePage.WebWindowType) -> QWebEnginePage:  # noqa: N802
        """Open the new window's page in this one.

        Args:
            _type: The kind of window asked for; every kind opens here.

        Returns:
            This page.
        """
        return self
```

Extend `__init__` (document both new arguments in the docstring; the class docstring gains: "A person's visit (``judge_pages=False``) ends only with a download or a renderer crash; leaving it is :meth:`abort`."):

```python
        parent: QObject | None = None,
        *,
        view: QWebEngineView | None = None,
        judge_pages: bool = True,
    ) -> None:
        ...
        self._view = view
        self._judge_pages = judge_pages
```

`start()`:

```python
    def start(self) -> None:
        """Load the address, in the view when there is one, and start waiting."""
        # Parentless, so it is deleted when this visit says, always before
        # the profile, as QtWebEngine requires (_release_page). A view
        # showing it falls back to a page of its own when it goes.
        page = QWebEnginePage(self._profile) if self._view is None else _ShownPage(self._profile)
        page.setAudioMuted(self._view is None)
        page.loadingChanged.connect(self._on_loading_changed)
        page.renderProcessTerminated.connect(self._on_render_process_terminated)
        self._page = page
        if self._view is not None:
            self._view.setPage(page)
        if self._judge_pages:
            self._timeout.start(self._timeout_ms)
        page.load(QUrl(self._address))
```

`_on_loading_changed`: in the `HttpStatusCodeDomain` branch, change the gone test to `if self._judge_pages and self._status in CHALLENGE_GONE_STATUSES:`. Before the load-failure test, add:

```python
        if not self._judge_pages:
            # A person's page: shown, never settled (D2)
            return
```

`_on_timeout`: add `or not self._judge_pages` to its first guard.

- [ ] **Step 4: Run the handler tests**

Run: `pytest tests/test_rescue_attempt_handlers.py -q`
Expected: PASS, the stage 2 tests included.

- [ ] **Step 5: Add the shown scenarios to the harness**

In `tests/browser_rescue_harness.py`: import `QWebEngineView` from `PySide6.QtWebEngineWidgets` after `prepare_web_engine()` (with `# noqa: E402`). Add `PERSON_WAIT_SECONDS = 2 * TIMEOUT_MS / 1000`. Then add:

```python
def shown_scenarios(app: QApplication, base: str, root: Path) -> dict[str, Any]:
    """Visits shown in a view: a person's, and one judged as a walk judges it."""
    profile = rescue_profile(root / "shown-profile")
    current: list[RescueAttempt] = []
    profile.downloadRequested.connect(lambda request: current[0].accept_download(request))
    view = QWebEngineView()
    view.show()
    results: dict[str, Any] = {}

    def person(name: str, path: str, wait: float) -> dict[str, Any]:
        out = root / "shown" / name
        out.mkdir(parents=True)
        verdicts: list[RescueVerdict] = []
        attempt = RescueAttempt(profile, base + path, out / "article.pdf", TIMEOUT_MS, STALL_MS,
                                MAX_PDF_BYTES, view=view, judge_pages=False)
        attempt.finished.connect(verdicts.append)
        current[:] = [attempt]
        attempt.start()
        shown = view.page() is not None and view.page().profile() is profile
        finished = run_until(app, lambda: bool(verdicts), wait)
        if not finished:
            attempt.abort()
        pump(app, 0.2)
        # A finished visit lets its page go with deleteLater; deliver that first
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        result = {
            "shown": shown,
            "finished": finished,
            "verdict": verdicts[0].to_dict() if verdicts else None,
            "saved": (out / "article.pdf").is_file(),
            "view_alive": view.page() is not None,
            "view_released": view.page().profile() is not profile,
        }
        attempt.deleteLater()
        return result

    results["pdf"] = person("pdf", "/pdf?shown", SCENARIO_LIMIT_SECONDS)
    # Neither a gone page nor an abstract ends a person's visit; leaving it does
    results["gone"] = person("gone", "/gone?shown", PERSON_WAIT_SECONDS)
    results["abstract"] = person("abstract", "/abstract?shown", PERSON_WAIT_SECONDS)

    # A new window opens in the same page (D3)
    page_probe = rescue_attempt_module._ShownPage(profile)
    results["new_window_here"] = page_probe.createWindow(
        QWebEnginePage.WebWindowType.WebBrowserTab) is page_probe
    Shiboken.delete(page_probe)

    # A walk's visit is judged as the hidden pass judges, in the view
    out = root / "shown" / "walk"
    out.mkdir(parents=True)
    verdicts: list[RescueVerdict] = []
    attempt = RescueAttempt(profile, base + "/walled?shown", out / "article.pdf", TIMEOUT_MS,
                            STALL_MS, MAX_PDF_BYTES, view=view, judge_pages=True)
    attempt.finished.connect(verdicts.append)
    current[:] = [attempt]
    attempt.start()
    results["walk_walled"] = {
        "finished": run_until(app, lambda: bool(verdicts), SCENARIO_LIMIT_SECONDS),
        "verdict": verdicts[0].to_dict() if verdicts else None,
    }
    attempt.deleteLater()

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    Shiboken.delete(view)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    Shiboken.delete(profile)
    return results
```

Import the module for the probe: `from bmlibrarian_lite.gui import rescue_attempt as rescue_attempt_module  # noqa: E402`. Add `"shown": shown_scenarios(app, base, root),` to `main()`'s output, after `"attempts"`.

Append to `tests/test_browser_rescue_engine.py`:

```python
def _shown(harness: dict[str, Any], name: str) -> dict[str, Any]:
    result: dict[str, Any] = harness["out"]["shown"][name]
    return result


def test_a_person_s_visit_shows_the_page_and_saves_its_pdf(harness: dict[str, Any]) -> None:
    """The page is shown in the view; the PDF it serves is saved; the view outlives the page."""
    result = _shown(harness, "pdf")
    assert result["shown"] and result["finished"]
    assert result["verdict"]["outcome"] == "pdf" and result["saved"]
    assert result["view_alive"] and result["view_released"]


@pytest.mark.parametrize("name", ["gone", "abstract"])
def test_a_person_s_page_is_left_unjudged_until_they_leave(harness: dict[str, Any], name: str) -> None:
    """No verdict past the hidden timeout; leaving releases the page from the view."""
    result = _shown(harness, name)
    assert not result["finished"] and result["verdict"] is None
    assert result["view_alive"] and result["view_released"]


def test_a_new_window_opens_in_the_person_s_page(harness: dict[str, Any]) -> None:
    """createWindow returns the page itself (D3)."""
    assert harness["out"]["shown"]["new_window_here"] is True


def test_a_walk_judges_the_shown_page(harness: dict[str, Any]) -> None:
    """A walk's visit is judged as the hidden pass's."""
    result = _shown(harness, "walk_walled")
    assert result["finished"]
    assert result["verdict"] == {"outcome": "challenge", "http_status": 403, "markers": ["cloudflare"]}
```

- [ ] **Step 6: Run the harness tests**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_browser_rescue_engine.py -q`
Expected: PASS. Then check that the harness's stderr holds no `Release of profile requested but WebEnginePage still not deleted`: `QT_QPA_PLATFORM=offscreen python tests/browser_rescue_harness.py 2>&1 >/dev/null | grep -c "Release of profile"` prints `0`. That stderr check is a test already (`test_quit_releases_the_page_before_the_profile`). Confirm it still covers the whole run.

- [ ] **Step 7: Commit**

```bash
git add src/bmlibrarian_lite/gui/rescue_attempt.py tests/test_rescue_attempt_handlers.py \
  tests/browser_rescue_harness.py tests/test_browser_rescue_engine.py
git commit -m "feat(rescue): a visit shown in a view, judged only by what a person downloads (#480)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The service runs shown visits

**Files:**
- Modify: `src/bmlibrarian_lite/gui/browser_rescue_service.py`
- Test: `tests/test_browser_rescue_service.py`; harness

**Interfaces:**
- Consumes: `RescueAttempt(..., view=, judge_pages=)` (Task 3).
- Produces, on `BrowserRescueService`:
  - `__init__(..., visible_timeout_ms: int = RESCUE_VISIBLE_TIMEOUT_MS, ...)`
  - Signals: `entry_changed = Signal(object)`, emitted with the `RescueEntry` after every applied verdict, hidden or shown. `shown_visit_started = Signal(object)`, with the entry. `shown_visit_ended = Signal(object, object)`, with the entry and its `RescueVerdict`, or `None` when it was left without one. `shown_visit_failed = Signal(object)`, with the entry whose page could not be opened; `shown_visit_ended(entry, None)` follows it.
  - `visit_shown(entry: RescueEntry, view: QWebEngineView) -> None`: a person's visit (D2). It replaces any shown visit and stops a walk.
  - `end_shown_visit() -> None`: leaves the shown visit with no verdict.
  - `shown_entry_id -> int | None` (property).
  - Private: `_ShownVisit` dataclass, `_ask_shown`, `_start_shown_when_free`, `_begin_shown`, `_on_shown_finished`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_browser_rescue_service.py`)

```python
from PySide6.QtCore import QObject, Signal

from bmlibrarian_lite.gui import browser_rescue_service as service_module


class _FakeAttempt(QObject):
    """Stands in for RescueAttempt: records how it was made and driven."""

    finished = Signal(object)
    made: list["_FakeAttempt"] = []

    def __init__(self, profile: Any, address: str, pdf_path: Path, timeout_ms: int, stall_ms: int,
                 max_pdf_bytes: int, parent: Any = None, *, view: Any = None, judge_pages: bool = True) -> None:
        super().__init__()
        self.address, self.timeout_ms, self.view, self.judge_pages = address, timeout_ms, view, judge_pages
        self.started = False
        self.aborted = False
        _FakeAttempt.made.append(self)

    def start(self) -> None:
        self.started = True

    def abort(self) -> None:
        self.aborted = True


@pytest.fixture
def shown(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, Any]]:
    """Fake attempts and profile; scheduled starts are recorded, not run."""
    _FakeAttempt.made = []
    monkeypatch.setattr(service_module, "RescueAttempt", _FakeAttempt)
    monkeypatch.setattr(BrowserRescueService, "_ensure_profile", lambda self: object())
    scheduled: list[tuple[int, Any]] = []
    monkeypatch.setattr(
        service_module, "QTimer",
        SimpleNamespace(singleShot=lambda delay, _ctx, fn: scheduled.append((delay, fn))),
    )
    return scheduled


def _started_service(tmp_path: Path, storage: _Storage) -> BrowserRescueService:
    """A service as start() leaves it, without its timers running."""
    service = _service(tmp_path, storage, [0.0])
    service._started = True
    return service


def _run_scheduled(scheduled: list[tuple[int, Any]]) -> None:
    """Run what was scheduled, as the event loop would."""
    while scheduled:
        _delay, fn = scheduled.pop(0)
        fn()


def test_a_person_s_visit_is_made_in_their_view_unjudged(qapp, tmp_path: Path, shown: list) -> None:
    """visit_shown makes one attempt, in the view, with no page judgement (D2)."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    view = object()
    started: list[Any] = []
    service.shown_visit_started.connect(started.append)
    service.visit_shown(_entry(), view)
    _run_scheduled(shown)
    (attempt,) = _FakeAttempt.made
    assert attempt.started and attempt.view is view and attempt.judge_pages is False
    assert service.shown_entry_id == 1
    assert len(started) == 1


def test_the_hidden_pass_takes_no_entry_while_a_shown_visit_is_asked_for(qapp, tmp_path: Path, shown: list) -> None:
    """While the window drives its view, the hidden pass pauses."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    service.visit_shown(_entry(), object())
    service._tick()
    assert storage.reads == 0


def test_a_shown_visit_waits_for_the_hidden_visit_under_way(qapp, tmp_path: Path, shown: list) -> None:
    """One visit at a time: the shown one starts when the hidden one ends (D1)."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    service._attempt = _FakeAttempt(None, "h", tmp_path, 0, 0, 0)  # a hidden visit under way
    _FakeAttempt.made = []
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    assert _FakeAttempt.made == []
    service._busy = True
    service._on_finished(_entry(), PDF)
    _run_scheduled(shown)
    assert len(_FakeAttempt.made) == 1 and _FakeAttempt.made[0].started


def test_a_shown_visit_s_verdict_is_recorded_announced_and_reported(qapp, tmp_path: Path, shown: list) -> None:
    """A person's PDF is saved as the hidden pass's is: recorded, rescued, entry_changed."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    rescued: list[Any] = []
    changed: list[Any] = []
    ended: list[tuple[Any, Any]] = []
    service.rescued.connect(rescued.append)
    service.entry_changed.connect(changed.append)
    service.shown_visit_ended.connect(lambda e, v: ended.append((e, v)))
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    _FakeAttempt.made[0].finished.emit(PDF)
    assert storage.writes == [(1, PDF)]
    assert len(rescued) == 1 and len(changed) == 1
    assert ended[0][1] == PDF
    assert service.shown_entry_id is None


def test_leaving_a_shown_visit_records_nothing(qapp, tmp_path: Path, shown: list) -> None:
    """The entry stays as it was; no attempt is counted (D2)."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    ended: list[tuple[Any, Any]] = []
    service.shown_visit_ended.connect(lambda e, v: ended.append((e, v)))
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    service.end_shown_visit()
    assert _FakeAttempt.made[0].aborted
    assert storage.writes == []
    assert ended[0][1] is None


def test_choosing_another_entry_leaves_the_first_without_a_verdict(qapp, tmp_path: Path, shown: list) -> None:
    """One visit at a time; the one left is aborted, not judged."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    other = SimpleNamespace(id=2, request=SimpleNamespace(host="b.example", address="https://b.example/2.pdf"))
    service.visit_shown(other, object())
    _run_scheduled(shown)
    first, second = _FakeAttempt.made
    assert first.aborted and second.started and service.shown_entry_id == 2
    assert storage.writes == []


def test_a_start_scheduled_for_an_entry_the_reader_left_does_nothing(qapp, tmp_path: Path, shown: list) -> None:
    """Review focus 4: a quick click-through starts only the last entry's visit."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    service.visit_shown(_entry(), object())
    other = SimpleNamespace(id=2, request=SimpleNamespace(host="b.example", address="https://b.example/2.pdf"))
    service.visit_shown(other, object())
    _run_scheduled(shown)
    assert [a.address for a in _FakeAttempt.made] == ["https://b.example/2.pdf"]


def test_a_shown_visit_that_cannot_start_ends_without_a_verdict(
    qapp, tmp_path: Path, shown: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No profile folder: logged, reported as ended, nothing recorded for a person's visit."""
    def no_profile(self: Any) -> Any:
        raise OSError("read-only")

    monkeypatch.setattr(BrowserRescueService, "_ensure_profile", no_profile)
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    ended: list[tuple[Any, Any]] = []
    failed: list[Any] = []
    service.shown_visit_ended.connect(lambda e, v: ended.append((e, v)))
    service.shown_visit_failed.connect(failed.append)
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    assert ended and ended[0][1] is None and len(failed) == 1
    assert storage.writes == [] and service.shown_entry_id is None


def test_a_shown_visit_waits_while_a_verdict_is_unsaved(qapp, tmp_path: Path, shown: list) -> None:
    """The next write would most likely fail too: no visit starts until the save succeeds."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    service._unsaved = (_entry(), PDF)
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    assert _FakeAttempt.made == []


def test_shutdown_aborts_a_shown_visit(qapp, tmp_path: Path, shown: list, no_flush: None) -> None:
    """Quit: the shown visit ends with no verdict, its entry as it was."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    service.shutdown()
    assert _FakeAttempt.made[0].aborted and storage.writes == []
```

Also add `entry_changed` to the existing hidden-pass rescue test. In `test_a_rescue_another_process_recorded_first_is_not_announced_again`, assert that nothing is emitted when `applied=False`, and add a control in which an applied verdict emits `entry_changed` once.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_browser_rescue_service.py -q`
Expected: FAIL with `AttributeError: 'BrowserRescueService' object has no attribute 'visit_shown'`.

- [ ] **Step 3: Implement shown visits**

In `browser_rescue_service.py`: add `from dataclasses import dataclass`, `RESCUE_VISIBLE_TIMEOUT_MS` to the constants import, and under `TYPE_CHECKING` `from PySide6.QtWebEngineWidgets import QWebEngineView`. Update the module docstring: "The browser rescue's visits, hidden and shown, on the GUI thread (#480, stages 2 and 3). … One visit at a time: the hidden pass's, or one the review queue window shows. While the window has a visit asked for, the hidden pass takes no entry."

```python
@dataclass
class _ShownVisit:
    """A visit the review queue window asked for.

    Attributes:
        entry: The entry shown.
        view: The window's view.
        judge_pages: Whether its page is judged (a walk) or a person's (D2).
        token: Which request this is; a start scheduled for an earlier one
            does nothing.
        attempt: The visit, once it has started.
    """

    entry: RescueEntry
    view: QWebEngineView
    judge_pages: bool
    token: int
    attempt: RescueAttempt | None = None
    scheduled: bool = False
```

New signals beside `rescued`, documented in the class docstring:

```python
    rescued = Signal(object)
    entry_changed = Signal(object)
    shown_visit_started = Signal(object)
    shown_visit_ended = Signal(object, object)
    shown_visit_failed = Signal(object)
```

In `__init__`, add the `visible_timeout_ms` argument and document it ("How long a walk's visit waits for a download"):

```python
        self._visible_timeout_ms = visible_timeout_ms
        self._shown: _ShownVisit | None = None
        self._shown_token = 0
```

Public API:

```python
    @property
    def shown_entry_id(self) -> int | None:
        """The entry the window has a visit asked for or under way.

        Returns:
            Its ID; ``None`` when the window shows none.
        """
        return self._shown.entry.id if self._shown is not None else None

    def visit_shown(self, entry: RescueEntry, view: QWebEngineView) -> None:
        """Show an entry's page to the reader, in the window's view.

        A person's visit (D2): judged only by what it downloads. It replaces
        any visit the window had and stops a walk. It starts once no other
        visit is under way, at its host's pacing slot.

        Args:
            entry: An open entry.
            view: The window's view.
        """
        self._ask_shown(entry, view, judge_pages=False)

    def end_shown_visit(self) -> None:
        """Leave the window's visit with no verdict: its entry stays as it was."""
        shown, self._shown = self._shown, None
        if shown is None:
            return
        attempt = shown.attempt
        if attempt is not None:
            if self._attempt is attempt:
                self._attempt = None
            attempt.abort()
            attempt.deleteLater()
        self.shown_visit_ended.emit(shown.entry, None)
        self.wake()

    def _ask_shown(self, entry: RescueEntry, view: QWebEngineView, *, judge_pages: bool) -> None:
        """Replace the window's visit with one of ``entry``.

        Args:
            entry: The entry.
            view: The window's view.
            judge_pages: Whether its page is judged (a walk).
        """
        if self._stopped:
            return
        self.end_shown_visit()
        self._shown_token += 1
        self._shown = _ShownVisit(entry, view, judge_pages, self._shown_token)
        self._start_shown_when_free()

    def _start_shown_when_free(self) -> None:
        """Schedule the window's visit, once no visit is under way and no verdict waits to be saved."""
        shown = self._shown
        if shown is None or shown.scheduled or self._stopped:
            return
        if self._attempt is not None or self._unsaved is not None:
            return
        shown.scheduled = True
        token = shown.token
        try:
            delay = rescue_delay_ms(shown.entry.request.host)
        except Exception:
            logger.exception("No pacing slot for %s; opening it now", shown.entry.request.host)
            delay = 0
        QTimer.singleShot(delay, self, lambda: self._begin_shown(token))

    def _begin_shown(self, token: int) -> None:
        """Start the window's visit, unless the reader has moved on.

        A visit that cannot start is logged and reported as ended with no
        verdict: a person's visit counts no attempt.

        Args:
            token: Which request scheduled this start.
        """
        shown = self._shown
        if self._stopped or shown is None or shown.token != token or self._attempt is not None:
            return
        try:
            attempt = RescueAttempt(
                self._ensure_profile(), shown.entry.request.address, self._pdf_path_for(shown.entry),
                self._visible_timeout_ms, self._stall_ms, self._max_pdf_bytes, parent=self,
                view=shown.view, judge_pages=shown.judge_pages,
            )
            attempt.finished.connect(lambda verdict: self._on_shown_finished(token, verdict))
            shown.attempt = attempt
            self._attempt = attempt
            logger.info("Showing a walled PDF at %s in the review queue", shown.entry.request.host)
            attempt.start()
        except Exception:
            logger.exception("The review queue could not open the page at %s", shown.entry.request.host)
            self.end_shown_visit()
            self.shown_visit_failed.emit(shown.entry)
            return
        self.shown_visit_started.emit(shown.entry)

    def _on_shown_finished(self, token: int, verdict: RescueVerdict) -> None:
        """Record the window's visit's verdict, and say it ended.

        Args:
            token: Which request the visit was for.
            verdict: What it concluded.
        """
        shown = self._shown
        if shown is None or shown.token != token:
            return
        self._shown = None
        attempt, self._attempt = self._attempt, None
        if attempt is not None:
            attempt.deleteLater()
        logger.info("The review queue's visit to %s: %s", shown.entry.request.host, verdict.outcome.value)
        try:
            self._save(shown.entry, verdict)
        finally:
            self.shown_visit_ended.emit(shown.entry, verdict)
            self.wake()
```

Note on `end_shown_visit` inside `_begin_shown`'s `except`: when the attempt was made but `start()` raised, `end_shown_visit` aborts it and clears `self._attempt`. When construction raised, `shown.attempt` is `None`. Either way, `shown_visit_ended(entry, None)` is emitted, then `shown_visit_failed(entry)`. The window says "could not be opened" only on the latter, since `ended(entry, None)` also means the reader left the visit.

Hook into the hidden pass:
- `_next_entry`: after the unsaved-save block, add `if self._shown is not None: return None`.
- `_visit`: change the first guard to `if self._stopped or self._shown is not None: return`, *inside* the `try` so the `finally` frees `_busy` (move it below `started = False; try:`).
- `_on_finished` (hidden): after the `try/finally`, call `self._start_shown_when_free()` before `if saved: self.wake()`.
- `_tick`: at its end (after `finally`), call `self._start_shown_when_free()`, so a shown visit held by an unsaved verdict starts once the retry saves it.
- `_save`: on success, when `recorded.applied and updated is not None`, emit `self.entry_changed.emit(updated)`, whatever the state, and at quit too (the window may still be listening). Emit it before the `rescued` check, which stays as it is.
- `shutdown()`: after `self._stopped = True`, add `self._shown = None`. The attempt under way, hidden or shown, is `self._attempt` and is aborted as before.

- [ ] **Step 4: Run the service tests**

Run: `pytest tests/test_browser_rescue_service.py -q`
Expected: PASS, the stage 2 tests included.

- [ ] **Step 5: Add a real service scenario to the harness**

In `service_scenarios`, before the quit block, add a person's visit through the real service. Leave the `doc-f` quit block as the last step:

```python
    # A person's visit through the service: shown in a view, its PDF recorded
    view = QWebEngineView()
    view.show()
    storage.enqueue_rescues([request("doc-g", "/walled?g")])
    run_until(app, lambda: entry_of("?g").state is RescueState.NEEDS_PERSON, SCENARIO_LIMIT_SECONDS)
    # Point the entry's address at a PDF, as a person's tick would clear the wall
    walled_g = entry_of("?g")
    person_entry = replace(walled_g, request=replace(walled_g.request, address=base + "/pdf?g"))
    ended: list[Any] = []
    service.shown_visit_ended.connect(lambda e, v: ended.append(v))
    service.visit_shown(person_entry, view)
    result["shown"] = {
        "ended": run_until(app, lambda: bool(ended), SCENARIO_LIMIT_SECONDS),
        "verdict": ended[0].to_dict() if ended and ended[0] else None,
        "state": entry_of("?g").state.value,
        "rescued": "doc-g" in rescued,
    }

    service.end_shown_visit()
```

Leave the `doc-f` block (the hidden pass quit mid-visit) as the last step of `service_scenarios`, then `Shiboken.delete(view)`. Import `replace` from `dataclasses`.

Quitting during a shown visit gets a scenario of its own, with its own storage and service, and with the hidden pass off. A shown visit holds the hidden pass, so in `service_scenarios` it would keep `doc-f` from ever being visited:

```python
def shown_quit_scenario(app: QApplication, base: str, root: Path) -> dict[str, Any]:
    """Quit while a person's visit is shown: the entry is unchanged, the page goes first."""
    config = LiteConfig()
    config.storage.data_dir = root / "shown-quit-data"
    config.discovery.browser_rescue_hidden = False
    storage = LiteStorage(config)
    storage.enqueue_rescues([RescueRequest("doc-h", "10.1/doc-h", None, "Title doc-h",
                                           base + "/abstract?h", "127.0.0.1", SERVICE_UNPAYWALL_PDF)])
    service = BrowserRescueService(
        storage, config, root / "shown-quit-profile",
        pdf_path_for=lambda e: root / "shown-quit.pdf",
        timeout_ms=TIMEOUT_MS, stall_ms=STALL_MS, poll_ms=POLL_MS, max_pdf_bytes=MAX_PDF_BYTES,
    )
    view = QWebEngineView()
    view.show()
    service.start()
    (entry,) = storage.rescue_entries([RescueState.PENDING])
    service.visit_shown(entry, view)
    visiting = run_until(app, lambda: "/abstract?h" in Handler.visits, SCENARIO_LIMIT_SECONDS)
    service.shutdown()
    pump(app, 0.5)
    Shiboken.delete(view)
    after = storage.rescue_entry(entry.id)
    return {"visiting": visiting, "state": after.state.value if after else None,
            "attempts": after.attempts if after else None}
```

Add `"shown_quit": shown_quit_scenario(app, base, root),` to `main()`'s output. The engine tests' stderr check for the profile-release warning covers the whole harness run, this scenario included.

Append to `tests/test_browser_rescue_engine.py`:

```python
def test_a_person_s_visit_through_the_service_rescues_the_entry(harness: dict[str, Any]) -> None:
    """visit_shown records the person's PDF on a needs_person entry and announces it."""
    shown = harness["out"]["service"]["shown"]
    assert shown["ended"] and shown["verdict"]["outcome"] == "pdf"
    assert shown["state"] == "rescued" and shown["rescued"]


def test_quit_during_a_shown_visit_releases_the_page_first(harness: dict[str, Any]) -> None:
    """Review focus 1: the shown entry stays as it was; no profile-release warning."""
    quit_ = harness["out"]["shown_quit"]
    assert quit_["visiting"]
    assert (quit_["state"], quit_["attempts"]) == ("pending", 0)
    assert "Release of profile requested" not in harness["stderr"]
```

- [ ] **Step 6: Run the harness tests**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_browser_rescue_engine.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/bmlibrarian_lite/gui/browser_rescue_service.py tests/test_browser_rescue_service.py \
  tests/browser_rescue_harness.py tests/test_browser_rescue_engine.py
git commit -m "feat(rescue): the service shows a visit in the window's view, one visit at a time (#480)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Walk the queue

**Files:**
- Modify: `src/bmlibrarian_lite/gui/browser_rescue_service.py`
- Test: `tests/test_browser_rescue_service.py`

**Interfaces:**
- Consumes: `_ask_shown(..., judge_pages=True)` (Task 4); `LiteStorage.rescue_entry` (Task 2).
- Produces: `walk(entry_ids: Sequence[int], view: QWebEngineView) -> None`, `stop_walk() -> None`, `walking -> bool` (property), and `walk_ended = Signal()`.

- [ ] **Step 1: Write the failing tests** (append; reuse `shown`, `_FakeAttempt`, `_run_scheduled`)

```python
class _WalkStorage(_Storage):
    """A queue whose entries are read by ID; ``states`` decides which are still open."""

    def __init__(self, states: dict[int, RescueState]) -> None:
        super().__init__(None, failures=0)
        self.states = states

    def rescue_entry(self, entry_id: int) -> Any:
        state = self.states.get(entry_id)
        if state is None:
            return None
        address = f"https://h{entry_id}.example/{entry_id}.pdf"
        return SimpleNamespace(id=entry_id, state=state,
                               request=SimpleNamespace(host=f"h{entry_id}.example", address=address))

    def record_rescue_verdict(self, entry_id: int, verdict: RescueVerdict) -> RecordedVerdict:
        self.writes.append((entry_id, verdict))
        state = RescueState.RESCUED if verdict.outcome is RescueOutcome.PDF else RescueState.NEEDS_PERSON
        self.states[entry_id] = state
        return RecordedVerdict(SimpleNamespace(id=entry_id, state=state,  # type: ignore[arg-type]
                                               request=self.rescue_entry(entry_id).request), applied=True)


def test_a_walk_visits_each_entry_in_turn_judging_its_page(qapp, tmp_path: Path, shown: list) -> None:
    """Each entry in the order given, with the visible timeout and page judgement."""
    storage = _WalkStorage({1: RescueState.NEEDS_PERSON, 2: RescueState.NEEDS_PERSON})
    service = _started_service(tmp_path, storage)
    service.walk([1, 2], object())
    _run_scheduled(shown)
    first = _FakeAttempt.made[0]
    assert first.judge_pages is True and first.timeout_ms == service._visible_timeout_ms
    first.finished.emit(RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare",)))
    _run_scheduled(shown)
    assert [a.address for a in _FakeAttempt.made] == [
        "https://h1.example/1.pdf", "https://h2.example/2.pdf"
    ]
    assert service.walking


def test_a_walk_skips_an_entry_settled_meanwhile(qapp, tmp_path: Path, shown: list) -> None:
    """Review focus 5: re-read before each visit; one no longer open, or gone, is skipped."""
    storage = _WalkStorage({1: RescueState.DISMISSED, 3: RescueState.NEEDS_PERSON})
    service = _started_service(tmp_path, storage)
    service.walk([1, 2, 3], object())
    _run_scheduled(shown)
    assert [a.address for a in _FakeAttempt.made] == ["https://h3.example/3.pdf"]


def test_a_walk_ends_by_itself_after_the_last_entry(qapp, tmp_path: Path, shown: list) -> None:
    """walk_ended is emitted once the list is done; the hidden pass may run again."""
    storage = _WalkStorage({1: RescueState.NEEDS_PERSON})
    service = _started_service(tmp_path, storage)
    ended: list[bool] = []
    service.walk_ended.connect(lambda: ended.append(True))
    service.walk([1], object())
    _run_scheduled(shown)
    _FakeAttempt.made[0].finished.emit(RescueVerdict(RescueOutcome.NO_PDF, 200))
    assert ended == [True] and not service.walking


def test_stopping_a_walk_aborts_its_visit(qapp, tmp_path: Path, shown: list) -> None:
    """Stop: the visit under way ends with no verdict, and no further entry is visited."""
    storage = _WalkStorage({1: RescueState.NEEDS_PERSON, 2: RescueState.NEEDS_PERSON})
    service = _started_service(tmp_path, storage)
    ended: list[bool] = []
    service.walk_ended.connect(lambda: ended.append(True))
    service.walk([1, 2], object())
    _run_scheduled(shown)
    service.stop_walk()
    _run_scheduled(shown)
    assert _FakeAttempt.made[0].aborted and len(_FakeAttempt.made) == 1
    assert storage.writes == [] and ended == [True]


def test_choosing_an_entry_stops_the_walk(qapp, tmp_path: Path, shown: list) -> None:
    """The reader picking an entry takes the view over: the walk stops."""
    storage = _WalkStorage({1: RescueState.NEEDS_PERSON, 2: RescueState.NEEDS_PERSON})
    service = _started_service(tmp_path, storage)
    service.walk([1, 2], object())
    _run_scheduled(shown)
    service.visit_shown(storage.rescue_entry(2), object())
    _run_scheduled(shown)
    assert not service.walking
    assert _FakeAttempt.made[-1].judge_pages is False


def test_a_queue_that_cannot_be_read_skips_that_entry(qapp, tmp_path: Path, shown: list,
                                                      caplog: pytest.LogCaptureFixture) -> None:
    """A read failure is logged and the walk goes on."""
    storage = _WalkStorage({2: RescueState.NEEDS_PERSON})
    original = storage.rescue_entry

    def flaky(entry_id: int) -> Any:
        if entry_id == 1:
            raise sqlite3.OperationalError("database is locked")
        return original(entry_id)

    storage.rescue_entry = flaky  # type: ignore[method-assign]
    service = _started_service(tmp_path, storage)
    service.walk([1, 2], object())
    _run_scheduled(shown)
    assert [a.address for a in _FakeAttempt.made] == ["https://h2.example/2.pdf"]
    assert "could not be read" in caplog.text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_browser_rescue_service.py -q -k walk`
Expected: FAIL with `AttributeError: 'BrowserRescueService' object has no attribute 'walk'`.

- [ ] **Step 3: Implement the walk**

Add `from collections import deque`, `OPEN_RESCUE_STATES` to the rules import, `walk_ended = Signal()`, and in `__init__` `self._walk: deque[int] | None = None` and `self._walk_view: QWebEngineView | None = None`.

```python
    @property
    def walking(self) -> bool:
        """Whether *Walk the queue* is running.

        Returns:
            ``True`` from :meth:`walk` until its last entry or :meth:`stop_walk`.
        """
        return self._walk is not None

    def walk(self, entry_ids: Sequence[int], view: QWebEngineView) -> None:
        """Visit each entry in turn in the window's view, unattended.

        Each page is judged as the hidden pass judges it, waiting up to
        ``visible_timeout_ms`` for a PDF: with the window shown this passed
        the Cloudflare publishers 9 times in 9 (spike), so only walls that
        need a person's tick are left. Each entry is read again before its
        visit, and one no longer open is skipped.

        Args:
            entry_ids: The entries, in the order to visit them.
            view: The window's view.
        """
        if self._stopped:
            return
        self.end_shown_visit()
        self._walk = deque(entry_ids)
        self._walk_view = view
        self._walk_next()

    def stop_walk(self) -> None:
        """Stop *Walk the queue*; the visit under way ends with no verdict."""
        if self._walk is None:
            return
        self._walk = None
        self._walk_view = None
        self.end_shown_visit()
        self.walk_ended.emit()

    def _walk_next(self) -> None:
        """Visit the walk's next open entry, or end the walk."""
        walk, view = self._walk, self._walk_view
        if walk is None or view is None:
            return
        while walk:
            entry_id = walk.popleft()
            try:
                entry = self._storage.rescue_entry(entry_id)
            except (sqlite3.Error, SQLiteError) as e:
                logger.error("Walled PDF %s could not be read for the walk: %s", entry_id, e)
                continue
            if entry is None or entry.state not in OPEN_RESCUE_STATES:
                continue
            self._ask_shown(entry, view, judge_pages=True)
            return
        self._walk = None
        self._walk_view = None
        self.walk_ended.emit()
```

`visit_shown` (the reader's own choice) stops a walk first:

```python
    def visit_shown(self, entry: RescueEntry, view: QWebEngineView) -> None:
        ...
        if self._walk is not None:
            self.stop_walk()
        self._ask_shown(entry, view, judge_pages=False)
```

`_ask_shown` calls `end_shown_visit()`, which `wake()`s the hidden pass. During a walk that is harmless, because `_next_entry` returns `None` while `self._shown` is set and `_ask_shown` sets it straight after.

Move the walk forward after each verdict. In `_on_shown_finished`'s `finally`, replace `self.wake()` with:

```python
            if self._walk is not None:
                self._walk_next()
            else:
                self.wake()
```

In `_begin_shown`'s `except` branch, after `self.shown_visit_failed.emit(...)`, add `self._walk_next()` (a walk goes on past a page that could not open). `shutdown()`: set `self._walk = None` and `self._walk_view = None` beside `self._shown = None`. No `walk_ended` is emitted at quit.

- [ ] **Step 4: Run the service tests**

Run: `pytest tests/test_browser_rescue_service.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bmlibrarian_lite/gui/browser_rescue_service.py tests/test_browser_rescue_service.py
git commit -m "feat(rescue): walk the queue in the shown view, re-reading each entry first (#480)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The review queue window

**Files:**
- Create: `src/bmlibrarian_lite/gui/rescue_view.py`
- Create: `src/bmlibrarian_lite/gui/review_queue_dialog.py`
- Modify: `src/bmlibrarian_lite/resources/styles/stylesheet_generator.py` (rules for `reviewQueueHelp` and `reviewQueueStatus`, following the existing secondary/hint label rules; check what the generator already offers before adding any)
- Test: `tests/test_review_queue_dialog.py` (new)

**Interfaces:**
- Consumes: `awaiting_reader_states`, `fetchable_host`, `dismiss_entry`, `OPEN_RESCUE_STATES` (Task 1); `LiteStorage.rescue_entries` (stage 2); the service's `visit_shown`, `end_shown_visit`, `walk`, `stop_walk`, `walking`, `shown_entry_id`, and its signals `shown_visit_started`, `shown_visit_ended`, `entry_changed` and `walk_ended` (Tasks 4–5).
- Produces:
  - `gui/rescue_view.py`: `make_rescue_view(parent: QWidget) -> QWebEngineView`.
  - `gui/review_queue_dialog.py`: `class QueueFilter(Enum)` with `WAITING`, `OPEN`, `ALL`; `class ReviewQueueDialog(QDialog)`, `__init__(storage: LiteStorage, service: BrowserRescueService | None, make_view: Callable[[QWidget], QWidget] | None, hidden_pass_runs: Callable[[], bool], parent: QWidget | None = None)`; signal `queue_changed = Signal()`; `refresh() -> None`, `focus_entry(entry_id: int) -> None`. The module must not import QtWebEngine.

- [ ] **Step 1: Write the failing tests** (`tests/test_review_queue_dialog.py`)

```python
"""The review queue window (#480, stage 3).

Built in the suite's QApplication with a fake service and a plain widget for
its view; slots are called directly and the application is never pumped
(#516, #517). The real view runs in the harness.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from bmlibrarian_lite.browser_rescue import RescueOutcome, RescueRequest, RescueState, RescueVerdict
from bmlibrarian_lite.browser_rescue_text import REVIEW_QUEUE_NO_BROWSER
from bmlibrarian_lite.config import LiteConfig
from bmlibrarian_lite.constants import SERVICE_UNPAYWALL_PDF
from bmlibrarian_lite.gui import review_queue_dialog as dialog_module
from bmlibrarian_lite.gui.review_queue_dialog import QueueFilter, ReviewQueueDialog
from bmlibrarian_lite.storage import LiteStorage

CHALLENGE = RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare",))


@pytest.fixture
def qapp():
    """The suite's QApplication; never pumped."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class FakeService(QObject):
    """The service's surface the window uses, recording calls."""

    shown_visit_started = Signal(object)
    shown_visit_ended = Signal(object, object)
    shown_visit_failed = Signal(object)
    entry_changed = Signal(object)
    walk_ended = Signal()
    rescued = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, Any]] = []
        self.walking = False
        self.shown_entry_id: int | None = None

    def visit_shown(self, entry: Any, view: Any) -> None:
        self.calls.append(("visit", entry.id))
        self.shown_entry_id = entry.id

    def end_shown_visit(self) -> None:
        self.calls.append(("end", self.shown_entry_id))
        self.shown_entry_id = None

    def walk(self, entry_ids: Any, view: Any) -> None:
        self.calls.append(("walk", list(entry_ids)))
        self.walking = True

    def stop_walk(self) -> None:
        self.calls.append(("stop_walk", None))
        self.walking = False


def _request(doc: str, n: int) -> RescueRequest:
    return RescueRequest(doc, f"10.1/{doc}", None, f"Title {doc}", f"https://h{n}.example/{n}.pdf",
                         f"h{n}.example", SERVICE_UNPAYWALL_PDF)


@pytest.fixture
def storage(tmp_path: Path) -> LiteStorage:
    """A queue: doc-a needs a person, doc-b is pending, doc-c has no PDF."""
    config = LiteConfig()
    config.storage.data_dir = tmp_path
    storage = LiteStorage(config)
    storage.enqueue_rescues([_request("doc-a", 1), _request("doc-b", 2), _request("doc-c", 3)])
    a, _b, c = storage.rescue_entries([RescueState.PENDING])
    storage.record_rescue_verdict(a.id, CHALLENGE)
    storage.record_rescue_verdict(c.id, RescueVerdict(RescueOutcome.NO_PDF, 200))
    return storage


def _dialog(storage: LiteStorage, service: Any, hidden: bool = True) -> ReviewQueueDialog:
    make_view = (lambda parent: QWidget(parent)) if service is not None else None
    return ReviewQueueDialog(storage, service, make_view, lambda: hidden)


def _titles(dialog: ReviewQueueDialog) -> list[str]:
    return [dialog.table.item(r, 0).text() for r in range(dialog.table.rowCount())]


def test_the_window_never_imports_qtwebengine() -> None:
    """Importing it in a fresh interpreter loads no QtWebEngine module."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys, bmlibrarian_lite.gui.review_queue_dialog; "
         "print(any(m.startswith('PySide6.QtWebEngine') for m in sys.modules))"],
        capture_output=True, text=True, timeout=120,
        env={**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False"


def test_by_default_it_lists_what_waits_for_the_reader(qapp, storage: LiteStorage) -> None:
    """With the hidden pass on, only needs_person (D4)."""
    dialog = _dialog(storage, FakeService())
    assert _titles(dialog) == ["Title doc-a"]
    dialog.deleteLater()


def test_with_the_hidden_pass_off_pending_entries_wait_for_the_reader_too(qapp, storage: LiteStorage) -> None:
    """No hidden pass: pending is the reader's too, labelled so."""
    dialog = _dialog(storage, FakeService(), hidden=False)
    assert _titles(dialog) == ["Title doc-a", "Title doc-b"]
    assert dialog.table.item(1, 3).text() == "Waiting (the hidden browser is off)"
    dialog.deleteLater()


def test_the_filter_shows_the_rest(qapp, storage: LiteStorage) -> None:
    """All entries, with host, who named them, state and last verdict."""
    dialog = _dialog(storage, FakeService())
    dialog.set_filter(QueueFilter.ALL)
    assert len(_titles(dialog)) == 3
    row = _titles(dialog).index("Title doc-c")
    assert [dialog.table.item(row, c).text() for c in range(1, 5)] == [
        "h3.example", "Unpaywall", "No PDF on the page", "The page served no PDF (HTTP 200)"
    ]
    dialog.deleteLater()


def test_selecting_an_open_entry_shows_its_page(qapp, storage: LiteStorage) -> None:
    """A person's visit of the selected entry, in the window's view."""
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.table.selectRow(0)
    assert service.calls[-1][0] == "visit"
    dialog.deleteLater()


def test_selecting_a_settled_entry_visits_nothing(qapp, storage: LiteStorage) -> None:
    """D6: its verdict is shown, and any visit ends."""
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.set_filter(QueueFilter.ALL)
    service.calls.clear()
    dialog.table.selectRow(_titles(dialog).index("Title doc-c"))
    assert all(name != "visit" for name, _ in service.calls)
    assert service.calls[-1][0] == "end"
    assert not dialog.dismiss_button.isEnabled()
    dialog.deleteLater()


def test_dismissing_ends_the_visit_and_stores_it(qapp, storage: LiteStorage) -> None:
    """Dismiss: the entry's visit ends first, then it is set aside and the queue says it changed."""
    service = FakeService()
    dialog = _dialog(storage, service)
    changed: list[bool] = []
    dialog.queue_changed.connect(lambda: changed.append(True))
    dialog.table.selectRow(0)
    dialog._dismiss_selected()
    assert service.calls[-1][0] == "end"
    assert storage.count_rescue_entries([RescueState.DISMISSED]) == 1
    assert changed == [True] and _titles(dialog) == []
    dialog.deleteLater()


def test_a_dismissal_that_cannot_be_stored_is_said(qapp, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    """Golden rule 8: the reader is told, not only the log."""
    dialog = _dialog(storage, FakeService())
    dialog.table.selectRow(0)
    monkeypatch.setattr(dialog_module, "dismiss_entry", lambda _q, _i: None)
    monkeypatch.setattr(storage, "rescue_entry", lambda _i: None)
    dialog._dismiss_selected()
    assert dialog.status_label.text() == dialog_module.REVIEW_QUEUE_UNWRITABLE
    dialog.deleteLater()


@pytest.mark.parametrize("address, opened", [("https://h1.example/1.pdf", True), ("javascript:alert(1)", False)])
def test_only_a_web_address_opens_in_the_system_browser(
    qapp, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch, address: str, opened: bool
) -> None:
    """A stored address is untrusted (golden rule 1)."""
    urls: list[str] = []
    monkeypatch.setattr(dialog_module.QDesktopServices, "openUrl", lambda url: urls.append(url.toString()))
    dialog = _dialog(storage, FakeService())
    dialog._open_in_system_browser(address)
    assert bool(urls) is opened
    dialog.deleteLater()


def test_a_rescue_moves_on_to_the_next_waiting_entry(qapp, storage: LiteStorage) -> None:
    """When the person's PDF arrives, the next entry opens."""
    b = storage.rescue_entries_for_document("doc-b")[0]
    storage.record_rescue_verdict(b.id, CHALLENGE)  # two entries now need the reader
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.table.selectRow(0)
    first = service.calls[-1][1]
    storage.record_rescue_verdict(first, RescueVerdict(RescueOutcome.PDF))
    entry = storage.rescue_entry(first)
    service.shown_visit_ended.emit(entry, RescueVerdict(RescueOutcome.PDF))
    assert dialog.selected_entry_id() not in (None, first)
    assert service.calls[-1] == ("visit", dialog.selected_entry_id())
    dialog.deleteLater()


def test_walk_the_queue_passes_the_waiting_entries_in_order(qapp, storage: LiteStorage) -> None:
    """The listed waiting entries, top to bottom; the button stops it; walk_ended resets it."""
    service = FakeService()
    dialog = _dialog(storage, service, hidden=False)
    dialog._toggle_walk()
    ids = [e.id for e in storage.rescue_entries([RescueState.NEEDS_PERSON, RescueState.PENDING])]
    assert service.calls[-1] == ("walk", ids)
    assert dialog.walk_button.text() == "Stop walking"
    dialog._toggle_walk()
    assert service.calls[-1] == ("stop_walk", None)
    service.walk_ended.emit()
    assert dialog.walk_button.text() == "Walk the queue"
    dialog.deleteLater()


def test_without_a_browser_the_window_still_lists_and_dismisses(qapp, storage: LiteStorage) -> None:
    """D12: a note instead of the view; dismiss works; walking is off."""
    dialog = _dialog(storage, None)
    assert dialog.view is None
    assert REVIEW_QUEUE_NO_BROWSER in dialog.view_note.text()
    assert not dialog.walk_button.isEnabled()
    dialog.table.selectRow(0)
    dialog._dismiss_selected()
    assert storage.count_rescue_entries([RescueState.DISMISSED]) == 1
    dialog.deleteLater()


def test_closing_the_window_ends_the_visit_and_the_walk(qapp, storage: LiteStorage) -> None:
    """Review focus 1: nothing keeps running behind a closed window."""
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.table.selectRow(0)
    service.walking = True
    dialog.done(0)
    assert ("stop_walk", None) in service.calls
    assert service.calls[-1][0] == "end"
    # The close box goes through reject(), which reaches done()
    service.calls.clear()
    dialog.reject()
    assert service.calls[-1][0] == "end"
    dialog.deleteLater()


def test_focus_entry_selects_it_even_when_filtered_out(qapp, storage: LiteStorage) -> None:
    """The interrogation tab's offer opens the window on its entry."""
    dialog = _dialog(storage, FakeService())
    c = storage.rescue_entries_for_document("doc-c")[0]
    dialog.focus_entry(c.id)
    assert dialog.current_filter is QueueFilter.ALL
    assert dialog.selected_entry_id() == c.id
    dialog.deleteLater()


def test_a_page_that_cannot_open_is_said_but_leaving_one_is_not(qapp, storage: LiteStorage) -> None:
    """shown_visit_failed says so; shown_visit_ended(None) alone (the reader left) says nothing."""
    service = FakeService()
    dialog = _dialog(storage, service)
    dialog.table.selectRow(0)
    entry = storage.rescue_entry(dialog.selected_entry_id())
    service.shown_visit_ended.emit(entry, None)
    assert dialog.status_label.text() != dialog_module.SHOWN_VISIT_NOT_STARTED
    service.shown_visit_failed.emit(entry)
    assert dialog.status_label.text() == dialog_module.SHOWN_VISIT_NOT_STARTED
    dialog.deleteLater()


def test_a_queue_that_cannot_be_read_is_said(qapp, storage: LiteStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    """The list is left as it was and the reader is told."""
    import sqlite3

    dialog = _dialog(storage, FakeService())

    def fail(_states: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(storage, "rescue_entries", fail)
    dialog.refresh()
    assert dialog.status_label.text() == dialog_module.REVIEW_QUEUE_UNREADABLE
    assert _titles(dialog) == ["Title doc-a"]
    dialog.deleteLater()


def test_a_verdict_elsewhere_refreshes_the_list(qapp, storage: LiteStorage) -> None:
    """entry_changed from the hidden pass updates what the window shows."""
    service = FakeService()
    dialog = _dialog(storage, service)
    b = storage.rescue_entries_for_document("doc-b")[0]
    storage.record_rescue_verdict(b.id, CHALLENGE)
    service.entry_changed.emit(storage.rescue_entry(b.id))
    assert "Title doc-b" in _titles(dialog)
    dialog.deleteLater()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_review_queue_dialog.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'bmlibrarian_lite.gui.review_queue_dialog'`.

- [ ] **Step 3: Implement `gui/rescue_view.py`**

```python
"""The review queue window's embedded view (#480, stage 3).

The only module that imports QtWebEngineWidgets. It is loaded only once the
hidden browser's service started, so the window, and importing the app,
never load QtWebEngine.
"""

from __future__ import annotations

from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget


def make_rescue_view(parent: QWidget) -> QWebEngineView:
    """A view for the window; the service puts each visit's page into it.

    Args:
        parent: The window.

    Returns:
        The view, showing nothing until a visit starts.
    """
    view = QWebEngineView(parent)
    view.setObjectName("rescueView")
    return view
```

- [ ] **Step 4: Implement `gui/review_queue_dialog.py`**

```python
"""The browser rescue's review queue window (#480, stage 3).

Lists the walled PDFs (title, host, who named the address, state, last
verdict) and shows the selected entry's page in the built-in browser, where
the reader can pass a robot check the hidden pass could not. Every visit is
the service's (``BrowserRescueService``), one at a time: this window only
asks. It never imports QtWebEngine: its view comes from ``make_view``.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from enum import Enum, IntEnum
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..browser_rescue import (
    OPEN_RESCUE_STATES,
    RescueEntry,
    RescueOutcome,
    RescueState,
    RescueVerdict,
    awaiting_reader_states,
    dismiss_entry,
    fetchable_host,
)
from ..browser_rescue_text import (
    REVIEW_QUEUE_HELP,
    REVIEW_QUEUE_NO_BROWSER,
    REVIEW_QUEUE_UNREADABLE,
    REVIEW_QUEUE_UNWRITABLE,
    SHOWN_VISIT_LOADING,
    SHOWN_VISIT_NOT_STARTED,
    SHOWN_VISIT_OPENING,
    WALK_RUNNING,
    retry_text,
    service_label,
    state_label,
    verdict_text,
)
from ..constants import REVIEW_QUEUE_HEIGHT_LINES, REVIEW_QUEUE_WIDTH_CHARS
from ..exceptions import SQLiteError

if TYPE_CHECKING:
    from ..storage import LiteStorage
    from .browser_rescue_service import BrowserRescueService

logger = logging.getLogger(__name__)

WINDOW_TITLE = "Walled PDFs"
WALK_LABEL = "Walk the queue"
STOP_WALK_LABEL = "Stop walking"


class QueueFilter(Enum):
    """Which entries the list shows."""

    WAITING = "Waiting for you"
    OPEN = "Not yet settled"
    ALL = "All"


class _Column(IntEnum):
    """The list's columns."""

    TITLE = 0
    HOST = 1
    NAMED_BY = 2
    STATE = 3
    VERDICT = 4


_HEADERS = {
    _Column.TITLE: "Title",
    _Column.HOST: "Host",
    _Column.NAMED_BY: "Named by",
    _Column.STATE: "State",
    _Column.VERDICT: "Last visit",
}


class ReviewQueueDialog(QDialog):
    """The review queue window: non-modal, one per main window.

    Signals:
        queue_changed: The reader changed the queue here (a dismissal);
            verdicts arrive through the service's ``entry_changed``.
    """

    queue_changed = Signal()

    def __init__(
        self,
        storage: LiteStorage,
        service: BrowserRescueService | None,
        make_view: Callable[[QWidget], QWidget] | None,
        hidden_pass_runs: Callable[[], bool],
        parent: QWidget | None = None,
    ) -> None:
        """Build the window and list the waiting entries.

        Args:
            storage: The queue.
            service: The browser rescue; ``None`` when QtWebEngine is
                unavailable (D12).
            make_view: Makes the view a visit is shown in; ``None`` with no
                service.
            hidden_pass_runs: Whether a hidden pass will visit pending entries.
            parent: The main window.
        """
        super().__init__(parent)
        self._storage = storage
        self._service = service
        self._hidden_pass_runs = hidden_pass_runs
        self._entries: list[RescueEntry] = []
        self._filter = QueueFilter.WAITING
        self.setWindowTitle(WINDOW_TITLE)
        self.setModal(False)
        metrics = self.fontMetrics()
        self.resize(
            metrics.averageCharWidth() * REVIEW_QUEUE_WIDTH_CHARS,
            metrics.height() * REVIEW_QUEUE_HEIGHT_LINES,
        )
        self._build(make_view if service is not None else None)
        if service is not None:
            service.shown_visit_started.connect(self._on_visit_started)
            service.shown_visit_ended.connect(self._on_visit_ended)
            service.shown_visit_failed.connect(self._on_visit_failed)
            service.entry_changed.connect(self.refresh)
            service.walk_ended.connect(self._on_walk_ended)
        self.refresh()

    # -- building --------------------------------------------------------------

    def _build(self, make_view: Callable[[QWidget], QWidget] | None) -> None:
        """Lay the window out.

        Args:
            make_view: Makes the view; ``None`` shows a note instead.
        """
        layout = QVBoxLayout(self)
        help_label = QLabel(REVIEW_QUEUE_HELP)
        help_label.setObjectName("reviewQueueHelp")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.filter_box = QComboBox()
        for option in QueueFilter:
            self.filter_box.addItem(option.value, option)
        self.filter_box.currentIndexChanged.connect(
            lambda _i: self.set_filter(self.filter_box.currentData())
        )
        left_layout.addWidget(self.filter_box)
        self.table = QTableWidget(0, len(_Column))
        self.table.setHorizontalHeaderLabels([_HEADERS[c] for c in _Column])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(_Column.TITLE, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self.table)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel("")
        self.status_label.setObjectName("reviewQueueStatus")
        self.status_label.setWordWrap(True)
        right_layout.addWidget(self.status_label)
        self.view: QWidget | None = make_view(right) if make_view is not None else None
        self.view_note: QLabel | None = None
        if self.view is not None:
            right_layout.addWidget(self.view, 1)
        else:
            self.view_note = QLabel(REVIEW_QUEUE_NO_BROWSER)
            self.view_note.setObjectName("reviewQueueHelp")
            self.view_note.setWordWrap(True)
            right_layout.addWidget(self.view_note, 1)
        buttons = QHBoxLayout()
        self.reload_button = QPushButton("Reload")
        self.reload_button.clicked.connect(self._reload)
        self.system_browser_button = QPushButton("Open in system browser")
        self.system_browser_button.clicked.connect(self._open_selected_in_system_browser)
        self.dismiss_button = QPushButton("Dismiss")
        self.dismiss_button.clicked.connect(self._dismiss_selected)
        self.walk_button = QPushButton(WALK_LABEL)
        self.walk_button.clicked.connect(self._toggle_walk)
        self.walk_button.setEnabled(self._service is not None)
        for button in (self.reload_button, self.system_browser_button, self.dismiss_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.walk_button)
        right_layout.addLayout(buttons)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

    # -- the list ------------------------------------------------------------------

    @property
    def current_filter(self) -> QueueFilter:
        """The list's filter.

        Returns:
            It.
        """
        return self._filter

    def _states(self) -> frozenset[RescueState]:
        """The states the current filter lists."""
        if self._filter is QueueFilter.WAITING:
            return awaiting_reader_states(self._hidden_pass_runs())
        if self._filter is QueueFilter.OPEN:
            return OPEN_RESCUE_STATES
        return frozenset(RescueState)

    def set_filter(self, queue_filter: QueueFilter) -> None:
        """Show another set of entries.

        Args:
            queue_filter: The filter.
        """
        self._filter = queue_filter
        index = self.filter_box.findData(queue_filter)
        if index != self.filter_box.currentIndex():
            self.filter_box.blockSignals(True)
            self.filter_box.setCurrentIndex(index)
            self.filter_box.blockSignals(False)
        self.refresh()

    def refresh(self, *_args: object) -> None:
        """Read the queue again, keeping the selection when it is still listed.

        Connected to ``entry_changed``, whose entry is ignored. A queue that
        cannot be read leaves the list as it was and says so.

        Args:
            *_args: Ignored.
        """
        try:
            entries = self._storage.rescue_entries(self._states())
        except (sqlite3.Error, SQLiteError) as e:
            logger.error("The browser rescue queue could not be read for its window: %s", e)
            self.status_label.setText(REVIEW_QUEUE_UNREADABLE)
            return
        selected = self.selected_entry_id()
        self._entries = entries
        hidden = self._hidden_pass_runs()
        self.table.blockSignals(True)
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            state = state_label(entry.state, hidden)
            retry = retry_text(entry) if entry.state is RescueState.PENDING else ""
            cells = {
                _Column.TITLE: entry.request.title or entry.request.doi,
                _Column.HOST: entry.request.host,
                _Column.NAMED_BY: service_label(entry.request.service),
                _Column.STATE: f"{state}; {retry}" if retry else state,
                _Column.VERDICT: verdict_text(entry.verdict),
            }
            for column, text in cells.items():
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, entry.id)
                self.table.setItem(row, column, item)
            if entry.id == selected:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        self._update_buttons()

    def selected_entry_id(self) -> int | None:
        """The selected entry's ID.

        Returns:
            It; ``None`` when nothing is selected.
        """
        entry = self._selected_entry()
        return entry.id if entry is not None else None

    def _selected_entry(self) -> RescueEntry | None:
        """The selected entry, as last read."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def focus_entry(self, entry_id: int) -> None:
        """Select an entry, switching to *All* when the filter hides it.

        Args:
            entry_id: The entry.
        """
        if entry_id not in {e.id for e in self._entries}:
            self.set_filter(QueueFilter.ALL)
        for row, entry in enumerate(self._entries):
            if entry.id == entry_id:
                self.table.selectRow(row)
                return

    # -- visits ----------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        """Show the selected open entry's page; for a settled one, its verdict only (D6)."""
        entry = self._selected_entry()
        self._update_buttons()
        if self._service is None or entry is None:
            return
        if entry.state not in OPEN_RESCUE_STATES:
            self._service.end_shown_visit()
            self.status_label.setText(verdict_text(entry.verdict))
            return
        if self._service.shown_entry_id == entry.id:
            return
        if self.view is None:
            return
        self.status_label.setText(SHOWN_VISIT_OPENING)
        self._service.visit_shown(entry, self.view)  # type: ignore[arg-type]

    def _on_visit_started(self, _entry: RescueEntry) -> None:
        """A visit's page is opening in the view."""
        walking = self._service is not None and self._service.walking
        self.status_label.setText(WALK_RUNNING if walking else SHOWN_VISIT_LOADING)

    def _on_visit_ended(self, entry: RescueEntry, verdict: RescueVerdict | None) -> None:
        """Say how a visit ended; after a person's rescue, open the next waiting entry.

        Args:
            entry: The entry visited.
            verdict: Its verdict; ``None`` when it was left without one.
        """
        walking = self._service is not None and self._service.walking
        self.refresh()
        if verdict is None:
            # Left without a verdict: the reader moved on, or it could not open
            return
        self.status_label.setText(verdict_text(verdict))
        if verdict.outcome is RescueOutcome.PDF and not walking:
            self._select_next_waiting(after=entry.id)

    def _on_visit_failed(self, entry: RescueEntry) -> None:
        """A page could not be opened here (logged by the service).

        Args:
            entry: The entry.
        """
        if entry.id == self.selected_entry_id():
            self.status_label.setText(SHOWN_VISIT_NOT_STARTED)

    def _select_next_waiting(self, after: int) -> None:
        """Select the first listed open entry other than ``after``.

        Args:
            after: The entry just rescued.
        """
        for row, entry in enumerate(self._entries):
            if entry.id != after and entry.state in OPEN_RESCUE_STATES:
                self.table.selectRow(row)
                return

    def _reload(self) -> None:
        """Reload the page shown."""
        reload = getattr(self.view, "reload", None)
        if callable(reload):
            reload()

    def _open_selected_in_system_browser(self) -> None:
        """Open the selected entry's address in the system's browser."""
        entry = self._selected_entry()
        if entry is not None:
            self._open_in_system_browser(entry.request.address)

    def _open_in_system_browser(self, address: str) -> None:
        """Open an address in the system's browser; nothing is saved from it.

        Args:
            address: A stored address, untrusted: only ``http(s)`` opens.
        """
        if fetchable_host(address) is None:
            logger.warning("Not opening a walled PDF's address that is no web address: %r", address)
            return
        QDesktopServices.openUrl(QUrl(address))

    def _dismiss_selected(self) -> None:
        """Set the selected open entry aside, ending its visit first."""
        entry = self._selected_entry()
        if entry is None or entry.state not in OPEN_RESCUE_STATES:
            return
        if self._service is not None and self._service.shown_entry_id == entry.id:
            self._service.end_shown_visit()
        if dismiss_entry(self._storage, entry.id) is None:
            # Settled elsewhere meanwhile, or the queue could not be written
            current = self._storage.rescue_entry(entry.id)
            if current is None or current.state in OPEN_RESCUE_STATES:
                self.status_label.setText(REVIEW_QUEUE_UNWRITABLE)
        self.refresh()
        self.queue_changed.emit()

    def _toggle_walk(self) -> None:
        """Start *Walk the queue* over the listed waiting entries, or stop it."""
        if self._service is None or self.view is None:
            return
        if self._service.walking:
            self._service.stop_walk()
            return
        waiting = awaiting_reader_states(self._hidden_pass_runs())
        ids = [e.id for e in self._entries if e.state in waiting]
        if not ids:
            return
        self._service.walk(ids, self.view)  # type: ignore[arg-type]
        self.walk_button.setText(STOP_WALK_LABEL)
        self.status_label.setText(WALK_RUNNING)

    def _on_walk_ended(self) -> None:
        """The walk is over."""
        self.walk_button.setText(WALK_LABEL)
        self.refresh()

    def _update_buttons(self) -> None:
        """Enable what applies to the selection."""
        entry = self._selected_entry()
        is_open = entry is not None and entry.state in OPEN_RESCUE_STATES
        self.dismiss_button.setEnabled(is_open)
        self.system_browser_button.setEnabled(entry is not None)
        self.reload_button.setEnabled(self.view is not None and is_open)

    # -- closing ---------------------------------------------------------------------

    def done(self, result: int) -> None:
        """Every way out ends the walk and the visit: nothing runs behind a closed window.

        Args:
            result: The dialog's result.
        """
        if self._service is not None:
            if self._service.walking:
                self._service.stop_walk()
            self._service.end_shown_visit()
        super().done(result)
```

`QDialog.done` is reached by Escape, by `accept`, and by the window's close box: `QDialog.closeEvent` calls `reject()`, which calls `done()`. The settings dialog's lifecycle relies on the same path (PR #518). Add a dialog test that closes through `reject()` as well as `done()`.

- [ ] **Step 5: Run the window tests**

Run: `pytest tests/test_review_queue_dialog.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bmlibrarian_lite/gui/rescue_view.py src/bmlibrarian_lite/gui/review_queue_dialog.py \
  src/bmlibrarian_lite/resources/styles/stylesheet_generator.py tests/test_review_queue_dialog.py
git commit -m "feat(gui): the review queue window for walled PDFs (#480)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The interrogation tab's priority path

**Files:**
- Modify: `src/bmlibrarian_lite/gui/workers.py` (`FulltextDiscoveryWorker`)
- Modify: `src/bmlibrarian_lite/gui/document_interrogation_tab.py`
- Test: `tests/test_interrogation_browser_rescue.py` (new); `tests/test_interrogation_degradation.py` (the stub gains `lookups`)

**Interfaces:**
- Consumes: `rescue_requests`, `queue_challenged_copies`, `RescueSubject`, `wait_watch`, `wait_outcome`, `WaitOutcome`, `rescue_pdf_path` (stage 2 and Task 1); `LiteStorage.rescue_entries_for_document` (Task 2); the sentences in `browser_rescue_text` (Task 1).
- Produces, on `FulltextDiscoveryWorker`: `lookups: LookupRecord` (D10).
- Produces, on `DocumentInterrogationTab`: signals `rescue_queued = Signal()` and `review_queue_requested = Signal(int)`; `set_hidden_pass_check(check: Callable[[], bool]) -> None`; `on_rescue_entry_changed(entry: RescueEntry) -> None`. Module constants `FULLTEXT_WALLED` and `BROWSER_PDF_LABEL`; module function `ask_to_open_review_queue(parent: QWidget) -> bool`.

- [ ] **Step 1: Write the failing worker test** (append to `tests/test_interrogation_browser_rescue.py`, created here)

```python
"""The interrogation tab puts a walled PDF the reader asked for first (#480, stage 3).

The worker is stood in for, the queue is a real LiteStorage, the
service's signal is called directly, and the QApplication is never pumped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QObject, Signal

from bmlibrarian_lite.browser_rescue import RescueOutcome, RescueState, RescueVerdict
from bmlibrarian_lite.config import LiteConfig
from bmlibrarian_lite.constants import RESCUE_PRIORITY_WAITING, SERVICE_UNPAYWALL_PDF
from bmlibrarian_lite.data_models import (
    Citation,
    DocumentSource,
    LiteDocument,
    LookupRecord,
    RequestFailure,
    RequestFailureKind,
    SourceLookupFailure,
)
from bmlibrarian_lite.fulltext_discovery import FulltextResult, FulltextSourceType
from bmlibrarian_lite.gui import document_interrogation_tab as tab_module
from bmlibrarian_lite.gui.citation_loader import abstract_source_label
from bmlibrarian_lite.gui.document_interrogation_tab import (
    BROWSER_PDF_LABEL,
    FULLTEXT_CANCELLED,
    FULLTEXT_UNAVAILABLE,
    FULLTEXT_WALLED,
    DocumentInterrogationTab,
)
from bmlibrarian_lite.gui.workers import FulltextDiscoveryWorker
from bmlibrarian_lite.storage import LiteStorage

DOI = "10.1000/walled"
ADDRESS = "https://www.sciencedirect.com/science/article/pii/S1/pdfft"
OTHER_ADDRESS = "https://europepmc.org/articles/PMC1/pdf"


def _walled(*addresses: str) -> LookupRecord:
    return LookupRecord(failures=tuple(
        SourceLookupFailure(SERVICE_UNPAYWALL_PDF, RequestFailure(RequestFailureKind.CHALLENGED, 403), a)
        for a in addresses
    ))


def test_the_worker_keeps_the_record_of_a_failed_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """D10: the record is on the worker before its terminal signal."""
    worker = FulltextDiscoveryWorker({"doi": DOI})
    result = FulltextResult(success=False, source_type=FulltextSourceType.NOT_FOUND,
                            error="no", lookups=_walled(ADDRESS))
    monkeypatch.setattr(worker, "_build_discoverer",
                        lambda: MagicMock(discover_fulltext=lambda doc_dict: result))
    seen: list[LookupRecord] = []
    worker.error.connect(lambda _e: seen.append(worker.lookups))
    worker._discover()
    assert seen == [_walled(ADDRESS)]
```

Check the actual `FulltextResult` and `FulltextSourceType` import paths and constructor before running. If `FulltextSourceType` lives elsewhere, use the path `fulltext_discovery.py` imports it from.

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_interrogation_browser_rescue.py -q`
Expected: collection fails on the tab's missing names. Comment out the tab imports to see the worker test fail with `AttributeError: 'FulltextDiscoveryWorker' object has no attribute 'lookups'`.

- [ ] **Step 3: Implement D10** (`gui/workers.py`, `FulltextDiscoveryWorker`)

In `__init__`: `self.lookups: LookupRecord = LookupRecord()`, documented in the class docstring: "lookups: what the run's discovery left unsettled, set before its terminal signal; the interrogation tab reads it to queue a walled PDF (#480, stage 3)." Import `LookupRecord` from `..data_models`. In `_discover`, straight after `result = self._discoverer.discover_fulltext(...)`: `self.lookups = result.lookups`.

In `tests/test_interrogation_degradation.py`'s `StubFulltextWorker.__init__`, add `self.lookups = LookupRecord()` (import it from `bmlibrarian_lite.data_models`). Grep for other `FulltextDiscoveryWorker` stand-ins (`grep -rn "FulltextDiscoveryWorker" tests/`). A `MagicMock` worker needs no change, since its `lookups.failures` iterates empty.

- [ ] **Step 4: Write the failing tab tests** (append to the same file)

```python
class StubFulltextWorker(QObject):
    """Full-text discovery that answers only when the test emits for it."""

    progress = Signal(str, str)
    finished = Signal(str, str, str)
    paywall_detected = Signal(str, str)
    error = Signal(str)
    cancelled = Signal(str)

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        super().__init__()
        self.lookups = LookupRecord()

    def start(self) -> None:
        """Nothing runs until the test emits."""

    def cancel(self) -> None:
        """Ignored."""


@pytest.fixture
def qapp() -> Any:
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _citation(doi: str | None = DOI, doc_id: str = "doc-1") -> Citation:
    document = LiteDocument(id=doc_id, title="Walled trial", abstract="An abstract.", authors=["Smith J"],
                            year=2024, journal="J", pmid="1", doi=doi, source=DocumentSource.EUROPEPMC)
    return Citation(document=document, passage="An abstract.", relevance_score=4)


class Harness:
    """The tab, its queue, and what it did."""

    def __init__(self, tab: DocumentInterrogationTab, storage: LiteStorage) -> None:
        self.tab = tab
        self.storage = storage
        self.abstracts: list[str | None] = []
        self.pdfs: list[tuple[Path, str]] = []
        self.offers: list[bool] = []
        self.queued: list[bool] = []
        self.requested: list[int] = []

    def fail_with(self, record: LookupRecord) -> None:
        """End the citation's discovery with no full text and this record."""
        worker = self.tab._fulltext_worker
        worker.lookups = record
        worker.error.emit("no full text")

    def entries(self) -> list[Any]:
        return self.storage.rescue_entries_for_document("doc-1")

    def verdict(self, address: str, verdict: RescueVerdict) -> None:
        """Record a verdict as the service would, and tell the tab."""
        entry = next(e for e in self.entries() if e.request.address == address)
        recorded = self.storage.record_rescue_verdict(entry.id, verdict)
        self.tab.on_rescue_entry_changed(recorded.entry)


@pytest.fixture
def make(qapp: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Make a harness: the hidden pass on or off, the offer accepted or not."""

    def build(hidden: bool = True, accept_offer: bool = False, citation: Citation | None = None) -> Harness:
        monkeypatch.setattr(tab_module, "LiteInterrogationAgent", MagicMock())
        monkeypatch.setattr(tab_module, "QMessageBox", MagicMock())
        monkeypatch.setattr(tab_module, "find_existing_fulltext", lambda _m: None)
        monkeypatch.setattr(tab_module, "find_existing_pdf", lambda _m: None)
        monkeypatch.setattr(tab_module, "FulltextDiscoveryWorker", StubFulltextWorker)
        monkeypatch.setattr(tab_module, "rescue_pdf_path", lambda doi: tmp_path / "rescued.pdf")
        config = LiteConfig()
        config.storage.data_dir = tmp_path
        storage = LiteStorage(config)
        tab = DocumentInterrogationTab(config=config, storage=storage)
        tab.document_view = MagicMock()
        harness = Harness(tab, storage)
        monkeypatch.setattr(tab, "_load_citation_abstract",
                            lambda _c, degradation=None: harness.abstracts.append(degradation))
        monkeypatch.setattr(tab, "_load_citation_pdf",
                            lambda path, _c, label: harness.pdfs.append((path, label)))
        monkeypatch.setattr(tab_module, "ask_to_open_review_queue",
                            lambda _parent: harness.offers.append(True) or accept_offer)
        tab.rescue_queued.connect(lambda: harness.queued.append(True))
        tab.review_queue_requested.connect(harness.requested.append)
        tab.set_hidden_pass_check(lambda: hidden)
        tab.load_from_citation(citation or _citation())
        return harness

    return build


def test_a_walled_pdf_is_queued_first_and_the_reader_waits(make: Any) -> None:
    """Queued at the waiting priority; the service is told; nothing is loaded yet."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    (entry,) = h.entries()
    assert entry.priority == RESCUE_PRIORITY_WAITING and entry.state is RescueState.PENDING
    assert h.queued == [True]
    assert h.abstracts == [] and h.pdfs == []
    assert h.tab._pdf_progress_dialog is not None
    assert h.tab._pdf_progress_dialog.labelText() == tab_module.BROWSER_RESCUE_WAITING


def test_a_rescue_loads_the_pdf_into_the_session(make: Any, tmp_path: Path) -> None:
    """The PDF the browser saved is loaded, labelled as fetched in the built-in browser."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    (tmp_path / "rescued.pdf").write_bytes(b"%PDF-1.4\n")
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.PDF))
    assert h.pdfs == [(tmp_path / "rescued.pdf", BROWSER_PDF_LABEL)]
    assert h.abstracts == [] and h.tab._pdf_progress_dialog is None


def test_a_rescued_pdf_missing_from_disk_falls_back(make: Any) -> None:
    """Never load what is not there."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.PDF))
    assert h.pdfs == [] and h.abstracts == [FULLTEXT_UNAVAILABLE]


def test_a_wall_the_hidden_pass_could_not_pass_offers_the_queue(make: Any) -> None:
    """needs_person: the abstract, named as walled, and the offer; accepted, it opens on that entry."""
    h = make(accept_offer=True)
    h.fail_with(_walled(ADDRESS))
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare",)))
    assert h.abstracts == [FULLTEXT_WALLED]
    assert h.offers == [True]
    assert h.requested == [h.entries()[0].id]


def test_a_declined_offer_opens_nothing(make: Any) -> None:
    """Not now: the abstract stays, the entry stays queued."""
    h = make(accept_offer=False)
    h.fail_with(_walled(ADDRESS))
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.CHALLENGE, 403, ()))
    assert h.offers == [True] and h.requested == []
    assert h.entries()[0].state is RescueState.NEEDS_PERSON


def test_no_pdf_behind_the_wall_keeps_today_s_fallback(make: Any) -> None:
    """no_pdf: "could not be retrieved", and no offer."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.NO_PDF, 200))
    assert h.abstracts == [FULLTEXT_UNAVAILABLE] and h.offers == []


def test_a_transport_failure_ends_the_wait_with_the_offer(make: Any) -> None:
    """D8: the retry is 15 minutes away; the reader is not kept waiting."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.TRANSPORT))
    assert h.abstracts == [FULLTEXT_WALLED] and h.offers == [True]


def test_two_addresses_wait_for_both_or_one_rescue(make: Any, tmp_path: Path) -> None:
    """One wall answered, the other still to visit: still waiting; then its rescue loads."""
    h = make()
    h.fail_with(_walled(ADDRESS, OTHER_ADDRESS))
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.CHALLENGE, 403, ()))
    assert h.abstracts == [] and h.pdfs == []
    (tmp_path / "rescued.pdf").write_bytes(b"%PDF-1.4\n")
    h.verdict(OTHER_ADDRESS, RescueVerdict(RescueOutcome.PDF))
    assert len(h.pdfs) == 1


def test_with_the_hidden_pass_off_the_tab_offers_the_queue_at_once(make: Any) -> None:
    """Review focus 3: no pass will come, so no wait."""
    h = make(hidden=False)
    h.fail_with(_walled(ADDRESS))
    assert h.tab._pdf_progress_dialog is None
    assert h.abstracts == [FULLTEXT_WALLED] and h.offers == [True]
    assert h.entries()[0].priority == RESCUE_PRIORITY_WAITING


def test_the_wait_ends_at_its_limit_with_the_offer(make: Any) -> None:
    """Review focus 2: a slow or stuck pass never keeps the reader waiting past the limit."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    h.tab._on_rescue_wait_limit()
    assert h.abstracts == [FULLTEXT_WALLED] and h.offers == [True]
    assert h.tab._pdf_progress_dialog is None


def test_cancelling_the_wait_falls_back_and_keeps_the_entry(make: Any, tmp_path: Path) -> None:
    """Cancel: "cancelled", the entry stays queued, a later rescue is not loaded here (D9)."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    h.tab._cancel_rescue_wait()
    assert h.abstracts == [FULLTEXT_CANCELLED]
    (tmp_path / "rescued.pdf").write_bytes(b"%PDF-1.4\n")
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.PDF))
    assert h.pdfs == []


def test_another_document_s_verdict_is_ignored(make: Any) -> None:
    """Only this document's entries end its wait."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    other = h.storage
    other.enqueue_rescues(list(tab_module.rescue_requests(
        tab_module.RescueSubject("doc-2", "10.1/o", None, "O"), _walled(OTHER_ADDRESS))))
    entry = other.rescue_entries_for_document("doc-2")[0]
    h.tab.on_rescue_entry_changed(other.record_rescue_verdict(entry.id, RescueVerdict(RescueOutcome.PDF)).entry)
    assert h.pdfs == [] and h.abstracts == []


def test_loading_another_citation_drops_the_wait(make: Any) -> None:
    """The reader moved on: a later verdict for the first document does nothing."""
    h = make()
    h.fail_with(_walled(ADDRESS))
    h.tab.load_from_citation(_citation(doc_id="doc-2", doi="10.1/other"))
    h.verdict(ADDRESS, RescueVerdict(RescueOutcome.CHALLENGE, 403, ()))
    assert h.offers == [] and h.abstracts == []


def test_a_failure_with_nothing_walled_is_today_s_fallback(make: Any) -> None:
    """Control: no challenged copy, nothing queued, the abstract as before."""
    h = make()
    h.fail_with(LookupRecord())
    assert h.entries() == [] and h.queued == []
    assert h.abstracts == [FULLTEXT_UNAVAILABLE]


def test_a_document_without_a_doi_is_not_queued(make: Any) -> None:
    """No DOI, no file name for the PDF: today's fallback."""
    h = make(citation=_citation(doi=None))
    h.fail_with(_walled(ADDRESS))
    assert h.entries() == [] and h.abstracts == [FULLTEXT_UNAVAILABLE]


def test_a_queue_that_cannot_be_written_is_today_s_fallback(make: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Golden rule 8: logged; the reader gets the abstract, not a hang."""
    import sqlite3

    h = make()

    def fail(*_a: Any, **_k: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(h.storage, "rescue_entries_for_document", fail)
    h.fail_with(_walled(ADDRESS))
    assert h.abstracts == [FULLTEXT_UNAVAILABLE]
    assert h.tab._pdf_progress_dialog is None
```

Also add a test of `ask_to_open_review_queue` itself: patch `QMessageBox.exec` to return the role of the "Open" button and assert `True`; return the other and assert `False`. Its shape follows Step 5.

- [ ] **Step 5: Implement the priority path** (`gui/document_interrogation_tab.py`)

Imports: `sqlite3`, `from dataclasses import dataclass, field`, `from datetime import UTC, datetime`, `QTimer` from QtCore; from `..browser_rescue`: `RescueEntry, RescueSubject, WaitOutcome, queue_challenged_copies, rescue_pdf_path, rescue_requests, wait_outcome, wait_watch`; from `..browser_rescue_text`: `BROWSER_RESCUE_WAITING, REVIEW_QUEUE_OFFER, REVIEW_QUEUE_OFFER_LATER, REVIEW_QUEUE_OFFER_OPEN, REVIEW_QUEUE_OFFER_TITLE`; from `..constants`: `RESCUE_PRIORITY_WAITING, RESCUE_READER_WAIT_LIMIT_MS`; from `..data_models`: `LookupRecord`; `from ..exceptions import SQLiteError`.

Constants beside the other degradations:

```python
FULLTEXT_WALLED = "the publisher's copy is behind a robot check"
# The label of a PDF the built-in browser fetched for the reader (#480, stage 3)
BROWSER_PDF_LABEL = "Full Text (PDF, fetched in the built-in browser)"
```

The offer:

```python
def ask_to_open_review_queue(parent: QWidget) -> bool:
    """Offer the review queue for a walled PDF (#480, stage 3).

    Args:
        parent: The tab.

    Returns:
        Whether the reader chose to open it.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(REVIEW_QUEUE_OFFER_TITLE)
    box.setText(REVIEW_QUEUE_OFFER)
    open_button = box.addButton(REVIEW_QUEUE_OFFER_OPEN, QMessageBox.ButtonRole.AcceptRole)
    box.addButton(REVIEW_QUEUE_OFFER_LATER, QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is open_button
```

The wait's state:

```python
@dataclass
class _RescueWait:
    """A reader waiting on a document's walled PDFs.

    Attributes:
        citation: The citation being loaded.
        document_id: Its document.
        addresses: The walled addresses this load queued.
        watched: The entries waited for (``wait_watch``).
        fall_back: Loads the abstract, naming a cause.
        answered: The entries with a verdict since the wait began.
    """

    citation: Citation
    document_id: str
    addresses: frozenset[str]
    watched: frozenset[int]
    fall_back: Callable[[str], None]
    answered: set[int] = field(default_factory=set)
```

Signals on the class: `rescue_queued = Signal()` and `review_queue_requested = Signal(int)`. Add both to the class docstring.

In `__init__`:

```python
        # Whether a hidden pass will visit a walled PDF the reader waits on;
        # set by the main window (#480, stage 3). Until then, never.
        self._hidden_pass_runs: Callable[[], bool] = lambda: False
        self._rescue_wait: _RescueWait | None = None
        self._rescue_wait_timer = QTimer(self)
        self._rescue_wait_timer.setSingleShot(True)
        self._rescue_wait_timer.timeout.connect(self._on_rescue_wait_limit)
```

Wire the record through. In `_start_fulltext_discovery`, replace the error connection with:

```python
        worker = self._fulltext_worker
        worker.error.connect(
            lambda e, w=worker: self._on_fulltext_error(e, citation, on_error, w.lookups)
        )
```

In `_on_fulltext_error`, add `lookups: LookupRecord | None = None` (documented). After `logger.warning(...)`:

```python
        if callback and lookups is not None and self._begin_rescue_wait(citation, lookups, callback):
            return
```

`load_from_citation` and `_clear_document` both start with `self._end_rescue_wait()`. `_clear_document` is what `load_from_citation` calls first, so put it there and confirm both paths reach it.

The rest:

```python
    def set_hidden_pass_check(self, check: Callable[[], bool]) -> None:
        """Say how to tell whether a hidden pass will visit a queued PDF (#480, stage 3).

        Args:
            check: The main window's test.
        """
        self._hidden_pass_runs = check

    def _begin_rescue_wait(
        self, citation: Citation, lookups: LookupRecord, fall_back: Callable[[str], None]
    ) -> bool:
        """Queue the walled PDFs a failed load left, first, and wait for them.

        Args:
            citation: The citation being loaded.
            lookups: What its discovery left unsettled.
            fall_back: Loads the abstract, naming a cause.

        Returns:
            Whether this took the load over (waiting, or settled at once:
            the offer); ``False`` leaves today's fallback to the caller.
        """
        document = citation.document
        try:
            subject = RescueSubject(document.id, document.doi, document.pmid, document.title or "")
        except ValueError:
            return False
        requests = rescue_requests(subject, lookups)
        if not requests:
            return False
        queue_challenged_copies(self.storage, subject, lookups, RESCUE_PRIORITY_WAITING)
        addresses = frozenset(r.address for r in requests)
        entries = self._wait_entries(subject.document_id, addresses)
        if entries is None:
            return False
        if entries:
            self.rescue_queued.emit()
        watched = wait_watch(entries, self._hidden_pass_runs(), datetime.now(UTC))
        wait = _RescueWait(citation, subject.document_id, addresses, watched, fall_back)
        outcome = wait_outcome(entries, watched, wait.answered)
        if outcome is WaitOutcome.WAITING:
            self._rescue_wait = wait
            self._pdf_progress_dialog = self._create_progress_dialog("Fetching Full Text")
            self._pdf_progress_dialog.setLabelText(BROWSER_RESCUE_WAITING)
            self._pdf_progress_dialog.canceled.connect(self._cancel_rescue_wait)
            self._pdf_progress_dialog.show()
            self._rescue_wait_timer.start(RESCUE_READER_WAIT_LIMIT_MS)
            return True
        return self._settle_rescue_wait(wait, outcome, entries)

    def _wait_entries(self, document_id: str, addresses: frozenset[str]) -> list[RescueEntry] | None:
        """This load's queue entries, as they stand.

        Args:
            document_id: The document.
            addresses: The walled addresses this load queued.

        Returns:
            The entries; ``None`` when the queue could not be read (logged).
        """
        try:
            entries = self.storage.rescue_entries_for_document(document_id)
        except (sqlite3.Error, SQLiteError) as e:
            logger.error("The walled PDFs of %s could not be read from the queue: %s", document_id, e)
            return None
        return [e for e in entries if e.request.address in addresses]

    def on_rescue_entry_changed(self, entry: RescueEntry) -> None:
        """A visit's verdict was recorded: end the wait when it is decided.

        Args:
            entry: The entry, as it now stands.
        """
        wait = self._rescue_wait
        if wait is None or entry.request.document_id != wait.document_id:
            return
        if entry.request.address not in wait.addresses:
            return
        wait.answered.add(entry.id)
        entries = self._wait_entries(wait.document_id, wait.addresses)
        if entries is None:
            self._end_rescue_wait()
            wait.fall_back(FULLTEXT_UNAVAILABLE)
            return
        outcome = wait_outcome(entries, wait.watched, wait.answered)
        if outcome is WaitOutcome.WAITING:
            return
        self._end_rescue_wait()
        self._settle_rescue_wait(wait, outcome, entries)

    def _on_rescue_wait_limit(self) -> None:
        """The wait's limit: decide on what is known, waiting no longer."""
        wait = self._rescue_wait
        if wait is None:
            return
        self._end_rescue_wait()
        entries = self._wait_entries(wait.document_id, wait.addresses)
        if entries is None:
            wait.fall_back(FULLTEXT_UNAVAILABLE)
            return
        self._settle_rescue_wait(wait, wait_outcome(entries, wait.watched, wait.watched | wait.answered), entries)

    def _cancel_rescue_wait(self) -> None:
        """The reader stopped waiting: the abstract; the entry stays queued (D9)."""
        wait = self._rescue_wait
        if wait is None:
            return
        self._end_rescue_wait()
        wait.fall_back(FULLTEXT_CANCELLED)

    def _end_rescue_wait(self) -> None:
        """Stop waiting, silently: the timer, the dialog, the state."""
        self._rescue_wait_timer.stop()
        if self._rescue_wait is not None:
            self._rescue_wait = None
            self._close_progress_dialog()

    def _settle_rescue_wait(
        self, wait: _RescueWait, outcome: WaitOutcome, entries: list[RescueEntry]
    ) -> bool:
        """Load what the wait came to.

        Args:
            wait: The wait.
            outcome: Where it stands; never ``WAITING``.
            entries: Its entries, as they stand.

        Returns:
            ``True``: the load is decided here.
        """
        if outcome is WaitOutcome.RESCUED:
            rescued = next(e for e in entries if e.state is RescueState.RESCUED and e.id in wait.answered)
            path = rescue_pdf_path(rescued.request.doi)
            if path.is_file():
                self._load_citation_pdf(path, wait.citation, BROWSER_PDF_LABEL)
            else:
                logger.warning("The rescued PDF of %s is not at %s", wait.document_id, path)
                wait.fall_back(FULLTEXT_UNAVAILABLE)
            return True
        if outcome is WaitOutcome.NEEDS_READER:
            wait.fall_back(FULLTEXT_WALLED)
            open_entry = next(e for e in entries if e.state in OPEN_RESCUE_STATES)
            if ask_to_open_review_queue(self):
                self.review_queue_requested.emit(open_entry.id)
            return True
        wait.fall_back(FULLTEXT_UNAVAILABLE)
        return True
```

(Import `OPEN_RESCUE_STATES` and `RescueState` with the other rules.) `_close_progress_dialog` already blocks `canceled` while closing, so ending the wait never reads as a cancel.

The `_cancel_rescue_wait` name ends in `_wait`, unlike the tab's `_cancel_*_discovery` methods, so grep confirms that no test or caller confuses the two.

- [ ] **Step 6: Run the tab tests, and every interrogation suite**

Run: `pytest tests/test_interrogation_browser_rescue.py tests/test_interrogation_degradation.py tests/test_silent_cancels.py tests/test_elsevier_discovery.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/bmlibrarian_lite/gui/workers.py src/bmlibrarian_lite/gui/document_interrogation_tab.py \
  tests/test_interrogation_browser_rescue.py tests/test_interrogation_degradation.py
git commit -m "feat(gui): a walled PDF the reader opens goes first, and is loaded when it arrives (#480)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The indicator, the Tools menu, and the window's wiring

**Files:**
- Create: `src/bmlibrarian_lite/gui/rescue_queue_indicator.py`
- Modify: `src/bmlibrarian_lite/gui/app.py`
- Test: `tests/test_rescue_queue_indicator.py` (new), `tests/test_app_browser_rescue_startup.py`

**Interfaces:**
- Consumes: `LiteStorage.count_rescue_entries` (Task 2), `awaiting_reader_states` (Task 1), `ReviewQueueDialog` (Task 6), the service's `entry_changed` (Task 4); and the tab's `rescue_queued`, `review_queue_requested`, `on_rescue_entry_changed` and `set_hidden_pass_check` (Task 7).
- Produces:
  - `indicator_text(count: int) -> str`; `class RescueQueueIndicator(QPushButton)` with `__init__(storage, hidden_pass_runs: Callable[[], bool], poll_ms: int = RESCUE_POLL_INTERVAL_MS, parent=None)`, `start() -> None` and `refresh(*_args) -> int | None`.
  - `LiteMainWindow`: `rescue_indicator`, `show_review_queue(entry_id: int | None = None) -> None`, `_hidden_pass_runs() -> bool`, `_on_rescue_queued() -> None`.

- [ ] **Step 1: Write the failing indicator tests** (`tests/test_rescue_queue_indicator.py`)

```python
"""The status bar's "Walled PDFs: N" (#480, stage 3)."""

import sqlite3
from typing import Any

import pytest

from bmlibrarian_lite.browser_rescue import RescueState
from bmlibrarian_lite.gui.rescue_queue_indicator import RescueQueueIndicator, indicator_text


@pytest.fixture
def qapp():
    """The suite's QApplication; never pumped."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Storage:
    def __init__(self, count: int | Exception) -> None:
        self.count = count
        self.asked: list[frozenset[RescueState]] = []

    def count_rescue_entries(self, states: Any) -> int:
        self.asked.append(frozenset(states))
        if isinstance(self.count, Exception):
            raise self.count
        return self.count


def test_the_text_names_the_count() -> None:
    """The text names the count."""
    assert indicator_text(3) == "Walled PDFs: 3"


def test_it_is_hidden_at_zero_and_shown_otherwise(qapp) -> None:
    """Hidden at 0 (spec)."""
    storage = _Storage(0)
    indicator = RescueQueueIndicator(storage, lambda: True)  # type: ignore[arg-type]
    assert indicator.refresh() == 0 and indicator.isHidden()
    storage.count = 2
    assert indicator.refresh() == 2 and not indicator.isHidden()
    assert indicator.text() == "Walled PDFs: 2"


@pytest.mark.parametrize(
    "hidden, states",
    [(True, {RescueState.NEEDS_PERSON}), (False, {RescueState.NEEDS_PERSON, RescueState.PENDING})],
)
def test_it_counts_what_waits_for_the_reader(qapp, hidden: bool, states: set[RescueState]) -> None:
    """D4: pending counts only when no hidden pass will visit it."""
    storage = _Storage(1)
    RescueQueueIndicator(storage, lambda: hidden).refresh()  # type: ignore[arg-type]
    assert storage.asked == [frozenset(states)]


def test_a_queue_that_cannot_be_read_is_shown_not_hidden(qapp) -> None:
    """Golden rule 8: a "?" and a tooltip, not a silently missing indicator."""
    indicator = RescueQueueIndicator(_Storage(sqlite3.OperationalError("locked")), lambda: True)  # type: ignore[arg-type]
    assert indicator.refresh() is None
    assert indicator.text() == "Walled PDFs: ?" and not indicator.isHidden()
    assert "could not be read" in indicator.toolTip()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_rescue_queue_indicator.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `gui/rescue_queue_indicator.py`**

```python
"""The status bar's count of walled PDFs waiting for the reader (#480, stage 3)."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton, QWidget

from ..browser_rescue import awaiting_reader_states
from ..browser_rescue_text import REVIEW_QUEUE_UNREADABLE
from ..constants import RESCUE_POLL_INTERVAL_MS
from ..exceptions import SQLiteError

if TYPE_CHECKING:
    from ..storage import LiteStorage

logger = logging.getLogger(__name__)

TOOLTIP = "Open the review queue: PDFs behind a robot check that wait for you"
_UNKNOWN = "?"


def indicator_text(count: int | str) -> str:
    """The indicator's text.

    Args:
        count: The count, or "?" when it could not be read.

    Returns:
        "Walled PDFs: N".
    """
    return f"Walled PDFs: {count}"


class RescueQueueIndicator(QPushButton):
    """"Walled PDFs: N", the entries waiting for the reader; hidden at 0 (D4).

    Read on :meth:`refresh`, which the window calls on every change it hears
    of, and every ``poll_ms`` for entries other processes wrote.
    """

    def __init__(
        self,
        storage: LiteStorage,
        hidden_pass_runs: Callable[[], bool],
        poll_ms: int = RESCUE_POLL_INTERVAL_MS,
        parent: QWidget | None = None,
    ) -> None:
        """Make the indicator, hidden until :meth:`refresh` finds entries.

        Args:
            storage: The queue.
            hidden_pass_runs: Whether a hidden pass will visit pending entries.
            poll_ms: How often the count is read again.
            parent: The status bar's window.
        """
        super().__init__(parent)
        self.setObjectName("rescueQueueIndicator")
        self.setFlat(True)
        self.setToolTip(TOOLTIP)
        self.hide()
        self._storage = storage
        self._hidden_pass_runs = hidden_pass_runs
        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self.refresh)

    def start(self) -> None:
        """Read the count now, then every poll interval."""
        self._timer.start()
        self.refresh()

    def refresh(self, *_args: object) -> int | None:
        """Read the count again.

        Connected to signals with arguments (an entry), which are ignored.

        Args:
            *_args: Ignored.

        Returns:
            The count; ``None`` when the queue could not be read (logged, and
            shown as "?").
        """
        try:
            count = self._storage.count_rescue_entries(awaiting_reader_states(self._hidden_pass_runs()))
        except (sqlite3.Error, SQLiteError) as e:
            logger.error("The browser rescue queue could not be counted: %s", e)
            self.setText(indicator_text(_UNKNOWN))
            self.setToolTip(REVIEW_QUEUE_UNREADABLE)
            self.show()
            return None
        self.setText(indicator_text(count))
        self.setToolTip(TOOLTIP)
        self.setVisible(count > 0)
        return count
```

- [ ] **Step 4: Write the failing window-wiring tests** (append to `tests/test_app_browser_rescue_startup.py`, in the file's stand-in style)

```python
from PySide6.QtCore import QObject, Signal


class _FakeService(QObject):
    rescued = Signal(object)
    entry_changed = Signal(object)

    def wake(self, *_args: object) -> None:
        """Ignored."""


def _window(**extra: Any) -> SimpleNamespace:
    calls: list[tuple[str, Any]] = []
    window = SimpleNamespace(
        calls=calls,
        config=SimpleNamespace(discovery=SimpleNamespace(browser_rescue_hidden=True)),
        _rescue_service=None,
        rescue_indicator=SimpleNamespace(refresh=lambda *a: calls.append(("refresh", a))),
        interrogation_tab=SimpleNamespace(on_rescue_entry_changed=lambda e: calls.append(("tab", e))),
        systematic_review_tab=SimpleNamespace(transparency_outcome_ready=SimpleNamespace(connect=lambda f: None)),
        research_questions_tab=SimpleNamespace(transparency_outcome_ready=SimpleNamespace(connect=lambda f: None)),
        _on_pdf_rescued=lambda e: calls.append(("rescued", e)),
        **extra,
    )
    return window


def test_a_verdict_refreshes_the_indicator_and_tells_the_tab() -> None:
    """entry_changed reaches the indicator and the interrogation tab's wait."""
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    window = _window()
    service = _FakeService()
    app_module.LiteMainWindow.attach_rescue_service(window, service)  # type: ignore[arg-type]
    service.entry_changed.emit("entry")
    assert ("tab", "entry") in window.calls
    assert any(c[0] == "refresh" for c in window.calls)


@pytest.mark.parametrize("service, on, runs", [(None, True, False), (object(), False, False), (object(), True, True)])
def test_the_hidden_pass_runs_only_with_a_service_and_the_setting(service: Any, on: bool, runs: bool) -> None:
    """D4's "hidden pass runs": QtWebEngine started and the setting on."""
    window = _window()
    window._rescue_service = service
    window.config.discovery.browser_rescue_hidden = on
    assert app_module.LiteMainWindow._hidden_pass_runs(window) is runs  # type: ignore[arg-type]


def test_a_queued_rescue_wakes_the_service_and_refreshes() -> None:
    """The tab's enqueue wakes the hidden pass at once and updates the count."""
    woken: list[bool] = []
    window = _window()
    window._rescue_service = SimpleNamespace(wake=lambda *a: woken.append(True))
    app_module.LiteMainWindow._on_rescue_queued(window)  # type: ignore[arg-type]
    assert woken == [True] and any(c[0] == "refresh" for c in window.calls)


def test_a_rescue_is_said_without_hidden() -> None:
    """D11: the window's rescue is not the hidden browser's."""
    assert app_module.rescued_pdf_message("T", True, False) == (
        "Fetched the PDF of “T” in the built-in browser; re-checking its transparency."
    )
```

Update every existing assertion on `rescued_pdf_message` from "in the hidden browser" to "in the built-in browser". Find them with `grep -rn "hidden browser" tests/test_app_browser_rescue_startup.py`.

- [ ] **Step 5: Run them to verify they fail**

Run: `pytest tests/test_app_browser_rescue_startup.py tests/test_rescue_queue_indicator.py -q`
Expected: the indicator tests PASS. The new app tests FAIL with `AttributeError: type object 'LiteMainWindow' has no attribute '_hidden_pass_runs'`.

- [ ] **Step 6: Wire the window** (`gui/app.py`)

- Imports: `from .rescue_queue_indicator import RescueQueueIndicator`; `from .review_queue_dialog import ReviewQueueDialog` (it imports no QtWebEngine; the subprocess test from stage 2 keeps that true).
- `__init__`: `self._review_queue: ReviewQueueDialog | None = None` beside `_rescue_service`.
- `_setup_ui`, after the status bar is made and before the token label:

```python
        # Walled PDFs waiting for the reader (#480, stage 3); hidden at 0
        self.rescue_indicator = RescueQueueIndicator(self.storage, self._hidden_pass_runs, parent=self)
        self.rescue_indicator.clicked.connect(lambda: self.show_review_queue())
        self.status_bar.addPermanentWidget(self.rescue_indicator)
        self.rescue_indicator.start()

        # The app's only menu: the review queue opens from here at a count of 0 too (D5)
        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction("Walled PDFs…").triggered.connect(lambda: self.show_review_queue())
```

- After the interrogation tab is made: `self.interrogation_tab.set_hidden_pass_check(self._hidden_pass_runs)` (Task 7).
- `_connect_signals`: `self.interrogation_tab.rescue_queued.connect(self._on_rescue_queued)` and `self.interrogation_tab.review_queue_requested.connect(self.show_review_queue)` (Task 7's signals).
- `_show_settings`: after a save, `self.rescue_indicator.refresh()` (the setting changes what counts).
- `attach_rescue_service`: add

```python
        service.entry_changed.connect(self.rescue_indicator.refresh)
        service.entry_changed.connect(self.interrogation_tab.on_rescue_entry_changed)
```

- `browser_rescue_unavailable`: add `self.rescue_indicator.refresh()` (pending now counts).
- New methods:

```python
    def _hidden_pass_runs(self) -> bool:
        """Whether a hidden pass will visit pending entries (D4).

        Returns:
            ``True`` when QtWebEngine started and the setting is on.
        """
        return self._rescue_service is not None and self.config.discovery.browser_rescue_hidden

    def _on_rescue_queued(self) -> None:
        """The interrogation tab queued a walled PDF: visit soon, and count it."""
        if self._rescue_service is not None:
            self._rescue_service.wake()
        self.rescue_indicator.refresh()

    def show_review_queue(self, entry_id: int | None = None) -> None:
        """Open the review queue window, made once, focused on an entry if given.

        Args:
            entry_id: The entry to select, or ``None``.
        """
        if self._review_queue is None:
            make_view = None
            if self._rescue_service is not None:
                from .rescue_view import make_rescue_view

                make_view = make_rescue_view
            self._review_queue = ReviewQueueDialog(
                self.storage, self._rescue_service, make_view, self._hidden_pass_runs, parent=self
            )
            self._review_queue.queue_changed.connect(self.rescue_indicator.refresh)
        else:
            self._review_queue.refresh()
        self._review_queue.show()
        self._review_queue.raise_()
        self._review_queue.activateWindow()
        if entry_id is not None:
            self._review_queue.focus_entry(entry_id)
```

The QtWebEngine import stays lazy because `rescue_view` is imported only inside `show_review_queue`, and only with a service.

- `rescued_pdf_message`: "in the hidden browser" becomes "in the built-in browser" (D11), in its docstring too.

- [ ] **Step 7: Run the tests**

Run: `pytest tests/test_app_browser_rescue_startup.py tests/test_rescue_queue_indicator.py tests/test_a_failed_transparency_analysis_reaches_the_reader.py -q`
Expected: PASS, including `test_importing_the_app_does_not_load_qtwebengine`.

- [ ] **Step 8: Commit**

```bash
git add src/bmlibrarian_lite/gui/rescue_queue_indicator.py src/bmlibrarian_lite/gui/app.py \
  tests/test_rescue_queue_indicator.py tests/test_app_browser_rescue_startup.py
git commit -m "feat(gui): Walled PDFs indicator, Tools menu, and the review queue's wiring (#480)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Docs, the lodged issue, and the whole-branch gate

**Files:**
- Modify: `doc/developer/browser_rescue.md`, `doc/user/guide.md`, `CLAUDE.md`, `HANDOVER.md`
- Create: the GitHub issue for D7

- [ ] **Step 1: Developer notes** (`doc/developer/browser_rescue.md`)

- Title paragraph: "(#480, stages 2 and 3)".
- **Modules**: add `browser_rescue_text.py`, `gui/review_queue_dialog.py`, `gui/rescue_view.py` (the only QtWebEngineWidgets import) and `gui/rescue_queue_indicator.py`. Add the service's shown visits and walk.
- New section **Shown visits and the walk**: D1 (one visit at a time, the hidden pass held while a shown visit is asked for), D2 (a person's visit is judged by its download only, and leaving it records nothing), D3 (`createWindow`), the walk's re-read of each entry and its visible timeout, and `entry_changed`.
- New section **The interrogation tab**: D8 and D9 as rules, and D10.
- **States**: add "any open state --reader--> dismissed (`dismissed_state`); its last verdict is kept".
- **Accepted limits**: remove "Until stage 3 the reader sees no persistent indicator". Add "A rescue after the tab's wait ended is not loaded into the tab; opening the citation again finds it" and "The *Fetch PDF* button's path is not queued (Deferred: #NEW)".
- **Testing**: name the new harness scenarios.

- [ ] **Step 2: User guide** (`doc/user/guide.md`, after "Bot-walled PDFs (hidden browser)")

Add a section "#### The review queue (Walled PDFs)", covering: the "Walled PDFs: N" indicator and *Tools → Walled PDFs…*; selecting an entry opens its page, where you tick the robot check and the PDF is saved; *Walk the queue*; *Dismiss*; *Open in system browser* (nothing is saved from it); the filter; and the interrogation tab's "Trying the publisher's page in a browser…" with what follows each outcome. Keep the privacy paragraph's facts: the same profile, the same cookies.

- [ ] **Step 3: CLAUDE.md**

In the `gui/` line, add: `review_queue_dialog (walled PDFs' review queue), rescue_queue_indicator, rescue_view (the only QtWebEngineWidgets import)`. Add `browser_rescue_text.py # What the reader reads about the rescue queue` after `browser_rescue.py`.

- [ ] **Step 4: Lodge D7's issue**

```bash
gh issue create --title "Desktop: the Fetch PDF button's walled PDF is not queued for the browser" --body "$(cat <<'EOF'
Stage 3 of #480 queues a walled open-access PDF that the interrogation tab's
*citation* path hits (`load_from_citation` → `FulltextDiscoveryWorker`) at the
waiting priority. The *Fetch PDF* button (`_fetch_pdf_from_identifier` →
`PDFDiscoveryWorker`) does not queue: its metadata is typed in or carries no
storage `id`, and a minted `doi-…` ID is the identity question open in #533.

Expected: a walled PDF met through either button goes to the review queue.
Needs: a document identity for an identifier-only fetch (decide with #533),
then the same `_begin_rescue_wait` path, with `PDFDiscoveryWorker` carrying its
`lookups` as `FulltextDiscoveryWorker` does.
EOF
)"
```

Use the number it prints wherever this plan writes `#NEW`.

- [ ] **Step 5: The whole-branch gate**

Run, each to completion:

```bash
pytest tests/ -q
QT_QPA_PLATFORM=offscreen python tests/browser_rescue_harness.py > /dev/null   # exit 0
ruff check src/bmlibrarian_lite/browser_rescue.py src/bmlibrarian_lite/browser_rescue_text.py \
  src/bmlibrarian_lite/gui/review_queue_dialog.py src/bmlibrarian_lite/gui/rescue_queue_indicator.py \
  src/bmlibrarian_lite/gui/rescue_view.py
python .github/scripts/lint_delta.py --base-ref origin/master
```

Expected: 0 failures, harness exit 0, and no new ruff or mypy findings.

Also run the real app once by hand, if a display is available: `bmlibrarian-lite-gui`, open *Tools → Walled PDFs…*, close it, and quit. stderr must not show the profile-release warning.

- [ ] **Step 6: HANDOVER.md**

Mark stage 3 as landed (pending review). The desktop browser rescue is complete. Keep the binding rules from stages 2 and 3 in a compressed section, and drop the "Stage 3, planning" bullets. Move #480's remaining scope (the app ports' spec) to *Next up*, and add the D7 issue.

- [ ] **Step 7: Commit, push, PR**

```bash
git add doc/developer/browser_rescue.md doc/user/guide.md CLAUDE.md HANDOVER.md
git commit -m "docs: the review queue window and the reader's priority path (#480)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin feat/browser-rescue-stage3-480
gh pr create --base master --title "Browser rescue, stage 3: the review queue window and the reader's priority path (#480)" --body "..."
```

The PR body: the summary, D1–D12 as decided, the Review Focus tests, the harness results, and "Closes #480". Deferred: #NEW, #524, #525, #527, #528, #530–#533. Write no closing keyword before any of those. End the body with the attribution line.

---

## Self-review

1. **Spec coverage (section 4).**
   - The window: non-modal, opened from the status-bar indicator ("Walled PDFs: 3", hidden at 0) and from the menu (Tasks 6 and 7; D5).
   - The list's title, host, who named the address, state and last verdict, with `needs_person` by default and a filter for the rest (Task 6, D4).
   - The view on the same profile loading the selected address, with tick → PDF → the hidden pass's acceptance path → `rescued` → the next entry opens (Tasks 3, 4 and 6).
   - *Dismiss* and *Open in system browser* (Task 6). *Walk the queue* with `RESCUE_VISIBLE_TIMEOUT_MS` (Task 5).
   - The hidden pass paused while the window drives its view, with shared per-host limiters (Task 4, D1; `rescue_delay_ms` for shown visits).
   - A rescue re-analyses: already in stage 2 through `rescued`. A shown visit's rescue goes through the same `_save` (Task 4).
   - The interrogation tab enqueues at priority 1 and shows "Trying the publisher's page in a browser…" with *Cancel*. A rescue loads the PDF, `needs_person` offers the queue focused on the entry, and anything else keeps the fallback. OpenAthens is untouched (Task 7).
   - Testing: "needs_person → rescued" carried in (Task 2). The own-page check with a shown view sharing the profile (Task 3 harness, D3). `RescueEntry.retry_after` shown (Tasks 1 and 6).
2. **Placeholder scan:** every code step carries its code. Task 9's PR body is described rather than written because it quotes results that do not exist yet. `#NEW` is set in Task 9 Step 4.
3. **Type consistency:**
   - `visit_shown(entry, view)`, `end_shown_visit()`, `walk(entry_ids, view)`, `stop_walk()`, `walking`, `shown_entry_id`, `entry_changed`, `shown_visit_started`, `shown_visit_ended(entry, verdict | None)` and `walk_ended` are used the same way in Tasks 4–8.
   - `wait_outcome(entries, watched, answered)` and `wait_watch(entries, hidden_pass_runs, now)` match between Tasks 1 and 8.
   - `dismiss_entry(queue, entry_id) -> RescueEntry | None` matches between Tasks 1 and 6.
   - `count_rescue_entries`, `rescue_entries_for_document`, `rescue_entry` and `dismiss_rescue` match between Tasks 2, 5, 6, 7 and 8.
4. **Review Focus:** the five items are pinned in Tasks 4, 5, 6 and 7. Also pinned in their tasks: a stored address that is no web address (Task 6), a queue that cannot be read or written (Tasks 1, 2, 5, 6, 7 and 8), and a rescued PDF missing from disk (Task 7).
