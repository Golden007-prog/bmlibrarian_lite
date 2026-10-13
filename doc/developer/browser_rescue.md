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

- `browser_rescue.py`: the rules, with no Qt and no SQLite. States, outcomes,
  verdicts (`page_verdict`, `download_verdict`), `next_state`, which failures
  are queued (`rescue_requests`, `queue_challenged_copies`), the pacing delay
  (`rescue_delay_ms`) and `reanalyse_rescued`.
- `rescue_queue_store.py`: the queue's table. SQL only; `LiteStorage` opens the
  connection. `record_verdict` runs its own transaction, because the MCP server
  and the CLI write the same table from other processes.
- `gui/rescue_attempt.py`: `RescueAttempt`, one visit of one address, ending in
  exactly one verdict; and `rescue_profile`.
- `gui/browser_rescue_service.py`: `BrowserRescueService`, the hidden pass. One
  visit at a time, driven by signals and timers; nothing waits on it.
- `gui/web_engine.py`: `prepare_web_engine()`, QtWebEngine's start-up
  requirement.

## States

```
                  +--> rescued      (PDF arrived and was saved)
                  +--> no_pdf       (page loaded, unchallenged, no PDF)
pending --visit-->+--> gone         (404 or 410)
   ^              +--> too_large    (PDF over MAX_PDF_SIZE)
   |              +--> needs_person (still a challenge)
   +--transport---+
```

`dismissed` (the reader set it aside) and `superseded` (another address of the
same document was rescued) are reached outside a visit. `pending` and
`needs_person` are the *open* states; the others are settled and never change.

A **transport** outcome learnt nothing about the article, so the entry stays
`pending` (unreachable is not absent). Transport covers: a network error, a
renderer crash, a stalled or interrupted download, a PDF that arrived and could
not be kept, a visit with no main-frame answer before `RESCUE_HIDDEN_TIMEOUT_MS`,
and a load failure in Chromium's internal error domain other than ERR_ABORTED
(-3, which a script-driven navigation produces).

- **D2.** `too_large` is its own state: the PDF is real, the application just
  does not download that much.
- **D3.** A transport failure is retried no sooner than
  `RESCUE_RETRY_DELAY_SECONDS` (15 minutes) later. After `MAX_RESCUE_ATTEMPTS`
  (3) the entry becomes `needs_person` and waits for stage 3's window.

Two rules inside a visit: once a download is accepted, later page answers are
ignored, and a visit that ends mid-download cancels the download and discards
the partial file. If the verdict cannot be written to storage, the whole hidden
pass pauses for `RESCUE_RETRY_DELAY_SECONDS` rather than revisit the same entry
at once.

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
pass runs only while `discovery.browser_rescue_hidden` is on, read live.

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
- Only one visit runs at a time.
- Until stage 3 the reader sees no persistent indicator of the queue.
