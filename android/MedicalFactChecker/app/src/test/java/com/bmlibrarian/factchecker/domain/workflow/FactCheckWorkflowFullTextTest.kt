/*
 * BMLibrarian Lite - Biomedical Literature Research Tool
 * Copyright (C) 2024-2026 Dr Horst Herb
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program. If not, see <https://www.gnu.org/licenses/>.
 */

package com.bmlibrarian.factchecker.domain.workflow

import android.util.Log
import com.bmlibrarian.factchecker.data.local.entity.CitationEntity
import com.bmlibrarian.factchecker.data.local.entity.DocumentEntity
import com.bmlibrarian.factchecker.data.local.entity.SessionEntity
import com.bmlibrarian.factchecker.data.remote.fulltext.FullTextService
import com.bmlibrarian.factchecker.data.remote.fulltext.FullTextService.FullTextResult
import com.bmlibrarian.factchecker.data.remote.llm.LLMService
import com.bmlibrarian.factchecker.data.repository.DocumentRepository
import com.bmlibrarian.factchecker.data.repository.ReportRepository
import com.bmlibrarian.factchecker.data.repository.SessionRepository
import com.bmlibrarian.factchecker.data.repository.SettingsRepository
import com.bmlibrarian.factchecker.domain.model.AppSettings
import com.bmlibrarian.factchecker.domain.model.LLMProvider
import com.bmlibrarian.factchecker.domain.model.SearchProvider
import com.bmlibrarian.factchecker.domain.model.WorkflowStep
import com.bmlibrarian.factchecker.util.Constants
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.Date

/**
 * The full-text step of a fact check, and the retry the reader asks for
 * (iOS's `FullTextAutoFetchTests`, `FullTextUploadTests` and the retry tests
 * in `WorkflowCancelTests`).
 *
 * Over an in-memory document store, so what a fetch records is what citation
 * extraction then reads; the model is the citation service, answering each
 * paper with one passage unless a test says otherwise.
 */
class FactCheckWorkflowFullTextTest {

    private lateinit var documentRepository: DocumentRepository
    private lateinit var reportRepository: ReportRepository
    private lateinit var settingsRepository: SettingsRepository
    private lateinit var citationService: ParallelCitationService
    private lateinit var fullTextService: FullTextService
    private lateinit var workflow: FactCheckWorkflow

    private val documents = linkedMapOf<String, DocumentEntity>()
    private val citations = mutableListOf<CitationEntity>()

    /** What each call to the citation service was asked to read. */
    private val asked = mutableListOf<List<CitationInput>>()

    /** The passages the citation service answers each paper with. */
    private var passages = listOf(LLMService.CitationExtraction("A new passage.", "Relevant."))

    private val session = SessionEntity(
        id = SESSION_ID,
        claimText = "Aspirin prevents strokes",
        pubmedQuery = "aspirin",
        workflowStep = WorkflowStep.COMPLETED,
        searchProvider = SearchProvider.PUBMED
    )

