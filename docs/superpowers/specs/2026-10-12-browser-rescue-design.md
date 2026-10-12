# Bot-walled PDFs: the `challenged` kind, an embedded browser and a review queue

Sub-projects 2 and 3 of #480, after the machine channels
(`2026-10-04-fulltext-machine-channels-design.md`). A PDF that an open-access
source names, and that a bot wall keeps from our client, is recognised as
walled (`challenged`), queued, and fetched by a real embedded web view: first
hidden and unattended, then, for what is left, in a window where the reader
ticks the box. The `challenged` kind lands on all three platforms; the browser
and the queue on the desktop only. Replaces the desktop's Playwright fallback
(#483).

## Why

`doc/developer/unpaywall_pdf_survey/` measured 400 PDFs Unpaywall names: our
clients obtain 28%, and 81% of the failures are bot walls. A failure whose
answer was a challenge page (`probe_markers`: a known challenge in the page, or
a 403/503 served by Cloudflare itself) was a wall 89% of the time; about 6%
had no free PDF behind them.

The spikes (`spikes/README.md`) settled which client passes those walls:

| Client | PMC | ScienceDirect | Other walls |
|---|---|---|---|
| Playwright Chromium, headless or headed | 0 | 0 | some |
| obscura (stealth headless) | 0/12 | 0/10 | 0/14 |
| QtWebEngine, hidden (no window, 25 s) | **12/12** | 0/10 | 5/14 |
| QtWebEngine, window shown (60 s, nobody ticking) | | 0/10 | **9/9** |

ScienceDirect needs one tick from a person, and the tick covers later
ScienceDirect PDFs. The machine channels recovered 47 of the 149 failures that
matter; this recovers most of the rest.

## Decisions (maintainer, 2026-10-12)

1. **Auto, then queue.** A run never waits for a browser or a person. A walled
   PDF is queued; a hidden embedded browser works the queue unattended; what
   it cannot pass waits for the reader in a review queue window.
2. **A rescue re-analyses.** Saving a rescued PDF re-runs that document's
   transparency analysis, so its badge loses "Limited certainty…". Scores from
   abstracts are left alone.
3. **The kind on all three platforms, the browser on the desktop.** The apps
   detect `challenged` and say it; WKWebView and Android WebView get their own
   spec later.
4. **One recovery path (approach A).** The browser lives on the GUI thread and
   is driven only by the queue; no worker thread blocks on it. Rejected: an
   inline hidden attempt through a cross-thread broker (every worker can stall
   ~25 s per wall, a GUI-thread caller deadlocks, and the queue still needs a
   second path), and a helper process with its own QtWebEngine (start-up cost,
   an IPC protocol, a profile lock shared between processes).
5. **No impersonation** (machine-channels decision 6 still binds).
   QtWebEngine's own User-Agent, no automation protocol, no stealth.

## 1. The `challenged` kind (all three platforms)

**What it is.** A new `RequestFailureKind.CHALLENGED`. It keeps the HTTP
status the challenge came with (403, 503, or 200 for a challenge served as a
page) in `status_code`. It is **not an answer** about the article
(`is_answer` is false), so it reads "could not be asked, so a freely available
copy may exist", and never settles an absence.

**Detection** is one pure function, `challenge_markers(status, headers,
body_prefix) -> tuple[str, ...]`, in a new `bot_walls.py`. It ports the
survey's `WALL_MARKERS` (Cloudflare, Akamai, Imperva, DataDome, PerimeterX,
AWS WAF, Anubis, captcha, proof-of-work, robot text: matched on the lowercased
body prefix and on header names and values) and its Cloudflare rule (a 403 or
503 whose `server` header is `cloudflare`, whatever the page says). A
non-empty result means challenged.

**Where it applies.** Only to a non-PDF answer from an open-access PDF
address:

- `SERVICE_UNPAYWALL_PDF` (`unpaywall_pdf`),
- `SERVICE_OPENALEX_PDF` (`openalex_pdf`).

