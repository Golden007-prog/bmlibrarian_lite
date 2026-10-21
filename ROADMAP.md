# ROADMAP

Planned work, decided unless an item says otherwise, and not started. Where to start on an item is in
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
| **Repeated smart search** | With the original query and a smart-search round used up, another round runs (not when the request is finishing documents a stopped fetch left unscored); the prompt lists every query already run, as its concept names joined by AND; the session keeps each query once it has run, not one a source could not answer. A round that finds nothing new leaves the report and says so; a session without a report goes on to make one. *Continue Search* (history context menu, session detail) / *Get More Evidence* always offered; the history row's "more available" badge and its button mark untried sources only. | One round only (`smartSearchEnabled` flag) | No alternative-query search at all |
| **Full-text score setting** | *Fetch Full Text From Score*, 1–5, default 4, never below the relevance threshold. | No automatic full-text step at all | No score-based full-text step; citations are abstract-only |
| **Retry Full Text Fetching** | With the setting on, asks again for qualifying papers without full text, including ones no source had; re-extracts the citations of a relevant paper whose full text they have not read (a per-paper record of whether the last extraction read the full text, `Document.citationsReadFullText`); replaces them only when the text yields passages, and not when the run budget leaves the text out; redoes transparency; regenerates the report; no full text read, no regeneration. A citation newer than the report offers a regeneration. | Per-document retry only | Not possible yet: nothing records which text a paper's citations were read from |
| **Upload Full Text** | On each paper card without readable text: PDF (text extracted, kept in its own folder beside retrieved PDFs: not in a cache the OS may empty, nor removed when the PDF cache is cleared), HTML, Markdown, plain text. An upload is read whatever the full-text setting or score (a paper below the relevance threshold is still not in the analysis). Offered during a run too: the automatic fetch leaves a paper the reader uploaded for alone. | Not implemented (only the `uploaded` source label) | Loose files in Document Interrogation only, not attached to a paper |

**Python is a design question first.** The desktop extracts citations from
abstracts only and fetches full text only for transparency and interrogation,
so the full-text rows are new behaviour there, not a port. Decide what the
desktop's equivalent is before building.

**Also owed on iOS/macOS:** HTML uploads are passed on as raw markup; an
upload is not checked against the paper's title (`PDFTitleCheck`).
