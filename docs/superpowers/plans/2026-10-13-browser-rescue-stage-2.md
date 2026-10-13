# Browser rescue, stage 2: queue, hidden service, re-analysis, #483 removal

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `challenged` open-access PDF is queued, fetched by a hidden QtWebEngine page on the GUI thread, saved where the cached-PDF tier finds it, and the document's transparency analysis re-runs. The Playwright fallback (#483) is removed. Python only.

**Architecture:** Pure logic in `browser_rescue.py` (state transitions, outcome to verdict, `rescue_requests`); persistence in `LiteStorage` (`browser_rescue_queue`); the engine in `gui/browser_rescue_service.py`, driven only by signals and `QTimer`s. Discovery stays free of storage; callers that hold a document and its `LookupRecord` enqueue.

**Tech Stack:** Python 3.12, PySide6 `<6.12` (QtWebEngineCore, QtWebEngineWidgets), SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-10-12-browser-rescue-design.md`, sections 2, 3, 5 and the "rescued re-analyses" paragraph of 4. Stage 1 (the `challenged` kind, `bot_walls.challenge_markers`) is already on `feat/browser-rescue-480`. Stage 3 (the review window, interrogation priority path) is out of scope here.

## Global Constraints

- No impersonation: QtWebEngine's own User-Agent, no automation protocol, no stealth.
- Constants in `constants.py`: `RESCUE_HIDDEN_TIMEOUT_MS` = 25000, `RESCUE_DOWNLOAD_STALL_MS`, `RESCUE_POLL_INTERVAL_MS`, `MAX_RESCUE_ATTEMPTS`; reuse `MAX_PDF_SIZE`, `CHALLENGE_BODY_PREFIX_BYTES`.
- Queue is unique on `(document_id, address)`; enqueue is idempotent (an existing entry, open or settled, is untouched; only a higher priority raises an open entry's).
- States: `pending`, `needs_person`, `rescued`, `no_pdf`, `gone`, `dismissed`, `superseded`. A transport failure or renderer crash leaves `pending`, `attempts + 1`; at `MAX_RESCUE_ATTEMPTS` it becomes `needs_person`.
- A 404/410 stays gone; a not-challenged 403 is never queued.
- A saved PDF must begin `%PDF`, is written to a `.part` file (`pdf_download.partial_download_path`) and renamed into place; cancelled past `MAX_PDF_SIZE` and on `RESCUE_DOWNLOAD_STALL_MS` without progress.
- Persistent `QWebEngineProfile` under `~/.bmlibrarian_lite/browser_profile/`; `PdfViewerEnabled` off; page deleted before the profile at quit.
- `run_lite_app` sets `AA_ShareOpenGLContexts` and imports `QtWebEngineCore` before `QApplication`; the test Qt preflight does the same.
- Engine tests run in their own subprocess (not the shared `QApplication`, #516/#517) and fail rather than skip when QtWebEngine cannot start.
- A rescue never overwrites a newer build's stored row (`may_replace_stored`).
- Golden rules: type hints, Google docstrings, no magic numbers, pure functions in focused modules. Gates: `pytest tests/`, `lint_delta.py --base-ref origin/master`; Swift/Kotlin untouched, but CI runs them.

## Review Focus

1. A direct PDF, a JS challenge that sets a cookie then reloads to the PDF, a 404, an HTML abstract page, a stalling download, an oversize download, a non-`%PDF` "PDF": each a distinct verdict (Task 3).
2. Quit while a page is loading: the entry stays `pending`, no crash (Task 3).
3. Two processes: an entry written by MCP/CLI is picked up by the poll (Task 2, 3).
4. A rescued sibling supersedes the document's other open entries (Task 1, 2).
5. A rescue of a document whose stored row a newer build wrote does not overwrite it (Task 4).
6. Removing Playwright leaves no `use_browser_fallback`/`browser_headless`/`--no-browser` reference (Task 5).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/bmlibrarian_lite/browser_rescue.py` (create) | `RescueRequest`, `rescue_requests`, state transitions, outcome to verdict: pure |
| `src/bmlibrarian_lite/storage.py` (modify) | `browser_rescue_queue` table, `enqueue_rescue`, `next_pending_rescue`, `apply_rescue_transition`, listing |
| `src/bmlibrarian_lite/rate_limit.py` (modify) | public non-sleeping `next_slot(host)` |
| `src/bmlibrarian_lite/gui/browser_rescue_service.py` (create) | `BrowserRescueService(QObject)`: profile, hidden page, download acceptance, `rescued` signal |
| `src/bmlibrarian_lite/transparency/manager.py`, interrogation workers, `mcp_server.py` (modify) | call `rescue_requests` and `storage.enqueue_rescue` |
| `src/bmlibrarian_lite/transparency/` (modify) | `TransparencyManager.reanalyse(document_id)` |
| `src/bmlibrarian_lite/gui/app.py`, settings dialog (modify) | start-up flags, service start after window shown, "hidden browser" setting |
| `pdf_discovery.py`, `fulltext_discovery.py`, `study_transparency_analyzer/`, `transparency/assessment.py`, `mcp_server.py`, CLI, `doc/developer/guide.md` (modify) | #483 removal |
| `tests/test_browser_rescue.py`, `tests/test_storage_rescue_queue.py`, `tests/engine/` (create) | pure, storage, subprocess engine tests |

