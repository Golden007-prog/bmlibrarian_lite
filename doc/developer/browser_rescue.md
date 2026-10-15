# Browser Rescue

Developer notes for the desktop's browser rescue (#480, stage 2). Design and
decisions: `docs/superpowers/specs/2026-10-12-browser-rescue-design.md`. What
the embedded browser passes and what it does not:
`doc/developer/unpaywall_pdf_survey/spikes/README.md`.

## What It Does

An open-access PDF (Unpaywall's or OpenAlex's) whose host answered our client
with a bot wall's challenge page is **queued** instead of given up. Later, the
GUI **visits** its address in a hidden QtWebEngine page, **saves** the PDF
where discovery's cached-PDF tier finds it (`rescue_pdf_path`, in the PDF
library), and **re-analyses** the document so its transparency badge reflects
the full text.

## Modules

- `browser_rescue.py`: the rules, with no Qt and no database access (it imports `sqlite3` only to
  catch its errors). States, outcomes,
  verdicts (`page_verdict`, `download_verdict`), `next_state`, which failures
  are queued (`rescue_requests`, `queue_challenged_copies`), the pacing delay
  (`rescue_delay_ms`), `penalise_host_for_answer` and `reanalyse_rescued`.
- `rescue_queue_store.py`: the queue's table. SQL only; `LiteStorage` opens the
  connection. `record_verdict` runs its own transaction, because the MCP server
  writes the same table from another process. It returns a `RecordedVerdict`:
  the entry as it now stands and whether this verdict was applied, so a rescue
  another process recorded first is not announced twice.
- `gui/rescue_attempt.py`: `RescueAttempt`, one visit of one address, ending in
  exactly one verdict; and `rescue_profile`.
- `gui/browser_rescue_service.py`: `BrowserRescueService`, the hidden pass. One
  visit at a time, driven by signals and timers; nothing waits on it.
- `gui/web_engine.py`: `prepare_web_engine()`, QtWebEngine's start-up
  requirement. `gui/app.py` imports the service lazily (`start_browser_rescue`)
  and guards the preparation (`prepare_browser_rescue`), so an app whose
  QtWebEngine cannot load starts without the hidden pass, with a warning in the
  log, a status-bar message (while the setting is on) and the setting shown
  disabled in Settings (`LiteMainWindow.browser_rescue_unavailable`).

## States

```
                  +--> rescued      (PDF arrived and was saved)
                  +--> no_pdf       (page loaded, unchallenged, no PDF)
pending --visit-->+--> gone         (404 or 410)
   ^              +--> too_large    (PDF over MAX_PDF_SIZE)
   |              +--> needs_person (still a challenge)
   +--transport---+
   |
   +--transport, attempts >= cap--> needs_person
needs_person --visit, any outcome--> its answer's state
needs_person --transport--> needs_person
```

`dismissed` (the reader set it aside) and `superseded` (another address of the
same document was rescued) are reached outside a visit. `pending` and
`needs_person` are the *open* states; the others are settled and never change.

A **transport** outcome learnt nothing about the article, so the entry stays
`pending` (unreachable is not absent). Transport covers: a network error, a
renderer crash, a stalled or interrupted download, an empty download, a PDF
that arrived and could not be kept, a visit that could not start (no profile
or PDF folder), a visit with no main-frame answer before
`RESCUE_HIDDEN_TIMEOUT_MS` (`page_verdict` with no status), a page read while a
navigation it started is still open (the page in view is the one being left;
a challenge it shows still counts), and a load failure in Chromium's internal
error domain other than ERR_ABORTED (-3, which a script-driven navigation
produces). A browser status that is no HTTP status counts as none.

A page that answered with a status that says "not now" and shows no challenge
marker (408, 425, 429, any 5xx) is transport too: a temporary server error must
not settle an entry. A 401 or 403 with no marker is a `challenge` with no
markers and waits for a person, since the HTTP client was already walled at that
address. Markers win over the status; 404 and 410 stay `gone`; any other
answered page with no marker and no PDF is `no_pdf`. A download that is not a
PDF goes through the same marker rules: a wall served as a download is a
`challenge`, an empty one is transport, and only the rest is `no_pdf`. A main-frame 429 or 503 also penalises
the host's shared limiter (`limiter_for(host).penalise(retry_after)`, with the
answer's `Retry-After`), except on loopback, so the rescue and the HTTP client
back off together. The hidden page is muted.

- **D2.** `too_large` is its own state: the PDF is real, the application just
  does not download that much.
- **D3.** A transport failure is retried no sooner than
  `RESCUE_RETRY_DELAY_SECONDS` (15 minutes) later. After `MAX_RESCUE_ATTEMPTS`
  (3) the entry becomes `needs_person` and waits for stage 3's window.

