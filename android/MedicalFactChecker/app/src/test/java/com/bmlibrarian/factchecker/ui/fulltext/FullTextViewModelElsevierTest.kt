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

package com.bmlibrarian.factchecker.ui.fulltext

import androidx.lifecycle.SavedStateHandle
import com.bmlibrarian.factchecker.data.local.dao.DocumentDao
import com.bmlibrarian.factchecker.data.local.entity.DocumentEntity
import com.bmlibrarian.factchecker.data.remote.fulltext.FullTextService
import com.bmlibrarian.factchecker.data.remote.fulltext.NotEstablishedSource
import com.bmlibrarian.factchecker.data.remote.fulltext.PdfDownload
import com.bmlibrarian.factchecker.data.remote.fulltext.writeTitledPdf
import com.bmlibrarian.factchecker.domain.model.OpenAccessShortfall
import com.bmlibrarian.factchecker.domain.model.OpenAccessSource
import com.bmlibrarian.factchecker.domain.model.RequestFailure
import com.bmlibrarian.factchecker.domain.model.RequestFailureKind
import com.bmlibrarian.factchecker.data.repository.SettingsRepository
import com.bmlibrarian.factchecker.domain.model.AppSettings
import com.bmlibrarian.factchecker.util.Constants
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * The full-text viewer and Elsevier's PDF (#480, stage C2): a stored PDF opens
 * from disk without the chain, so a key cleared since never overwrites the
 * record; a PDF the chain returns is shown as the local file it is, never as a
 * link, since Elsevier's URL needs the key.
 */
/** How long a test waits for a state reached through a real PDF read off the main thread. */
private const val TIMEOUT_MS = 10_000L

@OptIn(ExperimentalCoroutinesApi::class)
class FullTextViewModelElsevierTest {

    @get:Rule
    val folder = TemporaryFolder()

    private lateinit var fullTextService: FullTextService
    private lateinit var documentDao: DocumentDao
    private lateinit var settingsRepository: SettingsRepository

    private val document = DocumentEntity(id = "d", sessionId = "s", title = "t", doi = "10.1016/j.x.1")

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        fullTextService = mockk(relaxed = true) {
            // Elsevier's file is not under the document's ID
            every { getCachedPdfPath(any()) } returns null
            coEvery { openAlexSteps(any(), any()) } returns emptyList()
            coEvery { askCore(any()) } returns null
        }
        documentDao = mockk(relaxed = true)
        // No Elsevier key: the stored PDF must not need one
        settingsRepository = mockk(relaxed = true) {
            every { settings } returns MutableStateFlow(AppSettings())
            every { getElsevierApiKey() } returns ""
            every { getElsevierInstToken() } returns ""
        }
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    /** A PDF on disk, as Elsevier's service caches it. */
    private fun pdfOnDisk(): File = folder.newFile("elsevier-x.pdf").apply { writeBytes("%PDF-1.7\nbytes".toByteArray()) }

    /** Opens the viewer on [stored], the chain answering [answer] if asked. */
    private fun open(
        stored: DocumentEntity,
        answer: FullTextService.FullTextResult = FullTextService.FullTextResult.DoiUrl("https://doi.org/10.1016/j.x.1")
    ): FullTextViewModel {
        coEvery { documentDao.getById("d") } returns stored
        coEvery { fullTextService.fetchFullText(any(), any(), any(), any(), any()) } returns Result.success(answer)
        return FullTextViewModel(
            savedStateHandle = SavedStateHandle(mapOf(FullTextViewModel.DOCUMENT_ID_KEY to "d")),
            fullTextService = fullTextService,
            documentDao = documentDao,
            settingsRepository = settingsRepository
        )
    }

    /** An Elsevier record as recording leaves it: the local file, its source, no text. */
    private fun elsevierRecord(path: String) = document.copy(
        pdfPath = path,
        fullTextSource = Constants.FULLTEXT_SOURCE_ELSEVIER,
        fullTextFetchedAt = java.util.Date()
    )