## Tasks

### Task 1: Pure rescue logic

**Files:** Create `src/bmlibrarian_lite/browser_rescue.py`, `tests/test_browser_rescue.py`; modify `constants.py`.

**Interfaces:** Produces `RescueState` (str enum), `RescueRequest(document_id, doi, pmid, title, address, host, service, priority)`, `rescue_requests(document: LiteDocument, record: LookupRecord) -> list[RescueRequest]` (only `challenged` failures with an address), `next_state(state, attempts, outcome) -> tuple[RescueState, int]`, `verdict_for(download_ok, markers, http_status, transport_error) -> Verdict`.

- [ ] Failing tests: every transition in the spec's diagram; attempt cap to `needs_person`; `rescue_requests` skips a plain 403 and an address-less challenge; 404 with challenge markers is `gone`.
- [ ] Implement; run `pytest tests/test_browser_rescue.py`; commit.

### Task 2: The queue in storage

**Files:** Modify `storage.py`, `rate_limit.py`; create `tests/test_storage_rescue_queue.py`.

**Interfaces:** Consumes Task 1. Produces `LiteStorage.enqueue_rescue(request) -> bool`, `next_pending_rescue() -> QueueEntry | None` (priority desc, then `created_at`), `settle_rescue(entry_id, new_state, attempts, verdict_json)` (supersedes siblings on `rescued`), `count_open_rescues()`; `RateLimiter.next_slot(host) -> float` (seconds to wait, never sleeps).

- [ ] Failing tests: idempotent enqueue (open and settled), priority raise, supersede, `CREATE TABLE IF NOT EXISTS` on an old database, a row written through a second connection is seen.
- [ ] Implement; run; commit.

### Task 3: The hidden service

**Files:** Create `gui/browser_rescue_service.py`, `tests/engine/` (loopback server + subprocess runner); modify `constants.py`, `gui/app.py`.

**Interfaces:** Consumes Tasks 1-2. Produces `BrowserRescueService(storage, parent)` with `start()`, `stop()`, `wake()` (thread-safe), signal `rescued(document_id: str)`; it saves into `generate_pdf_path(...)`.

- [ ] Failing engine tests (subprocess) for each Review Focus 1 case plus quit-mid-load; assert the run fails, not skips, when QtWebEngine cannot start.
- [ ] Implement: profile, page without a view, `downloadRequested` into `.part`, size and stall guards, `%PDF` check, rename, title/text through `challenge_markers`, `loadingChanged` 404/410, `renderProcessTerminated`; one entry at a time; `QTimer` poll at `RESCUE_POLL_INTERVAL_MS`.
- [ ] Wire start-up (`AA_ShareOpenGLContexts`, early `QtWebEngineCore` import) and start after the main window is shown; run; commit.

### Task 4: Enqueue sites, re-analysis, setting

**Files:** Modify `transparency/manager.py`, interrogation workers, `mcp_server.py`, settings dialog, `LiteConfig`; tests beside each.

**Interfaces:** Consumes Tasks 1-3. Produces `TransparencyManager.reanalyse(document_id)`; config key "try bot-walled PDFs in a hidden browser" (default on; off still collects entries).

- [ ] Failing tests: completion enqueues; a plain 403 never does; `rescued` triggers `reanalyse`; a newer-build row is not overwritten; the badge loses "Limited certainty" once the cached PDF is found.
- [ ] Implement; run; commit.

### Task 5: Remove the Playwright fallback (#483)

**Files:** `pdf_discovery.py`, `fulltext_discovery.py`, `study_transparency_analyzer/`, `transparency/assessment.py`, `mcp_server.py`, transparency CLI, `doc/developer/guide.md`, `pyproject.toml` extras, affected tests.

- [ ] Remove `BrowserSession`, `get_browser_session`, `close_browser_session`, `_try_browser_download`, `_try_direct_content`, the `blocked_oa_sources` routing, `use_browser_fallback`, `browser_headless`, `--no-browser`; `grep -ri playwright` is clean outside history.
- [ ] Run the full suite and lint delta; commit.

### Task 6: Live acceptance and docs

- [ ] Run the spike's 41 addresses (`spikes/2026-10-04-qtwebengine-hidden.jsonl`) through the real hidden pass. Expected: PMC near 12/12, ScienceDirect `needs_person`, controls `no_pdf`. Record rows beside the spike's, and the counts in the contract.
- [ ] Update `fulltext_retrieval.md` and `HANDOVER.md`; full gates; commit. Do not use a closing keyword for #480 (stage 3 is open); stage 2's PR may close #483.