    @Before
    fun setUp() {
        documentRepository = mockk(relaxed = true)
        reportRepository = mockk(relaxed = true)
        settingsRepository = mockk(relaxed = true)
        // Relaxed for detectConcurrency, which the call's default argument asks
        citationService = mockk(relaxed = true)
        fullTextService = mockk(relaxed = true)
        val sessionRepository = mockk<SessionRepository>(relaxed = true)
        coEvery { sessionRepository.getSession(SESSION_ID) } returns session
        every { settingsRepository.getLlmProvider() } returns LLMProvider.OPENAI.id
        every { settingsRepository.getLlmModel() } returns LLMProvider.OPENAI.defaultModel
        every { settingsRepository.settings } returns MutableStateFlow(AppSettings())
        val llmService = mockk<LLMService>(relaxed = true)
        coEvery { llmService.generateReport(any(), any(), any(), any(), any()) } returns
            Result.success(LLMService.ReportGeneration(verdict = "supported", summary = "S", report = "The analysis."))

        coEvery { documentRepository.getDocumentsBySessionSync(SESSION_ID) } answers { documents.values.toList() }
        coEvery { documentRepository.getDocument(any()) } answers { documents[firstArg()] }
        coEvery { documentRepository.updateDocument(any()) } answers {
            val document = firstArg<DocumentEntity>()
            documents[document.id] = document
        }
        coEvery { documentRepository.updateCitationsReadFullText(any(), any()) } answers {
            val id = firstArg<String>()
            documents[id] = documents.getValue(id).copy(citationsReadFullText = secondArg())
        }
        coEvery { documentRepository.getCitationsBySessionSync(SESSION_ID) } answers { citations.toList() }
        coEvery { documentRepository.saveCitations(any()) } answers { citations += firstArg<List<CitationEntity>>() }
        coEvery { documentRepository.replaceCitations(any(), any()) } answers {
            citations.removeAll { it.documentId == firstArg<String>() }
            citations += secondArg<List<CitationEntity>>()
        }
        coEvery { citationService.extractCitations(any(), any(), any(), any(), any(), any(), any(), any()) } coAnswers {
            val inputs = firstArg<List<CitationInput>>()
            asked += inputs
            val onResult = arg<suspend (CitationResult) -> Unit>(7)
            inputs.map { input ->
                CitationResult.Success(input.documentId, input.pmid, passages).also { onResult(it) }
            }
        }
        coEvery { reportRepository.getReportBySession(SESSION_ID) } returns mockk(relaxed = true) {
            every { id } returns REPORT_ID
        }
        coEvery { reportRepository.createReport(any(), any(), any(), any(), any(), any(), any(), any(), any()) } returns
            mockk(relaxed = true) { every { id } returns "report-new" }

        workflow = FactCheckWorkflow(
            llmService = llmService,
            literatureSearch = mockk(relaxed = true),
            sessionRepository = sessionRepository,
            documentRepository = documentRepository,
            reportRepository = reportRepository,
            usageRepository = mockk(relaxed = true),
            settingsRepository = settingsRepository,
            parallelScoringService = mockk(relaxed = true),
            parallelCitationService = citationService,
            checkpointManager = mockk(relaxed = true),
            errorPersistenceManager = mockk(relaxed = true),
            embeddingService = mockk(relaxed = true),
            hydeGenerator = mockk(relaxed = true),
            transparencyRunner = mockk(relaxed = true),
            fullTextService = fullTextService
        )
        workflow.restoreForViewing(session)
        Log.clear()
    }

    /** Hold a relevant paper whose citations were taken from its abstract. */
    private fun holdPaper(document: DocumentEntity) {
        documents[document.id] = document
        citations += CitationEntity(documentId = document.id, passage = "From the abstract.", createdAt = Date(1_000))
    }

    private fun paper(id: String = "d1") = DocumentEntity(
        id = id, sessionId = SESSION_ID, pmid = "1", title = "Study $id", abstractText = "The abstract.", relevanceScore = 5
    )

    // ==================== The retry ====================

    @Test
    fun `an uploaded text the citations have not read is read, replaces them, and the report is regenerated`() = runTest {
        holdPaper(paper().copy(fullTextMarkdown = "The uploaded body.", fullTextSource = Constants.FULLTEXT_SOURCE_UPLOADED, citationsReadFullText = false))

        workflow.retryFullTextRetrieval(WorkflowConfig(autoFetchFullText = false))

        assertTrue(asked.single().single().content.contains("Full text:\nThe uploaded body."))
        assertEquals(listOf("A new passage."), citations.map { it.passage })
        assertEquals(true, documents.getValue("d1").citationsReadFullText)
        assertEquals(WorkflowState.Completed(reportId = "report-new"), workflow.state.value)
    }

    @Test
    fun `a retry that reads no full text keeps the report and says so`() = runTest {
        holdPaper(paper())

        workflow.retryFullTextRetrieval(WorkflowConfig(autoFetchFullText = false))

        assertTrue(asked.isEmpty())
        assertEquals(FactCheckWorkflow.NO_NEW_FULL_TEXT_NOTICE, workflow.fullTextNotice.value)
        assertEquals(WorkflowState.Completed(reportId = REPORT_ID), workflow.state.value)
        coVerify(exactly = 0) { reportRepository.createReport(any(), any(), any(), any(), any(), any(), any(), any(), any()) }
    }