The DOI download (`PDFSourceType.DOI_DIRECT`) records no unobtained copy and
no address today, so it keeps its own reporting (and the OpenAthens offer).

A challenge replaces the kind those answers get today (`http_status` for a
401/403/429/503, `malformed_response` for a 200 that is not `%PDF`). **A 404
or 410 stays gone**, whatever its page says. Europe PMC's render tier (#453)
is out of scope.

**Reading enough of the page.** Today a non-PDF answer is read for 4 bytes
(`PDF_MAGIC_BYTES`). A non-PDF answer is now read up to
`CHALLENGE_BODY_PREFIX_BYTES` (64 KiB), the survey's bound. This is a bounded
read of a lookup page, not research content, so golden rule 13's truncation
rule does not apply.

**What the reader sees.** One new reason phrase in `RequestFailure.describe()`
and the apps' ports:

> The open-access copy's PDF (the host asked for a robot check) could not be
> asked, so a freely available copy may exist. Whether this document is open
> access was not established.

In `tried_sources_statement` the entry reads like any other unasked one.

**The contract.**

- `doc/cross_platform/fulltext_parity/challenge_detection.json` (new): rows of
  `status`, `headers`, `body` → `challenged`, `markers`. Cases come from the
  survey rows (one per marker family, ScienceDirect's Cloudflare 403 whose
  markup lies beyond the prefix, PMC's "Preparing to download" served as a
  200), plus controls: an ordinary 403 with no marker, a 404 carrying
  challenge markup, a `%PDF` body, an HTML abstract page.
- `open_access_unsettled_notice.json`: `challenged` rows for each of the three
  services, and the persisted forms.
- `doc/cross_platform/fulltext_retrieval.md`: a section "A challenged PDF
  (#480)", and the sentence in "Unpaywall PDF refusal" that called the
  decision open is replaced by it.
- Swift (BioMedLit) and Kotlin: detect, store in `OpenAccessShortfall`
  (schema 2 `entries`, kind `challenged`), and say the sentence. An older
  build reading the unknown kind falls under the existing unreadable-value
  rule.

## 2. The queue (desktop)

**Table.** `browser_rescue_queue` in `LiteStorage`, created with `CREATE
TABLE IF NOT EXISTS`, unique on `(document_id, address)`:

| Column | |
|---|---|
| `document_id`, `doi`, `pmid`, `title` | the document, as the run knew it |
| `address`, `host`, `service` | the PDF address and who named it |
| `state` | below |
| `attempts` | browser visits made |
| `verdict` | the last browser verdict, typed, stored as JSON |
| `priority` | 0, or 1 for an entry a reader is waiting on |
| `created_at`, `updated_at` | |

**States.**

```
pending ──hidden attempt──▶ rescued | needs_person | no_pdf | gone
needs_person ──visible attempt──▶ rescued | no_pdf | needs_person
any open state ──reader──▶ dismissed
a sibling rescued ──▶ superseded
```

- `needs_person`: still a challenge after the attempt.
- `no_pdf`: the page loaded with no challenge and no PDF (an abstract page).
- `gone`: the browser saw 404 or 410.
- A transport failure or renderer crash leaves the entry `pending` with
  `attempts + 1`; at `MAX_RESCUE_ATTEMPTS` it becomes `needs_person`, so no
  entry is retried for ever.
- Once one address of a document is rescued, its other open entries become
  `superseded`.

The transitions are a pure function in `browser_rescue.py`; storage only
persists them.

**Who adds entries.** Discovery stays free of storage. A pure function,
`rescue_requests(document, record) -> list[RescueRequest]`, picks the
`challenged` failures with an address out of a `LookupRecord`. Every place
that holds a document and its record calls `storage.enqueue_rescue(...)`:
`TransparencyManager` on completion, the interrogation tab's workers, and the
MCP server. Enqueueing is idempotent: an existing entry, open or settled, is
left as it is, except that a higher priority raises an open entry's (the
interrogation tab waiting on an entry a run queued).

