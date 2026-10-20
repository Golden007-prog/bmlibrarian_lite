# Retraction Checking

Developer notes for the desktop's retraction checking. Design and decisions:
`docs/superpowers/specs/2026-10-18-retraction-checking-design.md`. The rule
that turns evidence into a verdict is bmlib's (`bmlib.publications.retraction_status`,
bmlib#504); lite's copy of its contract fixture is
`doc/cross_platform/retraction_parity/` (run by `tests/test_retraction_contract.py`).

## What It Does

Every paper a systematic review, an MCP tool or the interrogation tab holds
is checked against three sources:

- **Retraction Watch's export**, a local SQLite copy in the data directory
  (`retraction_watch.db`), rebuilt whole on every refresh.
- **Crossref**, asked in batches of up to 50 DOIs over the polite session.
- **The record's PubMed label** ("Retracted Publication"), which needs
  neither network nor database.

bmlib decides a verdict: **retracted**, **concern** (an expression of
concern or a partial retraction), **clear**, or **unchecked**. Clear needs
the export consulted *and* Crossref to have served the DOI; anything less is
unchecked, never clear.

The review leaves retracted papers out before anything is filtered or scored
(the one choke point in `WorkflowWorker.run`); the report names them in a
"Left out as retracted" section and puts a caveat under a cited concern
paper's reference. The MCP tools leave them out too and mark every other
paper with its `retraction_status`.

## Modules

- `retraction_export.py`: the local copy. `export_copy` reports its state
  (`ExportState`: current, stale, missing, unreadable); `export_evidence`
  looks one paper up (`None` when not consulted); `refresh_export` downloads
  and swaps in a new copy, keeping the old one on any failure or cancel.
- `retraction_crossref.py`: `crossref_session` and `lookup_crossref`. A DOI
  that was not asked has no entry; one whose batch failed has a
  `RequestFailure`. Not asked is not unreachable.
- `retraction_store.py`: the `retraction_checks` table (one row per paper,
  keyed `doi:` + Crossref key, else `pmid:` + PMID) and `PaperCheck`.
- `retraction_checks.py`: `RetractionChecker.check`, the rules for reusing
  and replacing stored verdicts; `build_retraction_checker`.
- `retraction_screen.py`: pure. `RetractionScreen` splits documents into kept
  and excluded; `screen_with_fallback` never lets a failed check stop the
  caller; `RetractionSnapshot` rebuilds what a stored report needs.
- `retraction_text.py`: the shared reader-facing sentences.
- `gui/retraction_badge.py`: the RETRACTED / CONCERN / NOT CHECKED badge.

## Rules

- **An outage never erases a verdict.** A fresh verdict that leaves
  unanswered a lookup the stored one had answered never replaces it; a
  stored retraction or concern is still returned. A verdict resting on a
  failure only fills a missing row, so it cannot overwrite a row this build
  could not read.
- **A failure is not knowledge.** A stored verdict with a lookup unanswered
  is asked again on every check; a decided one is reused for
  `RETRACTION_CHECK_MAX_AGE_DAYS` (7), unless the record's label now says
  retracted.
- **The label survives everything.** A failed check (the checker could not be
  built, the database raised) still leaves out a paper its label retracts.
- **A failed store is not a failed check.** The verdicts are returned even
  when they could not be stored.
- **Only the review-like workflows download the export.** A copy older than
  `RETRACTION_WATCH_MAX_AGE_DAYS` (35) is not consulted; the review's checker
  and MCP's `fact_check_claim` download a new one (about 68 MB) once per run,
  and Cancel or quit stops it. MCP's single-paper tools and the
  interrogation tab use the copy there is.
  `bmll retractions-update` refreshes it by hand.
- **A shrunken export is refused.** A new copy holding fewer than
  `RETRACTION_WATCH_MIN_KEEP_RATIO` (95%) of the old copy's notices is a
  partial download; the old copy is kept.
- **Every unchecked paper has a named cause** in the run's summary
  (`_unchecked_causes`): the copy, an unreadable notice, Crossref's failure,
  no DOI, no usable Crossref answer, no identifiers, or a cancel. A restored
  report gives the count only, as its snapshot does not record why.
- **`excluded` is a fact of the run.** Snapshot rows record whether the run
  left a paper out; a later re-check (*Check again*) may change `status`,
  never that. It keeps the run's own status and notice as `run_status` and
  `run_deciding`, which the audit counts and the "left out" lines read.

## Tests

`tests/test_retraction_*.py`, `tests/test_mcp_retraction.py`,
`tests/test_cli_retractions.py`. They use stub sessions, never the network.
