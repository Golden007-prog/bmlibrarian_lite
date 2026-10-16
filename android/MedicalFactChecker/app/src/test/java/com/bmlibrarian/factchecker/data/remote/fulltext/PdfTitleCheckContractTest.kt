package com.bmlibrarian.factchecker.data.remote.fulltext

import com.bmlibrarian.factchecker.util.Constants
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * Whether a PDF is the article it was fetched for, judged by its title (#546): the
 * shared rows of `doc/cross_platform/fulltext_parity/pdf_title_check.json`, then
 * the reading of real PDFs.
 */
class PdfTitleCheckContractTest {
    @get:Rule
    val folder = TemporaryFolder()

    private val title = "Caffeine and blood pressure: a critical review perspective."

    private val contract: JsonObject by lazy {
        val relative = "doc/cross_platform/fulltext_parity/pdf_title_check.json"
        val file = generateSequence(File("").absoluteFile) { it.parentFile }
            .map { File(it, relative) }
            .firstOrNull { it.isFile }
            ?: error("could not locate $relative above ${File("").absolutePath}")
        Json.parseToJsonElement(file.readText()).jsonObject
    }

    // ==================== The shared contract ====================

    @Test
    fun `the bounds are the contract's`() {
        assertEquals(Constants.PDF_TITLE_MIN_PAIR_SHARE, contract["min_pair_share"]!!.jsonPrimitive.double, 0.0)
        assertEquals(Constants.PDF_TITLE_PAGES_READ, contract["pages_read"]!!.jsonPrimitive.int)
        assertEquals(Constants.PDF_TITLE_MIN_JOINED_CHARACTERS, contract["min_joined_characters"]!!.jsonPrimitive.int)
        assertEquals(PdfTitleCheck.OUTCOME_NAMES, contract["outcomes"]!!.jsonArray.map { it.jsonPrimitive.content })
    }

    @Test
    fun `each case`() {
        val cases = contract["cases"]!!.jsonArray.map { it.jsonObject }
        assertTrue("an empty table would pass vacuously", cases.isNotEmpty())
        for (row in cases) {
            val name = row["name"]!!.jsonPrimitive.content
            val check = PdfTitleChecker.judge(row["page"]!!.jsonPrimitive.content, row["title"]!!.jsonPrimitive.content)
            assertEquals(name, row["outcome"]!!.jsonPrimitive.content, check.outcomeName)
            val share = row["share"]
            if (share == null || share is JsonNull) {
                assertNull(name, check.share)
            } else {
                assertEquals(name, share.jsonPrimitive.double, check.share!!, 1e-6)
            }
        }
    }

    // ==================== The judgement's one bar ====================

    @Test
    fun `a compared check is judged by the bar`() {
        assertEquals(PdfTitleCheck.Matches(0.5), PdfTitleCheck.compared(0.5))
        assertEquals(PdfTitleCheck.Mismatch(0.49), PdfTitleCheck.compared(0.49))
        assertEquals(PdfTitleCheck.Mismatch(0.0), PdfTitleCheck.compared(0.0))
    }

    @Test
    fun `only a mismatch refuses a PDF`() {
        assertTrue(PdfTitleCheck.Mismatch(0.2).refusesPdf)
        for (check in listOf(
            PdfTitleCheck.Matches(1.0), PdfTitleCheck.NoTitle, PdfTitleCheck.NoText,
            PdfTitleCheck.TranslatedTitle, PdfTitleCheck.Unreadable
        )) {
            assertFalse("$check", check.refusesPdf)
        }
    }

    // ==================== Reading a real PDF ====================

    private fun pdf(vararg pages: String): File = writeTitledPdf(folder.newFile("a.pdf"), *pages)

    @Test
    fun `a PDF carrying the title matches`() {
        val file = pdf("Caffeine and blood pressure: a critical review perspective\nAbstract")
        assertEquals(PdfTitleCheck.Matches(1.0), PdfTitleChecker.check(file, title))
    }

    @Test
    fun `a title typeset over two lines matches`() {
        val file = pdf("Journal of Hypertension\nCaffeine and blood pressure: a critical review\nperspective\nA. Author")
        assertEquals(PdfTitleCheck.Matches(1.0), PdfTitleChecker.check(file, title))
    }

    @Test
    fun `another article's PDF does not match`() {
        val file = pdf("Using Effect Size - or Why the P Value Is Not Enough\nGail M. Sullivan, Richard Feinn")
        val check = PdfTitleChecker.check(
            file, "Acute Effects of Coffee Consumption on Blood Pressure and Endothelial Function"
        )
        assertTrue("$check", check.refusesPdf)
    }

    /** A title further in (a cited article, an issue's contents) is not this PDF's. */
    @Test
    fun `only the first page with text is read`() {
        assertTrue(PdfTitleChecker.check(pdf("Supplementary table S1", title), title).refusesPdf)
    }

    @Test
    fun `a first page with no text is passed over`() {
        assertEquals(PdfTitleCheck.Matches(1.0), PdfTitleChecker.check(pdf("", title, "Body"), title))
        val cover = writeTitledPdf(folder.newFile("b.pdf"), "", "Supplementary methods S1 to S9")
        assertTrue(PdfTitleChecker.check(cover, title).refusesPdf)
    }

    @Test
    fun `a scan is not checked`() {
        assertEquals(PdfTitleCheck.NoText, PdfTitleChecker.check(pdf("", ""), title))
    }

    @Test
    fun `a file that is no readable PDF is unreadable`() {
        val truncated = folder.newFile("t.pdf").apply { writeBytes("%PDF-1.7\n".toByteArray() + ByteArray(64) { '0'.code.toByte() }) }
        val empty = folder.newFile("e.pdf")
        assertEquals(PdfTitleCheck.Unreadable, PdfTitleChecker.check(truncated, title))
        assertEquals(PdfTitleCheck.Unreadable, PdfTitleChecker.check(empty, title))
        assertEquals(PdfTitleCheck.Unreadable, PdfTitleChecker.check(File(folder.root, "gone.pdf"), title))
    }

    @Test
    fun `no title opens no file`() {
        val notAPdf = folder.newFile("n.pdf").apply { writeText("not a pdf") }
        assertEquals(PdfTitleCheck.NoTitle, PdfTitleChecker.check(notAPdf, "<i></i>"))
        assertEquals(PdfTitleCheck.NoTitle, PdfTitleChecker.check(File(folder.root, "gone.pdf"), " "))
    }
}