**Accepted limit.** A `no_pdf` or `gone` verdict does not rewrite the
document's stored sentence: it keeps the conservative "challenged … could not
be asked", since only a rescue re-analyses. The queue window shows the
verdict. Lodged as an issue, not a new reader-facing kind.

## 3. The browser and the hidden pass (desktop)

**Modules.** Pure logic in `browser_rescue.py` (transitions, outcome →
verdict, bounds). The engine in `gui/browser_rescue_service.py`
(`BrowserRescueService`, a `QObject` on the GUI thread). QtWebEngine ships in
the PySide6 wheel already declared (6.10.1 imports `QtWebEngineCore` and
`QtWebEngineWidgets`); no new dependency.

**The engine, configured as the spike passed.**

- One persistent `QWebEngineProfile` under
  `~/.bmlibrarian_lite/browser_profile/`, cookies persisted, so a clearance
  or a ScienceDirect tick carries over between pages and sessions.
- QtWebEngine's own User-Agent; JavaScript on; `PdfViewerEnabled` off, so a
  PDF arrives through `downloadRequested`.

**The hidden pass.** Driven only by signals and `QTimer`s; nothing blocks.

1. Take the next `pending` entry, priority first, then oldest.
2. Ask the host's limiter for its next slot without sleeping (a public form
   of `RateLimiter._claim_slot`) and schedule the navigation for then.
3. Load the address in a `QWebEnginePage` with no view. Wait up to
   `RESCUE_HIDDEN_TIMEOUT_MS` (25 s) for a download.
