# ROADMAP

Planned work that is decided but not started. Where to start on an item is in
`HANDOVER.md`; remove an item here once it has landed.

---

## Parity: taking a fact check further (iOS/macOS, PR #611)

iOS and macOS gained five ways to take a finished fact check further. Android
and the Python desktop need the same, with the same rules. Swift is the
reference: `FactCheckWorkflow.swift`, `FullTextAutoFetch.swift`,
`FullTextUpload.swift`, `PromptTemplates.alternativeQueries`.

| Feature | Rule (as on iOS/macOS) | Android | Python desktop |
|---|---|---|---|
| **Find More Evidence / New Question** | After a search, the submit button fetches more for the same claim; an edited claim is a new question. New Question clears claim and session. | Fetch-more exists (Report screen only); `startNewQuestion()` exists, no button | No one-click "fetch more and regenerate"; Research Questions has *Re-run Search* |
| **Repeated smart search** | With the original query and a smart-search round used up, another round runs; the prompt lists every query already run; the session keeps them all. A round that finds nothing new leaves the report and says so. *Continue Search* / *Get More Evidence* always offered; the "more available" badge marks untried sources only. | One round only (`smartSearchEnabled` flag) | No alternative-query search at all |
| **Full-text score setting** | *Fetch Full Text From Score*, 1–5, default 4, never below the relevance threshold. | No automatic full-text step at all | No score-based full-text step; citations are abstract-only |
| **Retry Full Text Fetching** | Asks again for qualifying papers without full text, including ones no source had; re-extracts citations older than the paper's full text (only once the new ones exist); redoes transparency; regenerates the report; no full text read, no regeneration. | Per-document retry only | Not possible yet: citations carry no timestamp |
| **Upload Full Text** | On each paper card without readable text: PDF (text extracted, kept with retrieved PDFs, not in a cache the OS may empty), HTML, Markdown, text. An upload is always read, whatever the setting or score. | Not implemented (only the `uploaded` source label) | Loose files in Document Interrogation only, not attached to a paper |

**Python is a design question first.** The desktop extracts citations from
abstracts only and fetches full text only for transparency and interrogation,
so the full-text rows are new behaviour there, not a port. Decide what the
desktop's equivalent is before building.

**Also owed on iOS/macOS:** HTML uploads are passed on as raw markup; an
upload is not checked against the paper's title (`PDFTitleCheck`).
