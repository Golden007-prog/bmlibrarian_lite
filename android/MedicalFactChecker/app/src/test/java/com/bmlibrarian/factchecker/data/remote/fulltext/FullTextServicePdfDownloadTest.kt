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

import android.content.Context
import com.bmlibrarian.factchecker.data.remote.europepmc.EuropePMCService
import com.bmlibrarian.factchecker.domain.model.RequestFailure
import com.bmlibrarian.factchecker.domain.model.RequestFailureKind
import io.mockk.every
import io.mockk.mockk
import java.io.File
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * Downloading a PDF says why it got none (#478), and keeps only a PDF: a page
 * served in its place was saved as the PDF and returned as cached ever after.
 */
class FullTextServicePdfDownloadTest {

    @get:Rule
    val cache = TemporaryFolder()

    private lateinit var server: MockWebServer
    private lateinit var service: FullTextService

    private val pdfBytes = "%PDF-1.7\n%âãÏÓ\n1 0 obj\n".toByteArray(Charsets.ISO_8859_1)

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val context = mockk<Context> { every { cacheDir } returns cache.root }
        service = FullTextService(
            context = context,
            europePmcService = mockk<EuropePMCService>(relaxed = true),
            unpaywallApi = mockk(relaxed = true),
            httpClient = OkHttpClient(),
            pmcOpenData = absentBucket(),
            openAlex = absentOpenAlex(),
            core = absentCore(),
            elsevier = absentElsevier()
        )
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private suspend fun download(): PdfDownload = service.downloadPdf(server.url("/a.pdf").toString(), "d")

    private fun cachedFiles(): List<String> =
        cache.root.walkTopDown().filter { it.isFile }.map { it.name }.toList()

    @Test
    fun `a PDF is saved and its path answered`() = runTest {
        server.enqueue(MockResponse().setHeader("Content-Type", "application/pdf").setBody(Buffer().write(pdfBytes)))

        val saved = download() as PdfDownload.Saved

        assertArrayEquals(pdfBytes, File(saved.path).readBytes())
        assertEquals(listOf("d.pdf"), cachedFiles())
    }

    /** A challenge page served with 200 is not the PDF, and nothing is cached (#480). */
    @Test
    fun `a page served in place of the PDF is not saved`() = runTest {
        server.enqueue(MockResponse().setHeader("Content-Type", "text/html").setBody("<html>Sign in to read</html>"))

        assertEquals(PdfDownload.Failed(RequestFailure(RequestFailureKind.MALFORMED_RESPONSE)), download())
        assertEquals(emptyList<String>(), cachedFiles())
        assertNull(service.getCachedPdfPath("d"))
    }

    @Test
    fun `a challenge served as a PDF is challenged and not saved`() = runTest {
        server.enqueue(
            MockResponse().setHeader("Content-Type", "application/pdf").setBody("<title>Preparing to download")
        )

        assertEquals(PdfDownload.Failed(RequestFailure(RequestFailureKind.CHALLENGED, 200)), download())
        assertEquals(emptyList<String>(), cachedFiles())
        assertNull(service.getCachedPdfPath("d"))
    }

    @Test
    fun `a cloudflare 403 is challenged`() = runTest {
        server.enqueue(MockResponse().setResponseCode(403).setHeader("Server", "cloudflare").setBody("<html>"))

        assertEquals(PdfDownload.Failed(RequestFailure(RequestFailureKind.CHALLENGED, 403)), download())
    }

    /** The page's markers, not the Server header, are what name the wall here (#480). */
    @Test
    fun `a 403 whose page names the wall is challenged`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(403).setHeader("Server", "nginx").setBody("<html><title>Just a moment...")
        )

        assertEquals(PdfDownload.Failed(RequestFailure(RequestFailureKind.CHALLENGED, 403)), download())
    }

    /**
     * A refusal whose page cannot be read keeps its answer: the unread page is
     * no reason to call a 403 a connection failure. The enqueued response
     * promises more body than it sends, then drops the connection, which fails
     * the read of the page.
     */
    @Test
    fun `a cloudflare 403 whose page breaks off is still challenged`() = runTest {
        server.enqueue(brokenBody(403).setHeader("Server", "cloudflare"))

        assertEquals(PdfDownload.Failed(RequestFailure(RequestFailureKind.CHALLENGED, 403)), download())
    }

    @Test
    fun `an ordinary 403 whose page breaks off keeps its status`() = runTest {
        server.enqueue(brokenBody(403).setHeader("Server", "nginx"))

        assertEquals(PdfDownload.Failed(RequestFailure.forHttpStatus(403)), download())
    }

    @Test
    fun `a 404 whose page breaks off is still gone`() = runTest {
        server.enqueue(brokenBody(404))

        assertEquals(PdfDownload.Failed(RequestFailure.forHttpStatus(404)), download())
    }

    private fun brokenBody(status: Int): MockResponse =
        MockResponse()
            .setResponseCode(status)
            .setBody(Buffer().write(ByteArray(BROKEN_PAGE_BYTES)))
            .setSocketPolicy(SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY)

    @Test
    fun `an ordinary 403 keeps its status`() = runTest {
        server.enqueue(MockResponse().setResponseCode(403).setHeader("Server", "nginx").setBody("<html>"))

        assertEquals(PdfDownload.Failed(RequestFailure.forHttpStatus(403)), download())
    }

    @Test
    fun `a refusal keeps its status`() = runTest {
        for (status in listOf(403, 404, 503)) {
            server.enqueue(MockResponse().setResponseCode(status))

            assertEquals("$status", PdfDownload.Failed(RequestFailure(RequestFailureKind.HTTP_STATUS, status)), download())
        }
    }

    /** No answer at all is a connection failure, which the reader is told "could not be asked". */
    @Test
    fun `a dropped connection is a connection failure`() = runTest {
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST))

        assertEquals(PdfDownload.Failed(RequestFailure(RequestFailureKind.CONNECTION)), download())
    }

    @Test
    fun `an address that cannot be requested is never asked for`() = runTest {
        for (address in listOf("ftp://repo.example.org/a.pdf", "/a.pdf", "file:///tmp/a.pdf")) {
            assertEquals(
                address,
                PdfDownload.Failed(RequestFailure(RequestFailureKind.REQUEST_FAILED)),
                service.downloadPdf(address, "d")
            )
        }
        assertEquals(0, server.requestCount)
    }

    /** A body that breaks off is a failure, and no part of it is left to be served as cached. */
    @Test
    fun `a download that breaks off leaves nothing cached`() = runTest {
        server.enqueue(
            MockResponse()
                .setBody(Buffer().write(pdfBytes + ByteArray(TRUNCATED_BODY_PADDING)))
                .setSocketPolicy(SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY)
        )

        assertTrue(download() is PdfDownload.Failed)
        assertEquals(emptyList<String>(), cachedFiles())
        assertNull(service.getCachedPdfPath("d"))
    }

    /**
     * A cache that cannot be written is our fault, never blamed on the source
     * as a connection failure: the PDF was served (#478).
     */
    @Test
    fun `a PDF that could not be saved is not the source's failure`() = runTest {
        File(cache.root, "fulltext_pdfs").writeText("a file where the cache directory should be")
        server.enqueue(MockResponse().setBody(Buffer().write(pdfBytes)))

        assertSame(PdfDownload.NotSaved, download())
        assertEquals(1, server.requestCount)
        assertEquals(listOf("fulltext_pdfs"), cachedFiles())
    }

    /** A page an earlier build saved as the PDF is quarantined and fetched again. */
    @Test
    fun `a cached file that is not a PDF is fetched again`() = runTest {
        val stale = File(cache.root, "fulltext_pdfs/d.pdf").apply {
            parentFile?.mkdirs()
            writeText("<html>login</html>")
        }
        assertNull(service.getCachedPdfPath("d"))
        server.enqueue(MockResponse().setBody(Buffer().write(pdfBytes)))

        val saved = download() as PdfDownload.Saved

        assertEquals(stale.absolutePath, saved.path)
        assertArrayEquals(pdfBytes, stale.readBytes())
        assertEquals(1, server.requestCount)
        assertEquals("<html>login</html>", File(cache.root, "fulltext_pdfs/d.pdf.corrupt").readText())
    }

    /** The control: a cached PDF is served without asking again. */
    @Test
    fun `a cached PDF is served without a request`() = runTest {
        File(cache.root, "fulltext_pdfs/d.pdf").apply {
            parentFile?.mkdirs()
            writeBytes(pdfBytes)
        }

        assertTrue(download() is PdfDownload.Saved)
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `only bytes that begin with the PDF signature look like a PDF`() {
        assertTrue(looksLikePdf(pdfBytes))
        assertFalse(looksLikePdf("%PD".toByteArray()))
        assertFalse(looksLikePdf("<html>".toByteArray()))
        assertFalse(looksLikePdf(ByteArray(0)))
    }

    private companion object {
        /** Enough body that the server breaks off well before its end. */
        const val TRUNCATED_BODY_PADDING = 256 * 1024

        /** Well under the 64 KiB read of a refusal's page, of which MockWebServer sends half. */
        const val BROKEN_PAGE_BYTES = 8 * 1024
    }
}
