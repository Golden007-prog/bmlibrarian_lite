# Retraction Checking (Desktop) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every document a desktop workflow takes in gets one of four retraction statuses (retracted, concern, clear, unchecked). Unattended work leaves retracted papers out with a visible trace. A person looking at a paper is told its status, and saved work can be checked again.

**Architecture:** bmlib owns the rule and the Crossref request/parse (`bmlib.publications.retraction_status`), and lite imports it. Lite owns the transport (polite `requests` session), the local Retraction Watch copy (a SQLite file built with bmlib's parser), a per-paper `retraction_checks` table in `metadata.db`, and one screening function. Each workflow calls that function exactly once. Reader-facing text lives in one module, as `search_failures.py` / `analysis_failures.py` do for shortfalls.

**Tech Stack:** Python 3.11+, bmlib (git pin), requests + `mount_politely`, SQLite, PySide6, argparse.

**Spec:** `docs/superpowers/specs/2026-10-18-retraction-checking-design.md`. This plan covers the spec's PRs 1 (desktop half), 2 and 3. The iOS and Android plans follow and use the same fixture.

## Global Constraints

- Statuses: `retracted`, `concern`, `clear`, `unchecked` (bmlib `RetractionStatus`). **Never treat unchecked as clear.**
- `RETRACTION_WATCH_MAX_AGE_DAYS = 35`, `RETRACTION_CHECK_MAX_AGE_DAYS = 7` (spec).
- Crossref is paced by `mount_politely` and `POLITE_RATE_CEILINGS["api.crossref.org"]` (already 5.0/s). Requests carry the mailto User-Agent: `contact_email(config)` from `transparency/assessment.py`.
- A failed batch leaves every DOI in it unanswered, typed by `RequestFailure` (via `request_failure_from_exception`). No status is ever inferred from a failure.
- A missing, stale or unreadable export copy means "export not consulted" (`export=None` to bmlib).
- Reader-facing text carries no provider text (golden rule 13, `RequestFailure.describe()` only). No inline stylesheets; use `scaled()` and the stylesheet generator.
- No magic numbers: constants go in `constants.py` with a unit suffix and a why-comment.
- No test talks to live Crossref or downloads the export. Use a stubbed session; check with the dead-proxy memory's `HTTPS_PROXY` trick before the PR.
- Each workflow excludes exactly once, and the audit trail sees the exclusion. A check failure never stops a run.
- **Everything learned about a document is stored**, never kept only in memory (the maintainer, 2026-10-18: an offline knowledge base). This covers documents, publication types, verdicts and snapshots. NULL means not recorded, distinct from recorded-empty. An upsert never erases a stored fact. The other gaps already found are #590.
- One branch per PR, never stacked. PR 1 = Tasks 1–3, PR 2 = Tasks 4–12, PR 3 = Tasks 13–14.

## Review Focus

1. **A document with neither DOI nor PMID** (a preprint record, a restored row): it must come out `unchecked`, counted in the summary, never `clear`. Owner: Task 7, `test_no_identifier_is_unchecked_and_counted`.
2. **The export download is an HTML error page or truncated**: the previous copy must survive, and if that copy is stale the export reads as not consulted. Owner: Task 4, `test_a_refused_download_keeps_the_previous_copy`.
3. **Two documents in one run share a DOI** (the merge kept both; or the DOI is spelled differently): one Crossref request, and both documents get the verdict. Owner: Task 6, `test_documents_sharing_a_doi_are_asked_once`.
4. **Crossref answers 429 for one batch of a 120-document run**: only that batch's 50 DOIs are unchecked. The others are decided, and the summary counts exactly 50. Owner: Task 5, `test_one_failed_batch_leaves_only_its_dois_unanswered`.
5. **The run is cancelled during the check**: the worker ends cancelled (no report, no "checked" snapshot written). Owner: Task 8, `test_cancel_during_the_check_ends_the_run`.

---

## PR 1: the contract and publication types

### Task 1: Depend on bmlib, copy the contract, sync the ledger

**Files:**
- Modify: `pyproject.toml` (dependency + `[tool.hatch.metadata] allow-direct-references = true`), `uv.lock`
- Create: `doc/cross_platform/retraction_parity/retraction_status.json` (copied verbatim from bmlib `tests/fixtures/retraction_status.json` at `f76721545a76eceb683d9c460fb1f47e5c925cdb`), `doc/cross_platform/retraction_parity/README.md`
- Modify: `doc/cross_platform/bmlib_sync/ledger.json` (bump `bmlib_commit`, triage)
- Test: `tests/test_retraction_contract.py`

**Interfaces:**
- Produces: the import `from bmlib.publications.retraction_status import ...`, available to every later task.

- [ ] **Step 1:** Add `"bmlib @ git+https://github.com/hherb/bmlib@f76721545a76eceb683d9c460fb1f47e5c925cdb"` to `dependencies`. If the maintainer has cut bmlib 0.12.0 by then, use `"bmlib>=0.12.0"` instead and skip `allow-direct-references`. Run `uv lock`, then `uv pip install -e ".[dev]"`.
- [ ] **Step 2:** Copy the fixture. Write the README after `request_failure_parity/`'s: what the file is, its source commit, "copied verbatim, never edited here; a bmlib change arrives by a ledger sync", and the five readers (bmlib Python, bmlib Rust, lite desktop, Swift, Kotlin).
- [ ] **Step 3: Write the contract test.** It mirrors bmlib's `tests/test_retraction_status.py` runners (`test_rule_case`, `test_parse_case`, `test_request_case`) over lite's copy, including `crossref.get("for", case["doi"])`. Add `test_the_copy_is_the_pinned_bmlib_s`: assert the `_readme` mentions `crossref.for`. That fails loudly if someone copies an older fixture.
- [ ] **Step 4:** Run `pytest tests/test_retraction_contract.py -v`. Expected: 47 rule + 24 parse + 5 request cases pass.
- [ ] **Step 5: Ledger sync.** Set `bmlib_commit` to `f76721545a76eceb683d9c460fb1f47e5c925cdb`, then run `python scripts/check_bmlib_ledger.py --bmlib ~/src/bmlib`. Triage each id it lists (bmlib#489, #490, #494, #497, #504 at the time of writing) by reading the ports, as `doc/cross_platform/bmlib_sync/README.md` says:
  - `bmlib#504`: `python: ported` (by import), `swift`/`android: owed`. Note: rule via bmlib; the apps are the next plans.
  - `bmlib#497`: lite #587.
  - `bmlib#490`: `n/a` (FFI build; lite #583 consumed it).
  - `#489`, `#494`: read `quality/` and `fulltext_discovery.py`'s Europe PMC PDF tier and set the status by what the port does. Lodge an issue for a port still owed.
  - Update the `bmlib:0.7.0-retraction-watch` row's note. Expected: the check exits 0.
- [ ] **Step 6: Commit.** `feat(retraction): depend on bmlib, copy the retraction contract, sync the ledger`

### Task 2: Keep publication types, through storage

**Files:**
- Modify: `src/bmlibrarian_lite/data_models.py` (`LiteDocument`), `src/bmlibrarian_lite/pubmed/data_types.py` (`ArticleMetadata`), `src/bmlibrarian_lite/pubmed/search_client.py` (`_parse_single_article`, ~840), `src/bmlibrarian_lite/europepmc.py` (`ArticleInfo`, `_article_info_from_result`, ~648), `src/bmlibrarian_lite/search_service.py` (`_pubmed_to_merged` 611, `_europepmc_to_merged` 636), `src/bmlibrarian_lite/search_merger.py` (`MergedArticle`, `to_lite_document` 118), `src/bmlibrarian_lite/agents/search_agent.py` (`_articles_to_documents` 390), `src/bmlibrarian_lite/gui/workers.py` (~1012)
- Modify: `src/bmlibrarian_lite/storage.py`:
  - the schema (~840);
  - a column migration in the `PRAGMA table_info` pattern (~626);
  - `add_document` (1128), `add_documents` (1234), `get_document` (1327), `get_documents` (1386);
  - the rebuilds at 1368, 1429 and 1522.
- Test: `tests/test_publication_types.py`

**Interfaces:**
- Produces:
  - `LiteDocument.publication_types: list[str] | None` (default `None`). `None` means not recorded (a row from before the column, or a source that gave no type list). `[]` means the record had none. It is in `to_dict`/`from_dict`.
  - **Stored** as `documents.publication_types TEXT` (a JSON list or `NULL`). A restored document keeps the PubMed label, the one free source that settles a retraction alone and the only one for a paper without a DOI. *Check again* and a reopened run (Tasks 13–14) work only on restored documents.
  - **Not** in `metadata`: `MetadataFilter._get_publication_types` reads `metadata["publication_types"]`, and filling that would switch on Tier 1 quality classification for every search, a separate decision. Lodge that as an issue ("Tier 1 never sees publication types").

- [ ] **Step 1: Write failing tests.**
  - `test_pubmed_record_keeps_publication_types`: a PubMed XML with `<PublicationTypeList><PublicationType UI="D016441">Retracted Publication</PublicationType><PublicationType UI="D016428">Journal Article</PublicationType></PublicationTypeList>` gives `["Retracted Publication", "Journal Article"]`.
  - `test_europepmc_result_keeps_publication_types`: `{"pubTypeList": {"pubType": ["retracted publication", "research-article"]}}` gives both, verbatim. A bare string `pubType` gives a one-item list, and a missing list gives `None`.
  - `test_merged_article_unions_both_sources_types`, keeping first-seen order without duplicates (`None` from both sources stays `None`).
  - `test_lite_document_round_trips_publication_types`.
  - `test_metadata_filter_still_sees_no_publication_types`: a merged search document's `metadata` has no `publication_types` key.
  - `test_storage_round_trips_publication_types`: `add_documents` then `get_document` keeps the list. `[]` stays `[]`, and `None` stays `None`.
  - `test_a_pre_column_database_migrates_and_reads_none`: a `metadata.db` created with the old `documents` schema opens and gains the column, its rows read `None`, and a second open is a no-op.
  - `test_a_later_search_fills_types_but_never_erases_them`: re-adding with a list fills a `NULL` row, and re-adding with `None` keeps a stored list.
- [ ] **Step 2:** Run them. Expected: FAIL (no field).
- [ ] **Step 3:** Implement. PubMed: `[e.text.strip() for e in article.findall(".//PublicationTypeList/PublicationType") if e.text and e.text.strip()]`. Europe PMC: `result.get("pubTypeList", {}).get("pubType")`, accepting str or list of str. Thread the list through `MergedArticle` (field `publication_types: list[str] | None`) to `LiteDocument`. Storage: add the column to `_get_sqlite_schema` and a migration (`ALTER TABLE documents ADD COLUMN publication_types TEXT` when `PRAGMA table_info` lacks it; raise `SQLiteError` on failure, as the newer migrations do). Upsert with `COALESCE(excluded.publication_types, documents.publication_types)`.
- [ ] **Step 4:** Run `pytest tests/test_publication_types.py tests/ -q`. Expected: PASS, with no other test changed.
- [ ] **Step 5: Commit.** `feat(search): keep publication types on search documents`

### Task 3: Store every document the incremental search finds

The maintainer's rule (2026-10-18): every document, and every fact about one, found by any route is stored, never kept only in memory. The goal is an offline knowledge base. Today `IncrementalSearchWorker` (`gui/workers.py:801`) hands its documents to the review tab in memory only. A finished review then links and scores them by id (`add_question_documents`, `save_scored_document`), but never stores the documents. Measured 2026-10-18 on a scratch database: such a document is linked and scored, `get_document` returns `None`, and reopening the question drops it.

**Files:**
- Modify: `src/bmlibrarian_lite/gui/workers.py` (`IncrementalSearchWorker.run` 886; it already holds `self.storage`), `src/bmlibrarian_lite/gui/systematic_review_tab.py` (the preloaded path ~314)
- Test: `tests/test_incremental_search_storage.py`

**Interfaces:**
- Consumes: `LiteStorage.add_documents(documents, embedding_function=...)`, as `LiteSearchAgent.search` calls it (`agents/search_agent.py:233`), and `add_question_documents(question, ids, search_session_id=None)`.
- Produces: every `LiteDocument` emitted by `batch_complete`/`finished` is already stored, `publication_types` included (Task 2), and linked to its question.

- [ ] **Step 1: Write failing tests:**
  - `test_documents_found_incrementally_are_stored_before_they_are_emitted`: a stubbed PubMed client returns two records. After `run()`, `get_document(id)` returns each with its title, identifiers and publication types, and `get_document_ids_for_question(q)` holds both.
  - `test_a_scored_preloaded_document_survives_a_reopen`: the full preloaded path through `WorkflowWorker` with a fake scorer, then `get_documents(get_document_ids_for_question(q))` returns it.
  - `test_a_storage_failure_is_reported_not_swallowed`: `add_documents` raises `sqlite3.Error`, and the worker's `error` names it. The documents are still not presented as stored.
- [ ] **Step 2:** Run them. Expected: FAIL (`get_document` is `None`).
- [ ] **Step 3: Implement.**
  - In `IncrementalSearchWorker.run`, call `add_documents` then `add_question_documents` on each batch before emitting it. Pass the embedder the search agent passes, or none if the worker has none: check how `add_documents` treats a missing `embedding_function` and keep embeddings consistent with the search agent's path.
  - In the review worker's preloaded path, call `add_documents` on preloaded documents not yet stored, as a backstop for any other route that preloads.
- [ ] **Step 4:** Run them, then `pytest tests/ -q`. Expected: PASS.
- [ ] **Step 5: Commit.** `fix(search): documents found by the incremental search are stored`

---

## PR 2: lookups, storage, the choke point, audit, report and UI

### Task 4: The local Retraction Watch copy

**Files:**
- Create: `src/bmlibrarian_lite/retraction_export.py`
- Modify: `src/bmlibrarian_lite/constants.py`
- Test: `tests/test_retraction_export.py`

**Interfaces:**
- Produces:
  - `RETRACTION_WATCH_EXPORT_URL = "https://api.labs.crossref.org/data/retractionwatch"` (query `mailto=`), `RETRACTION_WATCH_DB_NAME = "retraction_watch.db"`, `RETRACTION_WATCH_MAX_AGE_DAYS = 35`, `RETRACTION_WATCH_DOWNLOAD_TIMEOUT_SECONDS`.
  - `class ExportState(StrEnum): CURRENT, STALE, MISSING, UNREADABLE`.
  - `@dataclass(frozen=True) class ExportCopy: state: ExportState; data_as_of: date | None`. Property `consulted -> bool` is true only for `CURRENT`.
  - `export_copy(data_dir: Path, *, today: date) -> ExportCopy`.
  - `export_evidence(data_dir: Path, *, doi: str | None, pmid: str | None) -> list[RetractionEvidence] | None`. It returns `None` when not consulted (copy not current, no usable identifier, or bmlib's `lookup_retractions` raises `ValueError`), else `evidence_from_notices(lookup_retractions(conn, doi=, pmid=))`.
  - `refresh_export(data_dir: Path, session: requests.Session, *, mailto: str, today: date) -> ExportCopy`.

- [ ] **Step 1: Write failing tests** (stub session serving canned bytes; small CSV in the export's header shape, as in bmlib's tests):
  - `test_refresh_builds_a_current_copy`: state `CURRENT`, `data_as_of == today`, and a looked-up DOI returns its evidence.
  - `test_a_refused_download_keeps_the_previous_copy`: an HTML page, a truncated CSV and a 503 each leave the earlier file byte-identical, and the state follows its age.
  - `test_a_copy_older_than_35_days_is_stale_and_not_consulted`: day 35 is `CURRENT`, day 36 is `STALE`, and `export_evidence(...)` is `None`.
  - `test_missing_and_unreadable_copies_are_not_consulted`: a garbage file gives `UNREADABLE`.
  - `test_no_identifier_is_not_consulted` gives `None`. `test_a_found_nothing_lookup_is_consulted` gives `[]`.
- [ ] **Step 2:** Run them. Expected: FAIL (module missing).
- [ ] **Step 3: Implement.**
  - Download to `<db>.part` and parse with `parse_retraction_watch_csv(path)`. Build a new SQLite file with `connect_sqlite` + `ensure_schema` + `store_retraction_notices`, plus a lite table `export_meta(key TEXT PRIMARY KEY, value TEXT)` holding `data_as_of`.
  - Then `os.replace` it over the old file. A fresh file per refresh means records Retraction Watch dropped disappear (bmlib#510's divergence avoided).
  - Any parse/store error, non-200 or empty parse deletes the temporary file and returns the old copy's state.
- [ ] **Step 4:** Run `pytest tests/test_retraction_export.py -v`. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(retraction): the local Retraction Watch copy`

### Task 5: Crossref lookups over lite's transport

**Files:**
- Create: `src/bmlibrarian_lite/retraction_crossref.py`
- Test: `tests/test_retraction_crossref.py`

**Interfaces:**
- Consumes: bmlib `crossref_retraction_requests`, `parse_crossref_retraction_response`, `crossref_key`, `CrossrefResult`, `CrossrefOutcome`.
- Produces:
  - `@dataclass(frozen=True) class CrossrefLookups: results: dict[str, CrossrefResult]; failures: dict[str, RequestFailure]`, keyed by `crossref_key(doi)`. `failures` holds the DOIs whose batch failed on the wire or with a non-200.
  - `crossref_session(mailto: str) -> requests.Session`, built like `CrossRefClient`: `mount_politely(requests.Session(), retry=Retry(total=0))` (the adapter owns throttling), with `User-Agent: f"bmlibrarian-lite/{__version__} (mailto:{mailto})"`.
  - `CROSSREF_REQUEST_TIMEOUT_SECONDS` in `constants.py` (match `CrossRefClient`'s timeout).
  - `lookup_crossref(dois: Iterable[str], session: requests.Session, *, mailto: str, should_cancel: Callable[[], bool] = lambda: False) -> CrossrefLookups`.
  - An unsendable DOI gets no entry, so the rule reads it as not asked.

- [ ] **Step 1: Write failing tests** (stub session mapping URL → (status, body) or an exception):
  - `test_served_batch_gives_served_results_keyed_by_crossref_key`.
  - `test_one_failed_batch_leaves_only_its_dois_unanswered`: 120 DOIs make 3 batches. The second answers 429 → its 50 DOIs are `UNREACHABLE` with `failures[doi] == RequestFailure(HTTP_STATUS, 429)`, and the other 70 are `SERVED`.
  - `test_connection_error_and_malformed_json_are_unreachable`: kinds `CONNECTION` and `MALFORMED_RESPONSE`. The control is a served batch.
  - `test_a_comma_doi_is_never_sent`: no request carries it, and it has no result.
  - `test_cancel_between_batches_stops_sending`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - For each `CrossrefBatch`: `session.get(batch.url, timeout=CROSSREF_REQUEST_TIMEOUT_SECONDS)`, then `parse_crossref_retraction_response(r.status_code, r.content, batch.dois)`.
  - On a non-200, record `RequestFailure(HTTP_STATUS, status)` for each DOI.
  - On `requests.RequestException`, use `request_failure_from_exception(exc)` and `CrossrefResult(CrossrefOutcome.UNREACHABLE, reason=failure.kind.value)` per DOI.
  - A body bmlib calls unreadable is `MALFORMED_RESPONSE`.
- [ ] **Step 4:** Run `pytest tests/test_retraction_crossref.py -v`. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(retraction): Crossref lookups over the polite session`

### Task 6: Stored verdicts and the checker

**Files:**
- Create: `src/bmlibrarian_lite/retraction_store.py` (its own `SCHEMA`, run from `LiteStorage._init_sqlite` after `rescue_queue_store.SCHEMA`), `src/bmlibrarian_lite/retraction_checks.py`
- Modify: `src/bmlibrarian_lite/storage.py` (run the schema; thin methods), `src/bmlibrarian_lite/constants.py` (`RETRACTION_CHECK_MAX_AGE_DAYS = 7`)
- Test: `tests/test_retraction_checks.py`

**Interfaces:**
- Consumes: Tasks 3 and 4.
- Produces:
  - Table `retraction_checks`: `paper_key TEXT PRIMARY KEY` (`"doi:" + crossref_key(doi)`, else `"pmid:" + canonical pmid`), `status`, `deciding_json` (`RetractionEvidence.to_dict()` or NULL), `unanswered_json` (list of `RetractionLookup` values), `crossref_failure_json` (`RequestFailure.to_dict()` or NULL), `export_data_as_of`, `checked_at` (ISO UTC).
  - `@dataclass(frozen=True) class PaperCheck: verdict: RetractionVerdict; checked_at: datetime; export_data_as_of: date | None; crossref_failure: RequestFailure | None`.
  - `paper_key(doc: LiteDocument) -> str | None`.
  - `class RetractionChecker.__init__(self, storage: LiteStorage, data_dir: Path, session: requests.Session, *, mailto: str, now: Callable[[], datetime] = lambda: datetime.now(UTC))`.
  - `RetractionChecker.check(self, documents: Sequence[LiteDocument], *, refresh: bool = False, should_cancel: Callable[[], bool] = lambda: False) -> dict[str, PaperCheck]`, keyed by `LiteDocument.id`.
  - `RetractionChecker.ensure_export(self) -> ExportCopy`.

- [ ] **Step 1: Write failing tests:**
  - `test_documents_sharing_a_doi_are_asked_once`: `"10.1/A"` and `"https://doi.org/10.1/a"` make one request, and both ids get the verdict.
  - `test_a_stored_verdict_younger_than_7_days_is_reused`: no request. At 7 days + 1 s it asks again. `refresh=True` always asks.
  - `test_unchecked_is_never_stored_as_clear`: Crossref unreachable gives a stored `unchecked` with `crossref_failure` set. The control: served + export consulted gives `clear`.
  - `test_ensure_export_downloads_once_per_checker_when_stale`: a failed refresh is not retried within the same checker.
  - `test_label_alone_retracts_with_everything_unreachable` (`publication_types=["Retracted Publication"]`).
  - `test_schema_is_idempotent` (open `LiteStorage` twice).
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Group documents by `paper_key`. Read stored rows. Collect DOIs whose row is missing, older than `RETRACTION_CHECK_MAX_AGE_DAYS`, or `refresh`. Call `lookup_crossref` once.
  - Per key: `retraction_status(doi=, export=export_evidence(...), crossref=lookups.results.get(crossref_key(doi)), publication_types=doc.publication_types or ())`, then upsert.
  - A document with no key gets `retraction_status(doi=None, export=None, crossref=None, publication_types=...)`, unstored.
- [ ] **Step 4:** Run `pytest tests/test_retraction_checks.py -v`. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(retraction): stored verdicts and the checker`

### Task 7: Screening and the reader's sentences

**Files:**
- Create: `src/bmlibrarian_lite/retraction_screen.py`, `src/bmlibrarian_lite/retraction_text.py`
- Test: `tests/test_retraction_screen.py`, `tests/test_retraction_text.py`

**Interfaces:**
- Consumes: `PaperCheck` (Task 6).
- Produces:
  - `@dataclass(frozen=True) class RetractionScreen: kept: tuple[LiteDocument, ...]; excluded: tuple[LiteDocument, ...]; checks: Mapping[str, PaperCheck]` with properties `unchecked_count`, `concern_ids`, `checked_at` (the oldest `checked_at`, or `None`). It also has `to_snapshot() -> list[dict]` and `from_snapshot(rows) -> dict[str, dict]`: per document `{document_id, doi, pmid, status, checked_at, deciding}`.
  - `screen_documents(documents, checks) -> RetractionScreen`: `retracted` goes to `excluded`, everything else to `kept`, in input order.
  - `retraction_text.py`:
    - `run_summary_clause(screen) -> str | None`, e.g. "2 retracted papers left out; 3 papers' retraction status could not be checked". It is `None` when both counts are 0, and singular forms are spelled out.
    - `format_retraction_notice(screen) -> str | None`, opening `"> **Retracted papers left out:** "`.
    - `with_retraction_notice(text, screen) -> str`, idempotent like `with_analysis_shortfall_notice`.
    - `checked_on_line(checked_at: datetime | None) -> str`: "Retraction status checked 2026-10-18", or "Retraction status never checked" for `None`.
    - `paper_status_line(check: PaperCheck) -> str` for a banner or MCP header. For a retraction: "Retracted on <date> (notice <doi>)", or "Retracted (PubMed's label)" when there is no notice DOI. For a concern: "Expression of concern, <date> (notice <doi>)". For unchecked: "Retraction status not checked: <unanswered sources>". The cause clause comes from `RequestFailure.describe()` only. Every line ends "Retraction Watch data as of <date>" when the export was consulted (spec, *The lookups*).
- [ ] **Step 1: Write failing tests:**
  - `test_no_identifier_is_unchecked_and_counted`.
  - `test_retracted_is_excluded_and_concern_kept`.
  - `test_summary_clause_counts_and_singulars`.
  - `test_notice_is_idempotent`.
  - `test_status_lines_carry_no_provider_text` (a `RequestFailure(HTTP_STATUS, 503)` line contains no response body).
  - `test_snapshot_round_trips`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run them. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(retraction): screening and the reader's sentences`

### Task 8: The systematic review's choke point and the audit trail

**Files:**
- Modify: `src/bmlibrarian_lite/gui/systematic_review_tab.py` (`WorkflowWorker.run`, between the empty check ~381 and the quality filter ~387; the checkpoint at ~444; `add_question_documents` ~456), `src/bmlibrarian_lite/audit_records.py`, `src/bmlibrarian_lite/gui/report_tab.py` (audit JSON, audit trail text)
- Test: `tests/test_retraction_workflow.py`, `tests/test_audit_records.py`

**Interfaces:**
- Consumes: `RetractionChecker`, `screen_documents`, `RetractionScreen`.
- Produces:
  - `CHECKPOINT_RETRACTION_SNAPSHOT_KEY = "retraction_snapshot"` in `audit_records.py`.
  - `checkpoint_metadata_with_retraction_snapshot(metadata, screen) -> dict` (merges, like `checkpoint_metadata_with_extraction_failures`).
  - `recorded_retraction_snapshot(checkpoint_metadata) -> dict[str, dict]`.
  - `DocumentOutcomes` gains `excluded_retracted: tuple[LiteDocument, ...] = ()`. `__post_init__` refuses a document that is both excluded and scored.
  - `classify_document_outcomes(..., excluded_retracted=())`.
  - `outcome_summary` adds `documents_excluded_retracted` and `documents_retraction_unchecked`.
  - `outcome_entries` adds `retracted_documents: [{id, title, doi, pmid, retraction_date, notice_doi}]`.
  - `WorkflowWorker` gains signal `retraction_screened = Signal(object)` (a `RetractionScreen`) and constructor kwarg `retraction_checker: RetractionChecker | None` (built in the tab from config/storage).

- [ ] **Step 1: Write failing tests** (worker run headless with a fake checker returning fixed `PaperCheck`s and fake scorer, as existing worker tests do):
  - `test_a_retracted_document_never_reaches_scoring`: the scorer sees only kept ids.
  - `test_excluded_documents_are_linked_to_the_question`: `get_document_ids_for_question` includes them.
  - `test_snapshot_is_in_checkpoint_metadata`: `recorded_retraction_snapshot` gives one row per document held, `excluded` included.
  - `test_unchecked_document_is_scored_and_counted`.
  - `test_cancel_during_the_check_ends_the_run`: `should_cancel` true after the check → no checkpoint snapshot, cancelled outcome.
  - `test_a_failing_checker_does_not_stop_the_run`: the checker raises `sqlite3.Error` → every document is unchecked, the run continues, and the summary says so.
  - `test_outcomes_refuse_excluded_and_scored`.
  - `test_audit_json_lists_retracted_documents`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - After the empty check: `checks = checker.check(documents, should_cancel=self.should_cancel)`, then `screen = screen_documents(documents, checks)`, `documents = list(screen.kept)`, and emit `retraction_screened`.
  - Link `screen.excluded` with `add_question_documents`.
  - Merge the snapshot into `checkpoint_metadata` before `create_checkpoint`.
  - Pass `screen` on so Task 9 can reach the report.
  - The tab shows `run_summary_clause` in the existing warning label (as `_on_search_incomplete` does).
- [ ] **Step 4:** Run `pytest tests/test_retraction_workflow.py tests/test_audit_records.py -v`, then `pytest tests/ -q`. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(retraction): the review leaves retracted papers out, and the audit trail says so`

### Task 9: The report

**Files:**
- Modify: `src/bmlibrarian_lite/data_models.py` (`ReportMetadata` + `to_dict`/`from_dict`), `src/bmlibrarian_lite/agents/reporting_agent.py` (`generate_report` 166, `_format_references_with_risk` 613, `format_methodology_section` 718), `src/bmlibrarian_lite/agents/report_risk_helpers.py`
- Test: `tests/test_retraction_report.py`

**Interfaces:**
- Consumes: `RetractionScreen`, `retraction_text`.
- Produces:
  - `ReportMetadata.retraction_screen: RetractionScreen | None = None`, serialised as its snapshot plus the excluded documents' titles.
  - `format_retracted_section(screen) -> str`: "## Left out as retracted" with one line per paper ("<title>. Retracted <date>; notice <doi>").
  - `format_reference_concern_annotation(check) -> str`: `"    ⚠️ Expression of concern (<date>, notice <doi>)"`.

- [ ] **Step 1: Write failing tests:**
  - `test_report_lists_left_out_papers`.
  - `test_cited_concern_paper_carries_a_caveat_at_its_reference`.
  - `test_methods_carries_the_checked_on_line`.
  - `test_report_without_exclusions_or_concerns_gains_only_the_checked_on_line`: diff against a report generated with `retraction_screen=None`, so only that line differs.
  - `test_retraction_notice_leads_the_report` (after the search and analysis notices).
  - `test_metadata_round_trips`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.** Section after References and before the high-risk section. Notice through `with_retraction_notice`. Checked-on line in `### Document Screening`.
- [ ] **Step 4:** Run them, then `pytest tests/ -q`. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(retraction): the report names left-out papers and concerns`

### Task 10: The MCP tools

**Files:**
- Modify: `src/bmlibrarian_lite/mcp_server.py` (`_handle_fact_check` 427, `_fact_check_result` 383, `_handle_search` 583, `_handle_fulltext` 620, `_handle_ask_document` 740, the context built ~853)
- Test: `tests/test_mcp_retraction.py`

**Interfaces:**
- Consumes: `RetractionChecker`, `screen_documents`, `retraction_text`.
- Produces these JSON keys:
  - `fact_check_claim` and `search_literature` gain `"retraction": {"left_out": [{title, doi, pmid, retraction_date, notice_doi}], "unchecked": <int>, "checked_at": <iso>}`. The fact-check `report` carries the notice.
  - `get_document_fulltext` and `ask_document` gain `"retraction_status": {"status", "line"}` as the first key. The `content`/`answer` text starts with `paper_status_line(...)` when the status is retracted, concern or unchecked.

- [ ] **Step 1: Write failing tests:**
  - `test_fact_check_excludes_retracted_and_lists_it`.
  - `test_search_literature_excludes_and_lists`.
  - `test_fulltext_of_a_retracted_paper_is_returned_with_the_status_first`.
  - `test_ask_document_states_unchecked`.
  - `test_each_workflow_screens_once`: a fake checker counts `check` calls, 1 per tool call.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.** Screen right after `search_agent.search` in both search tools. For the single-paper tools, check one `LiteDocument` built from the given identifiers (`get_document_fulltext`) or loaded by `document_id` (`ask_document`; no identifiers → unchecked).
- [ ] **Step 4:** Run them. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(retraction): the MCP tools leave out or flag retracted papers`

### Task 11: Badges and the interrogation banner

**Files:**
- Create: `src/bmlibrarian_lite/gui/retraction_badge.py`
- Modify: `src/bmlibrarian_lite/gui/document_card.py` (`title_row` ~364–409; a `set_retraction_check` beside `set_transparency_outcome` 754), `src/bmlibrarian_lite/gui/audit_literature_tab.py` (`_add_document_card` 215), `src/bmlibrarian_lite/gui/document_interrogation_tab.py` (`_create_top_bar` 258 or above the document pane; `_finalize_citation_load` 1304; `_clear_document` 483), stylesheet generator
- Test: `tests/test_retraction_gui.py` (offscreen; call methods directly, not synthetic key events)

**Interfaces:**
- Consumes: `PaperCheck`, `paper_status_line`.
- Produces:
  - `RetractionBadge(QLabel)`: `__init__(self, check: PaperCheck, parent=None)`. Its text is "RETRACTED", "CONCERN" or "NOT CHECKED", from `retraction_text`. It is hidden for clear. The tooltip is `paper_status_line`.
  - `DocumentCard.set_retraction_check(check: PaperCheck) -> None`.
  - `DocumentInterrogationTab.show_retraction_check(check: PaperCheck | None) -> None`:
    - retracted: prominent banner (error colour);
    - concern: warning banner;
    - unchecked: one plain line under the title;
    - clear or `None`: nothing.

- [ ] **Step 1: Write failing tests:**
  - `test_badge_text_per_status`.
  - `test_card_shows_badge_for_retracted_and_concern`.
  - `test_banner_levels`.
  - `test_clearing_the_document_hides_the_banner`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Stylesheets come through `get_stylesheet_generator()` with `ThemeColors`, never inline.
  - The interrogation tab gets the check from the checker on load: one paper, `refresh=False`, in the existing worker-thread pattern, never on the GUI thread.
- [ ] **Step 4:** Run them. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(retraction): badges and the interrogation banner`

### Task 12: `bmll retractions update`

**Files:**
- Modify: `src/bmlibrarian_lite/cli.py` (`create_parser` 272, epilog 283–299)
- Test: `tests/test_cli_retractions.py`

- [ ] **Step 1: Write failing tests:**
  - `test_update_refreshes_and_reports_the_date` (stubbed `refresh_export`): exit 0, output names `data_as_of`.
  - `test_update_failure_exits_1_and_names_the_kept_copy`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement a flat `retractions-update` subcommand (the CLI has no nested subcommands) with handler `cmd_retractions_update(args) -> int`.
- [ ] **Step 4:** Run them. Expected: PASS. Then run the full gate: `pytest tests/ -q`, `python .github/scripts/lint_delta.py --base-ref origin/master`, and the dead-proxy run (`HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY=localhost,127.0.0.1 pytest tests/ -q`).
- [ ] **Step 5: Commit.** `feat(retraction): bmll retractions-update`, then open PR 2, and mark the ledger row's python status `ported`.

---

## PR 3: saved work

### Task 13: A reopened question

**Files:**
- Modify: `src/bmlibrarian_lite/gui/app.py` (`_on_question_selected` 701), `src/bmlibrarian_lite/gui/report_tab.py` (a header line plus a *Check again* button; `display_report` 203 gains `retraction_snapshot: dict | None = None`), `src/bmlibrarian_lite/retraction_screen.py`
- Test: `tests/test_retraction_saved_work.py`

**Interfaces:**
- Produces:
  - `changed_since_snapshot(snapshot: dict[str, dict], current: Mapping[str, PaperCheck], used_ids: set[str]) -> list[tuple[str, PaperCheck]]`. It returns each used document whose current check is newer than its snapshot row and is now retracted or concern.
  - `"used"` means cited in the run's report (`get_citations_for_question` document ids).

- [ ] **Step 1: Write failing tests:**
  - `test_reopen_shows_oldest_checked_on_date`.
  - `test_reopen_of_a_pre_feature_run_says_never_checked`.
  - `test_reopen_warns_at_once_from_a_newer_stored_row`: no request is made.
  - `test_an_uncited_change_raises_no_warning`.
  - `test_check_again_updates_rows_and_snapshot_and_warns`: the report text is unchanged.
  - `test_reopen_keeps_excluded_as_excluded`: the audit trail shows the snapshot even after a reinstatement.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - The warning suggests re-running the question and runs nothing.
  - *Check again* runs the checker with `refresh=True` in a worker thread over every document linked to the question. It then writes the snapshot back with `update_checkpoint(..., metadata=checkpoint_metadata_with_retraction_snapshot(...))`.
- [ ] **Step 4:** Run them. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(retraction): a reopened question says when it was checked and can check again`

### Task 14: A saved report file

**Files:**
- Modify: `src/bmlibrarian_lite/gui/report_tab.py` (`_auto_save_report` 497: audit JSON key `"retraction": {"snapshot": [...], "checked_at": iso}`; `_load_report` 601)
- Test: `tests/test_retraction_saved_work.py`

- [ ] **Step 1: Write failing tests:**
  - `test_audit_json_carries_the_snapshot`.
  - `test_loading_a_report_shows_its_checked_on_line`.
  - `test_loading_an_old_report_says_never_checked`.
  - `test_check_again_on_a_loaded_report_rewrites_only_the_audit_json_snapshot`: the `.md` is byte-identical.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement. A loaded report's documents come from `citations[]` (all used) plus `retracted_documents`.
- [ ] **Step 4:** Run them, then the full gate as in Task 12. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(retraction): saved reports keep their check and can check again`, then open PR 3. Update HANDOVER and the ledger note.