    @Test
    fun `a stored Elsevier PDF opens from disk, and the chain is not run`() {
        val pdf = pdfOnDisk()

        val viewModel = open(elsevierRecord(pdf.absolutePath))

        assertEquals(
            FullTextViewModel.FullTextState.PdfContent(pdf.absolutePath, "t", Constants.FULLTEXT_SOURCE_ELSEVIER_LABEL),
            viewModel.state.value
        )
        coVerify(exactly = 0) { fullTextService.fetchFullText(any(), any(), any(), any(), any()) }
        coVerify(exactly = 0) { documentDao.update(any()) }
    }

    /** The control: the file gone, the chain runs, and its answer is what the reader gets. */
    @Test
    fun `a stored Elsevier PDF that is gone runs the chain`() {
        val viewModel = open(elsevierRecord(File(folder.root, "gone.pdf").absolutePath))

        coVerify(exactly = 1) { fullTextService.fetchFullText(any(), any(), any(), any(), any()) }
        val state = viewModel.state.value
        assertFalse("$state", state is FullTextViewModel.FullTextState.PdfContent)
        assertFalse("$state", state is FullTextViewModel.FullTextState.Error)
    }

    /** A stored PDF found to be another article's is not shown, and the chain runs (#546). */
    @Test
    fun `a stored Elsevier PDF of another article runs the chain`() {
        val pdf = pdfOnDisk()
        coEvery { fullTextService.refusesAsAnotherArticle(pdf.absolutePath, "t") } returns true

        val viewModel = open(elsevierRecord(pdf.absolutePath))

        coVerify(exactly = 1) { fullTextService.fetchFullText(any(), any(), any(), any(), "t") }
        val state = viewModel.state.value
        assertFalse("$state", state is FullTextViewModel.FullTextState.PdfContent)
        // Forgotten at once, and the fetch that follows does not write it back
        coVerify(atLeast = 1) { documentDao.update(match { it.id == "d" && it.pdfPath == null }) }
        coVerify(exactly = 0) { documentDao.update(match { it.pdfPath != null }) }
    }

    /**
     * A refused stored PDF takes its source and fetch date with it (#546): a fetch
     * that then records nothing must not leave an Elsevier record without its PDF,
     * whose cards would say a PDF of this article was found.
     */
    @Test
    fun `a refused stored PDF leaves no link-only record when the fetch records nothing`() {
        val pdf = pdfOnDisk()
        coEvery { fullTextService.refusesAsAnotherArticle(pdf.absolutePath, "t") } returns true
        val stored = mutableListOf<DocumentEntity>()
        coEvery { documentDao.update(capture(stored)) } returns Unit

        open(
            elsevierRecord(pdf.absolutePath),
            answer = FullTextService.FullTextResult.NotEstablished(
                RequestFailure(RequestFailureKind.TIMEOUT), NotEstablishedSource.EUROPE_PMC
            )
        )

        val last = stored.last()
        assertNull(last.pdfPath)
        assertNull(last.fullTextSource)
        assertNull(last.fullTextFetchedAt)
        assertFalse("never link-only", last.isLinkOnly)
    }

    /** The same for the PDF cached under the document's ID, checked with its title (#546). */
    @Test
    fun `a cached PDF of another article runs the chain`() {
        val pdf = pdfOnDisk()
        every { fullTextService.getCachedPdfPath("d") } returns pdf.absolutePath
        coEvery { fullTextService.refusesAsAnotherArticle(pdf.absolutePath, "t") } returns true

        val viewModel = open(document)

        coVerify(exactly = 1) { fullTextService.refusesAsAnotherArticle(pdf.absolutePath, "t") }
        coVerify(exactly = 1) { fullTextService.fetchFullText(any(), any(), any(), any(), "t") }
        assertFalse("${viewModel.state.value}", viewModel.state.value is FullTextViewModel.FullTextState.PdfContent)
    }

    /**
     * A refused PDF that was the stored one is forgotten (#546): left behind, it
     * would read as full text in hand, and a fetch that brings no PDF would keep it.
     */
    @Test
    fun `a refused stored PDF's path is forgotten`() {
        val pdf = pdfOnDisk()
        every { fullTextService.getCachedPdfPath("d") } returns pdf.absolutePath
        coEvery { fullTextService.refusesAsAnotherArticle(pdf.absolutePath, "t") } returns true

        open(document.copy(pdfPath = pdf.absolutePath))

        // Forgotten at once, and the fetch that follows does not write it back
        coVerify(atLeast = 1) { documentDao.update(match { it.id == "d" && it.pdfPath == null }) }
        coVerify(exactly = 0) { documentDao.update(match { it.pdfPath != null }) }
    }

