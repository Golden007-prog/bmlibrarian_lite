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
 * An author role declared on the contributor group (bmlib#111).
 *
 * About 57% of open-access articles declare `content-type="author"` on the
 * `<contrib-group>` and leave each `<contrib>` bare. A contributor's own
 * `contrib-type` decides alone; else the innermost enclosing group that declares
 * a role; else the contributor is an author. Case is folded.
 */
class JATSAuthorRoleTest {

    private fun contrib(surname: String, given: String = "A", attrs: String = ""): String =
        "<contrib $attrs><name><surname>$surname</surname><given-names>$given</given-names></name></contrib>"

    /** The surnames the parser reads as authors of an article-meta holding [groups]. */
    private fun authors(groups: String): List<String> = JATSXMLParser(
        """
        <?xml version="1.0"?>
        <article><front><article-meta>
          <title-group><article-title>Host article</article-title></title-group>
          $groups
        </article-meta></front><body><sec><title>Intro</title><p>Text.</p></sec></body></article>
        """.trimIndent().toByteArray()
    ).parseToArticle().authors.map { it.surname }

    @Test
    fun `a group role makes bare contribs authors`() {
        val groups = """<contrib-group content-type="author">${contrib("Smith")}${contrib("Jones", "B")}</contrib-group>"""
        assertEquals(listOf("Smith", "Jones"), authors(groups))
    }

    @Test
    fun `an editor group is not authors`() {
        val groups = """<contrib-group content-type="author">${contrib("Smith")}</contrib-group>
            <contrib-group content-type="editor">${contrib("Ed")}</contrib-group>"""
        assertEquals(listOf("Smith"), authors(groups))
    }

    @Test
    fun `a contribs own editor role wins inside an author group`() {
        val groups = """<contrib-group content-type="author">${contrib("Smith")}
            ${contrib("Ed", attrs = "contrib-type=\"editor\"")}</contrib-group>"""
        assertEquals(listOf("Smith"), authors(groups))
    }

    @Test
    fun `role case is folded`() {
        val groups = """<contrib-group content-type="Author">${contrib("Smith")}</contrib-group>
            <contrib-group content-type="EDITOR">${contrib("Ed")}</contrib-group>"""
        assertEquals(listOf("Smith"), authors(groups))
    }

    @Test
    fun `an empty attribute declares nothing`() {
        val groups = """<contrib-group content-type="">${contrib("Smith")}
            ${contrib("Jones", "B", "contrib-type=\"\"")}</contrib-group>"""
        assertEquals(listOf("Smith", "Jones"), authors(groups))
    }

    @Test
    fun `every group is read`() {
        val groups = """<contrib-group content-type="author">${contrib("Smith")}</contrib-group>
            <contrib-group content-type="author">${contrib("Jones", "B")}</contrib-group>"""
        assertEquals(listOf("Smith", "Jones"), authors(groups))
    }

    @Test
    fun `contrib-type author still reads`() {
        val groups = """<contrib-group>${contrib("Smith", attrs = "contrib-type=\"author\"")}
            ${contrib("Ed", attrs = "contrib-type=\"editor\"")}</contrib-group>"""
        assertEquals(listOf("Smith"), authors(groups))
    }

    @Test
    fun `a bare roster inside an editor group stays editors`() {
        val groups = """<contrib-group content-type="editor"><contrib-group>${contrib("Ed")}</contrib-group></contrib-group>
            <contrib-group content-type="author">${contrib("Smith")}</contrib-group>"""
        assertEquals(listOf("Smith"), authors(groups))
    }

    @Test
    fun `a collab roster is not listed`() {
        val groups = """<contrib-group content-type="author">${contrib("Smith")}
            <contrib><collab>Study Group<contrib-group>${contrib("Member")}</contrib-group></collab></contrib>
            </contrib-group>"""
        assertEquals(listOf("Smith"), authors(groups))
    }
}
