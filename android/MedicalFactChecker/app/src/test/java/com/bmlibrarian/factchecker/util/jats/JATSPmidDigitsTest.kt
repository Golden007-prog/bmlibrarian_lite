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

package com.bmlibrarian.factchecker.util.jats

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * A PMID read from its shape is ASCII digits (bmlib#470), as Swift's
 * `isASCII && isNumber` reads it: `isDigit()` also admits Arabic-Indic digits,
 * and a value read as a PMID is pasted into the PubMed URL.
 */
class JATSPmidDigitsTest {

    private fun article(articleId: String, refPubId: String): JATSArticle = JATSXMLParser(
        """
        <?xml version="1.0"?>
        <article><front><article-meta>
          <article-id>$articleId</article-id>
          <title-group><article-title>Host article</article-title></title-group>
        </article-meta></front>
        <body><sec><title>Intro</title><p>Text.</p></sec></body>
        <back><ref-list><ref id="r1"><mixed-citation>Smith J. A study.
          <pub-id>$refPubId</pub-id></mixed-citation></ref></ref-list></back></article>
        """.trimIndent().toByteArray()
    ).parseToArticle()

    @Test
    fun arabicIndicDigitsAreNotAPmid() {
        val digits = "٣٣٣٠١٢٤٦"
        val parsed = article(digits, digits)
        assertEquals("", parsed.pmid)
        assertEquals("", parsed.references.first().pmid)
    }

    /** Control: ASCII digits are still read as the PMID, in both places. */
    @Test
    fun asciiDigitsAreStillAPmid() {
        val parsed = article("33301246", "12345678")
        assertEquals("33301246", parsed.pmid)
        assertEquals("12345678", parsed.references.first().pmid)
    }
}