Three rules inside a visit: a visit accepts only a download its own page
started (the profile is shared, and a page an earlier visit let go of may still
start one: another article's PDF); once a download is accepted, later page
answers are ignored; and a visit that ends mid-download cancels the download
and discards the partial file. If the verdict cannot be written to storage, the service keeps
it and tries the write again, never the visit: the entry is still pending, and
visiting it again would ask the publisher twice. The retry waits
`RESCUE_VERDICT_SAVE_RETRY_SECONDS` (30 s), doubling with each further failure
up to `RESCUE_RETRY_DELAY_SECONDS`, and a successful write resets the wait.
While a verdict is unsaved no new entry is taken, since the next write would
most likely fail too. At quit an unsaved verdict gets one last try, and a
rescue it records is not announced: re-analysing would start an analysis while
the app quits, and the next session's analysis finds the PDF. Any error
writing a verdict holds it like a database lock does; any other unexpected
error in the pass's slots is logged and never leaves the pass busy (#525).

## Who Queues

`assess_document` (`transparency/assessment.py`, the body both analysis paths
share) and the MCP server queue the challenged copies a discovery left. Stage 3
adds the interrogation tab. A queue that cannot be written is logged, never
raised: the analysis must not fail for it.

**D1.** A challenged copy is not a paywall. Before stage 2, a challenged 401,
403 or `text/html` 200 at a paywall-looking address took the paywall branch, so
the reader was told a source refused access. It now is a challenged copy and is
queued.

**D7.** The service wakes on the systematic review and Research Questions tabs'
analysis outcomes (an analysis is where walled PDFs are queued); a 60 s poll
(`RESCUE_POLL_INTERVAL_MS`) catches entries other processes wrote. The hidden
pass runs only while `discovery.browser_rescue_hidden` is on. It is read when an
entry is taken and again when its visit starts, after the host's pacing slot,
which a throttling host can push minutes away.

A rescue re-analyses its document when storage holds it
(`reanalyse_rescued`). If that document's analysis is already pending, it may
have passed the cached-PDF tier before the PDF arrived, so the manager analyses
it once more when that one ends (`analyze_document(..., again_if_pending=True)`);
never after `stop()`.

Pacing: the browser does not go through `requests`, so each visit claims its
host's slot with `limiter_for(host).claim()` without sleeping and is scheduled
for then (`rescue_delay_ms`).

## QtWebEngine Facts the Code Depends On

- `prepare_web_engine()` must run **before any `QApplication`** exists: it sets
  `AA_ShareOpenGLContexts` and imports `QtWebEngineCore`. Otherwise the first
  page fails and Qt only warns. The application, the harness and CI's
  preflight all call it; it raises if a `QApplication` exists.
- The profile is persistent and owner-only (cookies are the reader's
  clearances), with its PDF viewer **off**, so a PDF arrives through
  `downloadRequested` instead of rendering.
- QtWebEngine's own User-Agent and no automation protocol: what passed the walls
  in the spikes. Nothing is impersonated.
- A load failure's domain says whether the network failed; ERR_ABORTED (-3) is
  not a failure.
- The page is judged by its serialised DOM, after a short grace
  (`RESCUE_PAGE_READ_GRACE_MS`).
- **Page before profile at quit**: pages are deleted first, then the profile.
  `shutdown()` ends a visit with no verdict, so its entry stays as it was.

## Testing

Everything that pumps QtWebEngine runs in `tests/browser_rescue_harness.py`, in
a **subprocess**, against a loopback publisher. Pumping the suite's shared
`QApplication` inherits earlier tests' dangling timers (#516, #517).
`tests/test_browser_rescue_engine.py` runs the harness once and asserts on its
JSON. Run it alone:

```bash
QT_QPA_PLATFORM=offscreen python tests/browser_rescue_harness.py
```

A QtWebEngine that cannot start **fails** these tests; it never skips them, as
a skip would leave CI green with the feature untested. CI sets
`QTWEBENGINE_DISABLE_SANDBOX` (its runner cannot sandbox Chromium) and runs
`.github/scripts/qt_preflight.py` first.

## Accepted Limits

- A `no_pdf` or `gone` verdict does not rewrite the stored sentence the reader
  saw ("could not be asked"). Lodged as #524.
- Only one visit runs at a time, so a host whose pacing slot is minutes away
  holds up every other host's entries meanwhile (#531).
- A fetched PDF is checked for `%PDF` and its size, not against the article's
  title (#532).
- An entry is unique by document ID and address, and the MCP server queues
  under its own IDs, so one address can be queued twice (#533).
- Until stage 3 the reader sees no persistent indicator of the queue.
