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

import com.bmlibrarian.factchecker.data.local.entity.CitationEntity
import com.bmlibrarian.factchecker.data.local.entity.DocumentEntity
import com.bmlibrarian.factchecker.util.Constants
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Date

/** Which papers the full-text step fetches, and which text citation extraction reads (iOS's `FullTextAutoFetchTests`). */
class FullTextAutoFetchTest {

    private val on = WorkflowConfig(relevanceThreshold = 3, autoFetchFullText = true, fullTextAutoFetchMinScore = 4)

    private fun paper(score: Int, id: String = "d$score") =
        DocumentEntity(id = id, sessionId = "s", title = "Paper $score", pmid = "1$score", relevanceScore = score)

    // ==================== The score ====================

    @Test
    fun `the full-text score is never below the relevance threshold`() {
        assertEquals(4, FullTextAutoFetch.minimumScore(relevanceThreshold = 3, autoFetchMinScore = 4))
        assertEquals(3, FullTextAutoFetch.minimumScore(relevanceThreshold = 3, autoFetchMinScore = 1))
    }

    @Test
    fun `a lowered full-text score reaches lower-scored papers`() {
        val papers = listOf(paper(5), paper(4), paper(3), paper(2))

        assertEquals(listOf("d5", "d4"), FullTextAutoFetch.documentsToFetch(papers, 3, 4).map { it.id })
        assertEquals(listOf("d5", "d4", "d3"), FullTextAutoFetch.documentsToFetch(papers, 3, 1).map { it.id })
    }

    // ==================== What is fetched ====================

    @Test
    fun `a paper no source had is asked again only on a retry`() {
        val noSource = paper(5).copy(fullTextUnavailable = true, fullTextFetchedAt = Date())

        assertTrue(FullTextAutoFetch.documentsToFetch(listOf(noSource), 3, 4).isEmpty())
        assertEquals(listOf(noSource), FullTextAutoFetch.documentsToFetch(listOf(noSource), 3, 4, retrying = true))
    }

    @Test
    fun `a paper that holds a full text, or has nothing to look it up by, is not fetched`() {
        val held = paper(5).copy(fullTextMarkdown = "# Body")
        val anonymous = paper(5).copy(pmid = " ", doi = null, pmcId = null)

        assertTrue(FullTextAutoFetch.documentsToFetch(listOf(held, anonymous), 3, 4, retrying = true).isEmpty())
    }

    // ==================== What citation extraction reads ====================

    @Test
    fun `with the setting off the abstract is read, but an upload always is`() {
        val off = on.copy(autoFetchFullText = false)
        val fetched = paper(5).copy(fullTextMarkdown = "Body text")
        val uploaded = paper(2).copy(fullTextMarkdown = "Body text", fullTextSource = Constants.FULLTEXT_SOURCE_UPLOADED)

        assertNull(FullTextAutoFetch.citationFullText(fetched, off))
        assertEquals("Body text", FullTextAutoFetch.citationFullText(uploaded, off)?.text)
        assertEquals("Body text", FullTextAutoFetch.citationFullText(fetched, on)?.text)
        assertNull("below the full-text score", FullTextAutoFetch.citationFullText(paper(3).copy(fullTextMarkdown = "Body"), on))
    }

    @Test
    fun `an abstract-only deposit is not read as the article, a PDF's text is`() {
        val deposit = paper(5).copy(fullTextMarkdown = "Abstract only", fullTextAbstractOnly = true)
        val pdf = paper(5).copy(pdfPath = "/cache/a.pdf", pdfExtractedText = "PDF prose")

        assertNull(FullTextAutoFetch.citationFullText(deposit, on))
        assertEquals("PDF prose", FullTextAutoFetch.citationFullText(pdf, on)?.text)
    }

    @Test
    fun `a long full text is cut and says so`() {
        val text = FullTextAutoFetch.citationText(paper(5).copy(fullTextMarkdown = "x".repeat(50)), maxCharacters = 10)

        assertEquals(CitationFullText("x".repeat(10), truncated = true), text)
        val input = CitationInput.fromDocument(paper(5).copy(abstractText = "The abstract"), text)
        assertTrue(input.content.startsWith("Abstract:\nThe abstract\n\nFull text:\nxxxxxxxxxx"))
        assertTrue(input.content.endsWith("[The full text is cut here for length; the rest of the article is not shown.]"))
    }

    // ==================== The budget ====================

    @Test
    fun `full texts are kept in order while they fit the budget, the rest read the abstract`() {
        // 4 characters a token: 100 characters is 25 tokens, $0.25 at a cent a token
        val inputs = listOf("a", "b", "c").map {
            CitationInput(documentId = it, pmid = null, title = it, abstract = "abstract", fullText = CitationFullText("y".repeat(100), truncated = false))
        }

        val (fitted, dropped) = FullTextAutoFetch.fittingBudget(inputs, remainingUsd = 0.6, usdPerInputToken = 0.01)

        assertEquals(1, dropped)
        assertEquals(listOf(true, true, false), fitted.map { it.fullText != null })
        assertEquals("abstract", fitted.last().content)
    }

    @Test
    fun `a text that does not fit falls back, and a later shorter one that does is kept`() {
        val inputs = listOf(400, 100).map { length ->
            CitationInput(documentId = "$length", pmid = null, title = "t", abstract = "abstract", fullText = CitationFullText("y".repeat(length), truncated = false))
        }

        val (fitted, dropped) = FullTextAutoFetch.fittingBudget(inputs, remainingUsd = 0.3, usdPerInputToken = 0.01)

        assertEquals(1, dropped)
        assertEquals(listOf(false, true), fitted.map { it.fullText != null })
    }

    // ==================== What the citations read ====================

    @Test
    fun `whether the citations read the full text is what was recorded, else judged by dates`() {
        val withText = paper(5).copy(fullTextMarkdown = "Body", fullTextFetchedAt = Date(2_000))

        assertFalse(FullTextAutoFetch.fullTextUnreadByCitations(withText.copy(citationsReadFullText = true), listOf(Date(1_000))))
        assertTrue(FullTextAutoFetch.fullTextUnreadByCitations(withText.copy(citationsReadFullText = false), listOf(Date(3_000))))
        assertTrue("older than the text", FullTextAutoFetch.fullTextUnreadByCitations(withText, listOf(Date(1_000))))
        assertFalse("newer than the text", FullTextAutoFetch.fullTextUnreadByCitations(withText, listOf(Date(3_000))))
        assertFalse("no text", FullTextAutoFetch.fullTextUnreadByCitations(paper(5), listOf(Date(1_000))))
    }

    @Test
    fun `the retry is offered for papers without full text and for full texts not yet read`() {
        val missing = paper(5)
        val unread = paper(4).copy(fullTextMarkdown = "Body", citationsReadFullText = false)
        val citation = CitationEntity(documentId = unread.id, passage = "p")

        val notice = FullTextAutoFetch.retryNotice(listOf(missing, unread), mapOf(unread.id to listOf(citation)), on)

        assertEquals(
            "1 paper scored 4 or higher has no full text. 1 paper has a full text the analysis has not read yet.",
            notice
        )
        assertNull(FullTextAutoFetch.retryNotice(listOf(paper(2)), emptyMap(), on))
    }
}
