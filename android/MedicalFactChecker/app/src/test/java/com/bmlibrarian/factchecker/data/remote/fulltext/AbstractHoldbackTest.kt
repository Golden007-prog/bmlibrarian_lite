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

package com.bmlibrarian.factchecker.data.remote.fulltext

import com.bmlibrarian.factchecker.data.local.entity.DocumentEntity
import com.bmlibrarian.factchecker.data.remote.europepmc.EuropePMCArticle
import com.bmlibrarian.factchecker.data.remote.europepmc.EuropePMCSearchResult
import com.bmlibrarian.factchecker.data.remote.europepmc.EuropePMCService
import com.bmlibrarian.factchecker.data.remote.europepmc.FullTextUrlEntry
import com.bmlibrarian.factchecker.data.remote.europepmc.FullTextUrlList
import com.bmlibrarian.factchecker.data.remote.europepmc.FullTextXmlFetch
import com.bmlibrarian.factchecker.data.remote.fulltext.FullTextService.FullTextResult
import com.bmlibrarian.factchecker.domain.model.OpenAccessShortfall
import com.bmlibrarian.factchecker.domain.model.OpenAccessSource
import com.bmlibrarian.factchecker.domain.model.RequestFailure
import com.bmlibrarian.factchecker.util.jats.JATSXMLParser
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * An abstract-only deposit is held back so that a PDF of the article can win,
 * and is never read as the article (the contract's "Abstract Holdback", #384).
 */
class AbstractHoldbackTest {

    private val abstractOnly = """
        <article>
          <front><article-meta>
            <title-group><article-title>A trial</article-title></title-group>
            <abstract><p>We randomised 40 patients.</p></abstract>
          </article-meta></front>
        </article>
    """.trimIndent()

    private val withBody = """
        <article>
          <front><article-meta>
            <title-group><article-title>A trial</article-title></title-group>
          </article-meta></front>
          <body><sec><title>Methods</title><p>We randomised 40 patients.</p></sec></body>
        </article>
    """.trimIndent()

    private val render = "https://europepmc.org/articles/PMC7/pdf"
    private val pdfUrl = "https://repo.example.org/a.pdf"
    private val doi = "10.1/holdback"

    // ==================== The parser ====================

    @Test
    fun `a body paragraph with text is a body, an abstract or an empty body is not`() {
        fun producedBody(xml: String) = JATSXMLParser(xml.toByteArray()).run { parseToMarkdown(); producedBody }

        assertTrue(producedBody(withBody))
        assertFalse(producedBody(abstractOnly))
        assertFalse(producedBody(abstractOnly.replace("</article-meta></front>", "</article-meta></front><body><sec><p> </p></sec></body>")))
    }

    // ==================== The chain ====================

    @Test
    fun `an abstract-only deposit with nothing better is returned, marked, and the bucket is not asked`() = runTest {
        val bucket = absentBucket()
        val europePmc = europePmcServing(abstractOnly)

        val result = service(europePmc, bucket).fetchFullText(pmcId = "PMC7", doi = null, pmid = null).getOrThrow()

        assertTrue("got $result", result is FullTextResult.EuropePmcXml && result.abstractOnly)
        coVerify(exactly = 0) { bucket.fetchXml(any()) }
    }

    @Test
    fun `a PDF tier after an abstract-only deposit is tried, with the abstract held`() = runTest {
        val europePmc = europePmcServing(abstractOnly, renderUrl = render)

        val result = service(europePmc, absentBucket()).fetchFullText(pmcId = null, doi = null, pmid = "1").getOrThrow()

        val pdf = result as FullTextResult.EuropePmcPdf
        assertEquals(render, pdf.pdfUrl)
        assertTrue((pdf.heldAbstract as FullTextResult.EuropePmcXml).abstractOnly)
    }

    @Test
    fun `an article with a body is returned at once`() = runTest {
        val europePmc = europePmcServing(withBody, renderUrl = render)

        val result = service(europePmc, absentBucket()).fetchFullText(pmcId = null, doi = null, pmid = "1").getOrThrow()

        assertTrue("got $result", result is FullTextResult.EuropePmcXml && !result.abstractOnly)
    }

    // ==================== Recording ====================

    private val held = FullTextResult.EuropePmcXml(xml = abstractOnly, markdown = "# A trial", html = "<h1>A trial</h1>", abstractOnly = true)

    @Test
    fun `a PDF with text beats the held abstract, and its text is kept for the analysis`() = runTest {
        val recorded = document().recordingFullTextFetch(
            FullTextResult.EuropePmcPdf(render, heldAbstract = held),
            downloadPdf = { PdfDownload.Saved("/cache/d.pdf") }, askOpenAlex = noOpenAlex, askCore = noCore,
            readPdfText = { "Methods. We randomised 40 patients." }
        ).document

        assertEquals("/cache/d.pdf", recorded.pdfPath)
        assertEquals("Methods. We randomised 40 patients.", recorded.analysisFullText)
    }

    @Test
    fun `a PDF without text gives way to the held abstract, which is never read as the article`() = runTest {
        val (recorded, result) = document().recordingFullTextFetch(
            FullTextResult.EuropePmcPdf(render, heldAbstract = held),
            downloadPdf = { PdfDownload.Saved("/cache/d.pdf") }, askOpenAlex = noOpenAlex, askCore = noCore,
            readPdfText = { null }
        )

        assertEquals(held, result)
        assertNull(recorded.pdfPath)
        assertTrue(recorded.fullTextAbstractOnly)
        assertEquals("# A trial", recorded.fullTextMarkdown)
        assertNull(recorded.analysisFullText)
    }

    @Test
    fun `without a held abstract a PDF without text is still the result`() = runTest {
        val recorded = document().recordingFullTextFetch(
            FullTextResult.EuropePmcPdf(render),
            downloadPdf = { PdfDownload.Saved("/cache/d.pdf") }, askOpenAlex = noOpenAlex, askCore = noCore,
            readPdfText = { null }
        ).document

        assertEquals("/cache/d.pdf", recorded.pdfPath)
        assertNull(recorded.analysisFullText)
    }

    @Test
    fun `a textless open-access copy settles the question, and the walk goes on before the abstract`() = runTest {
        val second = "https://repo.example.org/b.pdf"
        val (recorded, result) = document().recordingFullTextFetch(
            FullTextResult.OpenAccessPdfs(
                listOf(OpenAccessStep.Candidate(pdfUrl, PdfNamer.UNPAYWALL), OpenAccessStep.Candidate(second, PdfNamer.UNPAYWALL)),
                doi,
                heldAbstract = held
            ),
            downloadPdf = { url -> if (url == pdfUrl) PdfDownload.Saved("/cache/d.pdf") else PdfDownload.Failed(RequestFailure.forHttpStatus(403)) },
            askOpenAlex = noOpenAlex, askCore = noCore,
            readPdfText = { null }
        )

        assertTrue("got $result", result is FullTextResult.EuropePmcXml)
        // The second copy's refusal is not told: the first copy settled it
        assertNull(recorded.fullTextOpenAccessShortfallJson)
        assertTrue(recorded.fullTextAbstractOnly)
    }

    @Test
    fun `an open-access copy not saved ends the walk on the held abstract with its caching note`() = runTest {
        val recorded = document().recordingFullTextFetch(
            FullTextResult.OpenAccessPdfs(listOf(OpenAccessStep.Candidate(pdfUrl, PdfNamer.UNPAYWALL)), doi, heldAbstract = held),
            downloadPdf = { PdfDownload.NotSaved }, askOpenAlex = noOpenAlex, askCore = noCore,
            readPdfText = { null }
        ).document

        assertTrue(recorded.fullTextAbstractOnly)
        assertEquals(pdfUrl, recorded.fullTextPdfNotSavedFrom)
    }

    @Test
    fun `with no copy served the held abstract carries what went unsettled`() = runTest {
        val recorded = document().recordingFullTextFetch(
            FullTextResult.OpenAccessPdfs(listOf(OpenAccessStep.Candidate(pdfUrl, PdfNamer.UNPAYWALL)), doi, heldAbstract = held),
            downloadPdf = { PdfDownload.Failed(RequestFailure.forHttpStatus(503)) }, askOpenAlex = noOpenAlex, askCore = noCore,
            readPdfText = { null }
        ).document

        assertEquals(
            OpenAccessShortfall(OpenAccessSource.PDF, RequestFailure.forHttpStatus(503), pdfUrl).toJson(),
            recorded.fullTextOpenAccessShortfallJson
        )
        assertTrue(recorded.fullTextAbstractOnly)
    }

    // ==================== Helpers ====================

    private val noOpenAlex: suspend (String, List<String>) -> List<OpenAccessStep> = { _, _ -> emptyList() }
    private val noCore: suspend (String) -> CoreFetch? = { null }

    private fun document() = DocumentEntity(id = "d", sessionId = "s", title = "A trial", doi = doi)

    /** The chain over this Europe PMC and bucket, every other source absent. */
    private fun service(europePmc: EuropePMCService, bucket: PmcOpenDataService) = FullTextService(
        context = mockk(relaxed = true),
        europePmcService = europePmc,
        unpaywallApi = mockk<UnpaywallApi>(),
        httpClient = mockk(relaxed = true),
        pmcOpenData = bucket,
        openAlex = absentOpenAlex(),
        core = absentCore(),
        elsevier = absentElsevier()
    )

    /**
     * A Europe PMC whose search finds PMC7 (with a free render when given) and
     * whose XML fetch serves [xml].
     */
    private fun europePmcServing(xml: String, renderUrl: String? = null): EuropePMCService = mockk {
        coEvery { search(any(), any(), any(), any(), any()) } returns Result.success(
            EuropePMCSearchResult(
                articles = listOf(
                    EuropePMCArticle(
                        id = "1", source = "MED", pmid = "1", pmcid = "PMC7", title = "A trial",
                        fullTextUrlList = renderUrl?.let {
                            FullTextUrlList(listOf(FullTextUrlEntry(availability = "Free", documentStyle = "pdf", site = "Europe_PMC", url = it)))
                        }
                    )
                ),
                totalResults = 1,
                nextCursor = null,
                resultsReceived = 1,
                shortfalls = emptyList()
            )
        )
        coEvery { fetchFullTextXml(any()) } returns FullTextXmlFetch.Served(xml)
    }
}
