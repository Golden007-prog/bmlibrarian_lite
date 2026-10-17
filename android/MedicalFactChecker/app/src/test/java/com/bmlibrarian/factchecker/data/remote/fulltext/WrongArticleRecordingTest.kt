package com.bmlibrarian.factchecker.data.remote.fulltext

import com.bmlibrarian.factchecker.data.local.entity.DocumentEntity
import com.bmlibrarian.factchecker.data.remote.fulltext.FullTextService.FullTextResult
import com.bmlibrarian.factchecker.domain.model.OpenAccessShortfall
import com.bmlibrarian.factchecker.domain.model.OpenAccessSource
import com.bmlibrarian.factchecker.domain.model.RequestFailure
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * Another article's PDF, refused by the download's own title check before it is
 * cached (#546, #561), is recorded as a wrong-article skip with its address, and
 * never an absence. An open-access candidate's lets the walk go on; a render's
 * ends the fetch on the DOI link, or with no DOI on no link. The check itself is
 * `FullTextServicePdfDownloadTest`'s.
 */
class WrongArticleRecordingTest {
    @get:Rule
    val folder = TemporaryFolder()

    private val doi = "10.1/wrong"
    private val title = "Caffeine and blood pressure: a critical review"
    private val wrong = "https://wrong.example.org/a.pdf"
    private val right = "https://right.example.org/b.pdf"
    private val render = "https://europepmc.org/articles/PMC1/pdf"

    /** OpenAlex naming nothing, and CORE with no key. */
    private val noOpenAlex: suspend (String, List<String>) -> List<OpenAccessStep> = { _, _ -> emptyList() }
    private val noCore: suspend (String) -> CoreFetch? = { null }

    /** Where the download saves, as `FullTextService.downloadPdf` saves every PDF of a document. */
    private val saved: File get() = File(folder.root, "d.pdf")

    /** A download whose check refuses [wrong] and the render, and saves anything else. */
    private val download: suspend (String) -> PdfDownload = { url ->
        if (url == wrong || url == render) {
            PdfDownload.WrongArticle
        } else {
            saved.writeText("%PDF-1.7")
            PdfDownload.Saved(saved.path)
        }
    }

    private fun document(title: String = this.title, doi: String? = this.doi) =
        DocumentEntity(id = "d", sessionId = "s", title = title, doi = doi)

    private fun found(vararg urls: String) =
        FullTextResult.OpenAccessPdfs(urls.map { OpenAccessStep.Candidate(it, PdfNamer.UNPAYWALL) }, doi)

    @Test
    fun `another article's PDF is told and the next candidate is tried`() = runTest {
        val recorded = document().recordingFullTextFetch(found(wrong, right), download, noOpenAlex, noCore)
        assertEquals(FullTextResult.OpenAccessPdf(right, doi, PdfNamer.UNPAYWALL), recorded.result)
        assertEquals(saved.path, recorded.document.pdfPath)
        assertNull("a copy served settles the question", recorded.document.fullTextOpenAccessShortfallJson)
    }

    @Test
    fun `the only candidate being another article's leaves the question open on the DOI link`() = runTest {
        val recorded = document().recordingFullTextFetch(found(wrong), download, noOpenAlex, noCore)
        val expected = OpenAccessShortfall.wrongArticle(OpenAccessSource.PDF, wrong)
        assertEquals(FullTextResult.DoiUrl(doiLink(doi), expected), recorded.result)
        assertEquals(expected, recorded.document.openAccessShortfall)
        assertNull(recorded.document.pdfPath)
        assertFalse(recorded.document.fullTextUnavailable)
        assertEquals(
            "Failed to obtain a PDF from the following tried sources: wrong.example.org, named by Unpaywall " +
                "(another article's PDF). A freely available copy may exist. Whether this document is open " +
                "access was not established.",
            expected.notice
        )
    }

    /** OpenAlex's candidate, refused as another article's, is recorded under OpenAlex's copy. */
    @Test
    fun `OpenAlex's candidate of another article is told as OpenAlex's`() = runTest {
        val recorded = document().recordingFullTextFetch(
            found(right),
            downloadPdf = { url -> if (url == right) PdfDownload.Failed(RequestFailure.forHttpStatus(403)) else download(url) },
            askOpenAlex = { _, _ -> listOf(OpenAccessStep.Candidate(wrong, PdfNamer.OPENALEX)) },
            askCore = noCore
        )
        val expected = OpenAccessShortfall(OpenAccessSource.PDF, RequestFailure.forHttpStatus(403), right) +
            OpenAccessShortfall.wrongArticle(OpenAccessSource.OPENALEX_PDF, wrong)
        assertEquals(FullTextResult.DoiUrl(doiLink(doi), expected), recorded.result)
    }

    /**
     * A refusal never stops the walk (#561): the download checks a PDF before it
     * takes the document's cache name, so nothing it left there can be handed to
     * the next candidate, and every later candidate and OpenAlex are still asked.
     */
    @Test
    fun `a refusal lets every later source be asked`() = runTest {
        val asked = mutableListOf<String>()
        var openAlexAsked = false
        val recorded = document().recordingFullTextFetch(
            found(wrong, wrong + "?again"),
            downloadPdf = { url -> asked += url; PdfDownload.WrongArticle },
            askOpenAlex = { _, _ -> openAlexAsked = true; listOf(OpenAccessStep.Candidate(right, PdfNamer.OPENALEX)) },
            askCore = noCore
        )
        assertEquals(listOf(wrong, wrong + "?again", right), asked)
        assertTrue(openAlexAsked)
        val expected = OpenAccessShortfall.wrongArticle(OpenAccessSource.PDF, wrong) +
            OpenAccessShortfall.wrongArticle(OpenAccessSource.PDF, wrong + "?again") +
            OpenAccessShortfall.wrongArticle(OpenAccessSource.OPENALEX_PDF, right)
        assertEquals(FullTextResult.DoiUrl(doiLink(doi), expected), recorded.result)
    }

    // ==================== Europe PMC's render ====================

    @Test
    fun `a render of another article ends on the DOI link, told`() = runTest {
        val recorded = document().recordingFullTextFetch(FullTextResult.EuropePmcPdf(render), download, noOpenAlex, noCore)
        val expected = OpenAccessShortfall.wrongArticle(OpenAccessSource.EUROPE_PMC_PDF, render)
        assertEquals(FullTextResult.DoiUrl(doiLink(doi), expected), recorded.result)
        assertEquals(expected, recorded.document.openAccessShortfall)
        assertNull(recorded.document.pdfPath)
        assertEquals(
            "Failed to obtain a PDF from the following tried sources: europepmc.org, named by Europe PMC " +
                "(another article's PDF). A freely available copy may exist. Whether this document is open " +
                "access was not established.",
            expected.notice
        )
    }

    /**
     * With no DOI to link to, nothing is: the render's link names that other article.
     * Nothing is recorded, so the fetch stays on offer, and the reader is told why.
     */
    @Test
    fun `a render of another article with no DOI ends on no link, told`() = runTest {
        val stored = document(doi = null)
        val recorded = stored.recordingFullTextFetch(FullTextResult.EuropePmcPdf(render), download, noOpenAlex, noCore)
        val expected = OpenAccessShortfall.wrongArticle(OpenAccessSource.EUROPE_PMC_PDF, render)
        assertEquals(FullTextResult.OpenAccessNotEstablished(expected), recorded.result)
        assertEquals("recorded nothing", stored, recorded.document)
        assertEquals(
            "No source provided this article's full text. Failed to obtain a PDF from the following tried " +
                "sources: europepmc.org, named by Europe PMC (another article's PDF). A freely available copy " +
                "may exist. Whether this document is open access was not established.",
            (recorded.result as FullTextResult.OpenAccessNotEstablished).reason
        )
    }

    /** The control: a render that carries the title is recorded as its PDF. */
    @Test
    fun `a render of this article is its PDF`() = runTest {
        val recorded = document().recordingFullTextFetch(
            FullTextResult.EuropePmcPdf(right), download, noOpenAlex, noCore
        )
        assertEquals(saved.path, recorded.document.pdfPath)
        assertNull(recorded.document.openAccessShortfall)
    }
}
