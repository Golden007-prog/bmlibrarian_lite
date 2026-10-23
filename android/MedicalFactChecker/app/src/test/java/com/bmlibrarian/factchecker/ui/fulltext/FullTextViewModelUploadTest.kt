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
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.Date

/**
 * The full-text viewer and an uploaded PDF: it is shown as itself, ahead of any
 * PDF cached under the document's ID (the scan it may have replaced), and no
 * fetch is run over it or writes over it.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class FullTextViewModelUploadTest {

    @get:Rule
    val folder = TemporaryFolder()

    private lateinit var fullTextService: FullTextService
    private lateinit var documentDao: DocumentDao
    private lateinit var settingsRepository: SettingsRepository

    private val document = DocumentEntity(id = "d", sessionId = "s", title = "t", doi = "10.1/x")

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        fullTextService = mockk(relaxed = true) {
            every { getCachedPdfPath(any()) } returns null
            coEvery { refusesAsAnotherArticle(any(), any()) } returns false
            coEvery { openAlexSteps(any(), any()) } returns emptyList()
            coEvery { askCore(any()) } returns null
            coEvery { fetchFullText(any(), any(), any(), any(), any()) } returns
                Result.success(FullTextService.FullTextResult.DoiUrl("https://doi.org/10.1/x"))
        }
        documentDao = mockk(relaxed = true)
        settingsRepository = mockk(relaxed = true) {
            every { settings } returns MutableStateFlow(AppSettings())
        }
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun viewModel() = FullTextViewModel(
        savedStateHandle = SavedStateHandle(mapOf(FullTextViewModel.DOCUMENT_ID_KEY to "d")),
        fullTextService = fullTextService,
        documentDao = documentDao,
        settingsRepository = settingsRepository
    )

    private fun pdfOnDisk(name: String): File = folder.newFile(name).apply { writeBytes("%PDF-1.7\nbytes".toByteArray()) }

    /** A PDF upload as [com.bmlibrarian.factchecker.data.remote.fulltext.recordingUpload] leaves it. */
    private fun uploaded(path: String, at: Date = Date(2_000L)) = document.copy(
        pdfPath = path,
        fullTextSource = Constants.FULLTEXT_SOURCE_UPLOADED,
        fullTextFetchedAt = at
    )

    @Test
    fun `an uploaded PDF is shown ahead of the scan cached under the document, and nothing is fetched`() {
        val upload = pdfOnDisk("uploaded-d.pdf")
        every { fullTextService.getCachedPdfPath("d") } returns pdfOnDisk("d.pdf").absolutePath
        coEvery { documentDao.getById("d") } returns uploaded(upload.absolutePath)

        val viewModel = viewModel()

        assertEquals(
            FullTextViewModel.FullTextState.PdfContent(upload.absolutePath, "t", "Uploaded"),
            viewModel.state.value
        )
        coVerify(exactly = 0) { fullTextService.fetchFullText(any(), any(), any(), any(), any()) }
        coVerify(exactly = 0) { documentDao.update(any()) }
    }

    /** The control: the uploaded file gone, the chain runs and its answer is recorded. */
    @Test
    fun `an uploaded PDF that is gone runs the chain`() {
        coEvery { documentDao.getById("d") } returns uploaded(File(folder.root, "gone.pdf").absolutePath)

        viewModel()

        coVerify(exactly = 1) { fullTextService.fetchFullText(any(), any(), any(), any(), any()) }
        coVerify(exactly = 1) { documentDao.update(match { it.fullTextSource == Constants.FULLTEXT_SOURCE_DOI }) }
    }

    @Test
    fun `a PDF uploaded while the viewer fetched stands, and is shown`() {
        val upload = pdfOnDisk("uploaded-d.pdf")
        coEvery { documentDao.getById("d") } returnsMany listOf(document, uploaded(upload.absolutePath))

        val viewModel = viewModel()

        coVerify(exactly = 1) { fullTextService.fetchFullText(any(), any(), any(), any(), any()) }
        coVerify(exactly = 0) { documentDao.update(any()) }
        assertEquals(
            FullTextViewModel.FullTextState.PdfContent(upload.absolutePath, "t", "Uploaded"),
            viewModel.state.value
        )
    }
}