4. A download is accepted into a `.part` file beside its cache path
   (`pdf_download.partial_download_path` of `generate_pdf_path(...)`). It is
   cancelled past `MAX_PDF_SIZE` and after `RESCUE_DOWNLOAD_STALL_MS` with no
   progress (the survey's IEEE stall hung its sync API). Finished, it must
   begin with `%PDF`; then it is renamed into place, where the cached-PDF
   tier finds it. Otherwise it is deleted and the verdict is `no_pdf`.
5. Without a download by the timeout, the page's title and text go through
   `challenge_markers`: a challenge is `needs_person`, none is `no_pdf`. An
   HTTP 404/410 seen through `loadingChanged` (`ErrorDomain.HttpStatusCodeDomain`)
   is `gone`. A network error or `renderProcessTerminated` leaves the entry
   `pending`.

**When it runs.** It starts once the main window is shown. It wakes when an
entry is added: in-process enqueue sites emit a thread-safe signal, and a slow
`RESCUE_POLL_INTERVAL_MS` poll catches entries written by MCP or the CLI. At
quit it aborts the page, leaves the entry `pending`, and deletes the page
before the profile, as QtWebEngine requires.

**Setting.** "Try bot-walled PDFs in a hidden browser", on by default, in the
settings dialog. Off: entries still collect for the window.

**Start-up.** `run_lite_app` sets `AA_ShareOpenGLContexts` and imports
`QtWebEngineCore` before the `QApplication` exists. The test suite's Qt
preflight does the same.

## 4. The review queue window, and what a rescue sets off (desktop)

**The window.** A non-modal `gui/review_queue_dialog.py`, opened from a
status-bar indicator ("Walled PDFs: 3", hidden at 0) and from the menu.

- Left: the entries (title, host, who named the address, state, last
  verdict); `needs_person` by default, a filter for the rest.
- Right: a `QWebEngineView` on the same profile, loading the selected address.
  When the reader ticks and the PDF arrives, it takes the hidden pass's
  acceptance path (step 4), the entry becomes `rescued`, and the next opens.
- *Dismiss*; *Open in system browser* (a fallback; nothing is saved from it);
  *Walk the queue*: visit each `needs_person` entry in the shown view
  unattended, up to `RESCUE_VISIBLE_TIMEOUT_MS` (60 s) each. This is the mode
  that cleared the Cloudflare publishers 9/9, so only ScienceDirect-like walls
  are left to tick.
- While the window drives its view, the hidden pass pauses. Both use the same
  per-host limiters.

**A rescue re-analyses.** The service emits `rescued(document_id)`.
`TransparencyManager.reanalyse(document_id)` ignores this build's cached row,
still refuses to overwrite a newer build's (`may_replace_stored`), and queues
the analysis as usual; discovery finds the PDF at the cached-PDF tier, and
`analysis_complete` updates the badge as today.

**The interrogation tab.** When its fetch ends with no full text and a
`challenged` failure, it enqueues the document at priority 1 and shows
"Trying the publisher's page in a browser…" with *Cancel*. A rescue loads the
PDF into the session; `needs_person` offers "Open in the review queue",
focused on that entry; anything else keeps today's abstract fallback. The
OpenAthens flow is untouched.

## 5. Retired: the Playwright fallback (#483)

Removed: `BrowserSession`, `get_browser_session`, `close_browser_session`,
`_try_browser_download`, `_try_direct_content`, the `blocked_oa_sources`
routing, the `use_browser_fallback` and `browser_headless` parameters
everywhere (`PDFDiscoverer`, `FulltextDiscoverer`,
`StudyTransparencyAnalyzer`, `transparency/assessment.py`, `mcp_server.py`),
and the transparency CLI's `--no-browser` flag. Docstrings and
`doc/developer/guide.md` that mention Playwright follow.

## Testing

- **Pure:** `challenge_markers` against `challenge_detection.json` on all
  three platforms; the state machine; `rescue_requests`; the new sentences
  through `open_access_unsettled_notice.json`.
- **Storage:** idempotent enqueue; every transition; `superseded`; the attempt
  cap; an entry from another process is picked up by the poll.
- **Engine, against a loopback HTTP server:** a direct PDF; a JavaScript
  challenge that sets a cookie and reloads to the PDF; a 404; an HTML abstract
  page; a stalling download; an oversize download; a non-`%PDF` "PDF". The
  engine tests run **in their own subprocess**, not in the suite's shared
  `QApplication` (pumping it inherits earlier tests' backlog, #516/#517), and
  **fail rather than skip** when QtWebEngine cannot start on CI.
- **Controls:** a not-challenged 403 is still refused and never queued; a 404
  with challenge markup stays gone; a rescue of a document whose stored row a
  newer build wrote does not overwrite it.
- **Live acceptance**, before the browser PR is merged: the spike's 41
  addresses (`spikes/2026-10-04-qtwebengine-hidden.jsonl`) through the real
  service's hidden pass. Expected: PMC near 12/12, ScienceDirect
  `needs_person`, controls `no_pdf`. The rows go beside the spike's.

## Stages

Each its own PR, Python first as the reference.

| Stage | Adds | Platforms |
|---|---|---|
| **1** | `challenged`: `bot_walls.py`, the 64 KiB read, the kind and sentence, the contract and fixtures, and the ports | Python, Swift, Kotlin |
| **2** | The queue table, the hidden service, rescue → re-analysis, the #483 removal, live acceptance | Python |
| **3** | The review queue window and the interrogation tab's priority path | Python |

The kind and its ports are one PR (maintainer, 2026-10-12): the apps'
contract tests decode every row of `open_access_unsettled_notice.json` and
fail on an unknown kind, and CI runs all three platforms on every PR. The
machine channels' stages landed the same way.

## Out of scope

- The apps' embedded browsers (WKWebView, Android WebView): their own spec,
  after stage 4, with their walls measured on those engines.
- A reader-facing kind for the browser's `no_pdf` / `gone` verdicts (lodged).
- Europe PMC's render tier (#453).
- #481 (iOS keeps an earlier build's refused Unpaywall link) follows this
  work, as before.
