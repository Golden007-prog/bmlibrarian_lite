package com.bmlibrarian.factchecker.data.remote.fulltext

import com.bmlibrarian.factchecker.data.local.entity.DocumentEntity
import com.bmlibrarian.factchecker.data.remote.fulltext.FullTextService.FullTextResult
import com.bmlibrarian.factchecker.domain.model.OpenAccessShortfall
import com.bmlibrarian.factchecker.domain.model.OpenAccessSource
import com.bmlibrarian.factchecker.domain.model.RequestFailure
import com.bmlibrarian.factchecker.util.Constants
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
 * A PDF the recording saves is checked against the document's title (#546): another
 * article's is set aside unread, recorded as a wrong-article skip with its address,
 * and never an absence. An open-access candidate's lets the walk go on; a render's
 * ends the fetch on a link.
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
    private val setAside: File get() = File(folder.root, "d.pdf" + Constants.PDF_MISMATCH_SUFFIX)

    /** A download that saves another article's PDF for [wrong] and this one's otherwise. */
    private val download: suspend (String) -> PdfDownload = { url ->
        if (url == wrong || url == render) {
            writeTitledPdf(saved, "Using Effect Size - or Why the P Value Is Not Enough\nGail M. Sullivan")
        } else {
            writeTitledPdf(saved, "Caffeine and blood pressure: a critical review\nAbstract")
        }
        PdfDownload.Saved(saved.path)
    }

    private fun document(title: String = this.title, doi: String? = this.doi) =
        DocumentEntity(id = "d", sessionId = "s", title = title, doi = doi)

    private fun found(vararg urls: String) =
        FullTextResult.OpenAccessPdfs(urls.map { OpenAccessStep.Candidate(it, PdfNamer.UNPAYWALL) }, doi)

    @Test
    fun `another article's PDF is set aside and the next candidate is tried`() = runTest {
        val recorded = document().recordingFullTextFetch(found(wrong, right), download, noOpenAlex, noCore)
        assertEquals(FullTextResult.OpenAccessPdf(right, doi, PdfNamer.UNPAYWALL), recorded.result)
        assertEquals(saved.path, recorded.document.pdfPath)
        assertNull("a copy served settles the question", recorded.document.fullTextOpenAccessShortfallJson)
        assertTrue("set aside, not deleted", setAside.isFile)
    }

    @Test
    fun `the only candidate being another article's leaves the question open on the DOI link`() = runTest {
        val recorded = document().recordingFullTextFetch(found(wrong), download, noOpenAlex, noCore)
        val expected = OpenAccessShortfall.wrongArticle(OpenAccessSource.PDF, wrong)
        assertEquals(FullTextResult.DoiUrl(doiLink(doi), expected), recorded.result)
        assertEquals(expected, recorded.document.openAccessShortfall)
        assertNull(recorded.document.pdfPath)
        assertFalse(recorded.document.fullTextUnavailable)
        assertFalse("never left to be served", saved.exists())
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

    /** The control: a document stored with no title checks nothing. */
    @Test
    fun `a document with no title checks nothing`() = runTest {
        val recorded = document(title = "").recordingFullTextFetch(found(wrong, right), download, noOpenAlex, noCore)
        assertEquals(FullTextResult.OpenAccessPdf(wrong, doi, PdfNamer.UNPAYWALL), recorded.result)
        assertEquals(saved.path, recorded.document.pdfPath)
        assertFalse(setAside.exists())
    }

    /**
     * Another article's PDF that can be neither renamed nor deleted ends the walk:
     * every PDF of a document is saved under one name, so the next candidate would
     * be handed that file and refused under its own address. The question stays open.
     */
    @Test
    fun `a refused PDF that cannot be moved ends the walk`() = runTest {
        val asked = mutableListOf<String>()
        var openAlexAsked = false
        try {
            val recorded = document().recordingFullTextFetch(
                found(wrong, right),
                downloadPdf = { url ->
                    asked += url
                    download(url).also { folder.root.setWritable(false) }
                },
                askOpenAlex = { _, _ -> openAlexAsked = true; emptyList() },
                askCore = noCore
            )
            assertEquals(listOf(wrong), asked)
            assertFalse(openAlexAsked)
            val expected = OpenAccessShortfall.wrongArticle(OpenAccessSource.PDF, wrong)
            assertEquals(FullTextResult.DoiUrl(doiLink(doi), expected), recorded.result)
            assertNull(recorded.document.pdfPath)
        } finally {
            folder.root.setWritable(true)
        }
    }

    // ==================== Europe PMC's render ====================

    @Test
    fun `a render of another article ends on the DOI link, told`() = runTest {
        val recorded = document().recordingFullTextFetch(FullTextResult.EuropePmcPdf(render), download, noOpenAlex, noCore)
        val expected = OpenAccessShortfall.wrongArticle(OpenAccessSource.EUROPE_PMC_PDF, render)
        assertEquals(FullTextResult.DoiUrl(doiLink(doi), expected), recorded.result)
        assertEquals(expected, recorded.document.openAccessShortfall)
        assertNull(recorded.document.pdfPath)
        assertTrue(setAside.isFile)
        assertEquals(
            "Failed to obtain a PDF from the following tried sources: europepmc.org, named by Europe PMC " +
                "(another article's PDF). A freely available copy may exist. Whether this document is open " +
                "access was not established.",
            expected.notice
        )
    }

    /** With no DOI to link to, the render's link is kept, as a render not downloaded is (#471). */
    @Test
    fun `a render of another article with no DOI keeps the render's link, told`() = runTest {
        val recorded = document(doi = null)
            .recordingFullTextFetch(FullTextResult.EuropePmcPdf(render), download, noOpenAlex, noCore)
        assertEquals(FullTextResult.EuropePmcPdf(render), recorded.result)
        assertNull(recorded.document.pdfPath)
        assertEquals(
            OpenAccessShortfall.wrongArticle(OpenAccessSource.EUROPE_PMC_PDF, render),
            recorded.document.openAccessShortfall
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