    /** The control: a cached PDF that is not refused opens from disk. */
    @Test
    fun `a cached PDF of this article opens from disk`() {
        val pdf = pdfOnDisk()
        every { fullTextService.getCachedPdfPath("d") } returns pdf.absolutePath

        val viewModel = open(document)

        assertEquals(
            FullTextViewModel.FullTextState.PdfContent(pdf.absolutePath, "t", Constants.FULLTEXT_SOURCE_CACHED),
            viewModel.state.value
        )
        coVerify(exactly = 0) { fullTextService.fetchFullText(any(), any(), any(), any(), any()) }
    }

    /**
     * A render of another article, for a document with no DOI, ends on no link: the
     * render's names that other article (#546). The reader is told why, offered a
     * retry, and nothing is recorded.
     */
    @Test
    fun `a render of another article with no DOI offers no link, told`() = runBlocking {
        val render = "https://europepmc.org/articles/PMC1/pdf"
        // Refused by the download's own title check (#546, #561)
        coEvery { fullTextService.downloadPdf(render, "d", "Caffeine and blood pressure: a critical review") } returns
            PdfDownload.WrongArticle
        val viewModel = open(
            document.copy(doi = null, title = "Caffeine and blood pressure: a critical review"),
            answer = FullTextService.FullTextResult.EuropePmcPdf(render)
        )

        val state = withTimeout(TIMEOUT_MS) {
            viewModel.state.first { it is FullTextViewModel.FullTextState.Error || it is FullTextViewModel.FullTextState.WebUrl }
        }

        assertEquals(
            FullTextViewModel.FullTextState.Error(
                message = FullTextService.FullTextResult.OpenAccessNotEstablished(
                    OpenAccessShortfall.wrongArticle(OpenAccessSource.EUROPE_PMC_PDF, render)
                ).reason,
                canRetry = true
            ),
            state
        )
        coVerify(exactly = 0) { documentDao.update(any()) }
    }

    /** A file that exists but is not a PDF (empty, or a page) is not opened as the article. */
    @Test
    fun `a stored Elsevier file that is not a PDF runs the chain`() {
        for (content in listOf(ByteArray(0), "<html>sign in</html>".toByteArray())) {
            val file = folder.newFile().apply { writeBytes(content) }

            val viewModel = open(elsevierRecord(file.absolutePath))

            val state = viewModel.state.value
            assertFalse("$state", state is FullTextViewModel.FullTextState.PdfContent)
        }
        coVerify(exactly = 2) { fullTextService.fetchFullText(any(), any(), any(), any(), any()) }
    }

    /** The control: only a record whose source is Elsevier's owns the path it holds. */
    @Test
    fun `a PDF path left under another source is not served from disk`() {
        val pdf = pdfOnDisk()

        open(elsevierRecord(pdf.absolutePath).copy(fullTextSource = Constants.FULLTEXT_SOURCE_DOI))

        coVerify(exactly = 1) { fullTextService.fetchFullText(any(), any(), any(), any(), any()) }
    }

    /** handleFullTextResult's ElsevierPdf branch: the local file, never a link. */
    @Test
    fun `a PDF the chain returns is shown as the local file and recorded`() {
        val pdf = pdfOnDisk()

        val viewModel = open(document, FullTextService.FullTextResult.ElsevierPdf(pdf.absolutePath))

        val state = viewModel.state.value
        assertEquals(
            FullTextViewModel.FullTextState.PdfContent(pdf.absolutePath, "t", Constants.FULLTEXT_SOURCE_ELSEVIER_LABEL),
            state
        )
        assertFalse("$state", state is FullTextViewModel.FullTextState.WebUrl)
        coVerify(exactly = 0) { fullTextService.downloadPdf(any(), any(), any()) }
        coVerify {
            documentDao.update(
                match {
                    it.pdfPath == pdf.absolutePath && it.fullTextSource == Constants.FULLTEXT_SOURCE_ELSEVIER &&
                        it.fullTextPdfNotSavedFrom == null
                }
            )
        }
    }
}