    @Test
    fun `a full text that yields no passage keeps the old citations, and is not paid for again`() = runTest {
        holdPaper(paper().copy(fullTextMarkdown = "The uploaded body.", fullTextSource = Constants.FULLTEXT_SOURCE_UPLOADED, citationsReadFullText = false))
        passages = emptyList()

        workflow.retryFullTextRetrieval(WorkflowConfig(autoFetchFullText = false))

        assertEquals(listOf("From the abstract."), citations.map { it.passage })
        assertEquals(true, documents.getValue("d1").citationsReadFullText)
    }

    @Test
    fun `a full text the run budget leaves out keeps the citations the paper has`() = runTest {
        holdPaper(paper().copy(fullTextMarkdown = "x".repeat(40_000), fullTextSource = Constants.FULLTEXT_SOURCE_UPLOADED, citationsReadFullText = false))

        workflow.retryFullTextRetrieval(WorkflowConfig(autoFetchFullText = false, maxRunBudgetUsd = 0.000_1))

        assertTrue("no abstract is paid for again", asked.isEmpty())
        assertEquals(listOf("From the abstract."), citations.map { it.passage })
        assertEquals(
            FullTextAutoFetch.budgetNotice(1) + " " + FactCheckWorkflow.NO_NEW_FULL_TEXT_NOTICE,
            workflow.fullTextNotice.value
        )
    }

    // ==================== The automatic step ====================

    @Test
    fun `with the setting on, a retry asks again for a paper no source had, and its text is read`() = runTest {
        holdPaper(paper().copy(fullTextUnavailable = true, fullTextFetchedAt = Date(500)))
        coEvery { fullTextService.fetchFullText(any(), any(), any(), any(), any()) } returns
            Result.success(FullTextResult.CoreText("CORE's body."))

        workflow.retryFullTextRetrieval(WorkflowConfig(autoFetchFullText = true))

        coVerify(exactly = 1) { fullTextService.fetchFullText(any(), any(), "1", any(), any()) }
        assertTrue(asked.single().single().content.contains("Full text:\nCORE's body."))
        assertEquals(listOf("A new passage."), citations.map { it.passage })
    }

    @Test
    fun `a paper scored below the full-text score is not fetched`() = runTest {
        holdPaper(paper().copy(relevanceScore = 3, fullTextUnavailable = true))

        workflow.retryFullTextRetrieval(WorkflowConfig(autoFetchFullText = true, fullTextAutoFetchMinScore = 4))

        coVerify(exactly = 0) { fullTextService.fetchFullText(any(), any(), any(), any(), any()) }
    }

    @Test
    fun `a fetch the chain could not settle records nothing, and is named`() = runTest {
        holdPaper(paper().copy(fullTextUnavailable = true))
        coEvery { fullTextService.fetchFullText(any(), any(), any(), any(), any()) } returns Result.success(
            FullTextResult.NotEstablished(
                com.bmlibrarian.factchecker.domain.model.RequestFailure.forHttpStatus(503),
                com.bmlibrarian.factchecker.data.remote.fulltext.NotEstablishedSource.EUROPE_PMC
            )
        )

        workflow.retryFullTextRetrieval(WorkflowConfig(autoFetchFullText = true))

        coVerify(exactly = 0) { documentRepository.updateDocument(any()) }
        assertTrue(
            "got ${workflow.fullTextNotice.value}",
            workflow.fullTextNotice.value!!.startsWith(FullTextAutoFetch.failureNotice(listOf("Study d1")))
        )
    }

    @Test
    fun `a copy the reader uploaded while the targets were fetched stands`() = runTest {
        holdPaper(paper().copy(fullTextUnavailable = true))
        val uploaded = documents.getValue("d1").copy(fullTextMarkdown = "Theirs.", fullTextSource = Constants.FULLTEXT_SOURCE_UPLOADED)
        coEvery { documentRepository.getDocument("d1") } returns uploaded

        workflow.retryFullTextRetrieval(WorkflowConfig(autoFetchFullText = true))

        coVerify(exactly = 0) { fullTextService.fetchFullText(any(), any(), any(), any(), any()) }
        coVerify(exactly = 0) { documentRepository.updateDocument(any()) }
    }

    private companion object {
        const val SESSION_ID = "session-full-text"
        const val REPORT_ID = "report-1"
    }
}
