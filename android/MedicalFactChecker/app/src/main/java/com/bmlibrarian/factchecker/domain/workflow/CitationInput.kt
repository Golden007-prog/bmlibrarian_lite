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

import com.bmlibrarian.factchecker.data.local.entity.DocumentEntity

/**
 * Input data for extracting citations from a single document.
 *
 * Contains only the fields needed for the citation extraction prompt, avoiding
 * direct reference to Room entity objects for thread safety in coroutines.
 *
 * Mirrors iOS CitationInput for cross-platform consistency.
 *
 * @property documentId Unique identifier for matching back to the DocumentEntity.
 * @property pmid PubMed ID for display and tracking.
 * @property title Document title for the extraction prompt.
 * @property abstract The document's abstract, if it has one.
 * @property markdownFallback What is read when there is neither an abstract nor
 *   a full text for the prompt: the stored markdown, as before full text was read.
 * @property fullText The article's full text, when citation extraction is to read it.
 * @property fullTextTruncated Whether [fullText] was cut to bound the cost of the call.
 */
data class CitationInput(
    val documentId: String,
    val pmid: String?,
    val title: String,
    val abstract: String?,
    val markdownFallback: String? = null,
    val fullText: String? = null,
    val fullTextTruncated: Boolean = false
) {
    /**
     * What the prompt reads the paper from: the full text when there is one.
     *
     * The abstract stays alongside the full text, which may be cut and so may
     * lack the conclusions an abstract states. A cut is announced, so the model
     * does not mistake the end of the excerpt for the end of the paper. Swift's
     * `sourceSection(for:)` lays it out the same way.
     */
    val content: String
        get() = if (fullText == null) {
            abstract ?: markdownFallback ?: ""
        } else {
            buildString {
                abstract?.let { append("Abstract:\n").append(it).append("\n\n") }
                append("Full text:\n").append(fullText)
                if (fullTextTruncated) append("\n[The full text is cut here for length; the rest of the article is not shown.]")
            }
        }

    /** This input read from the abstract instead, as when the run budget leaves the full text out. */
    fun withoutFullText(): CitationInput = copy(fullText = null, fullTextTruncated = false)

    companion object {
        /**
         * Create citation input from a DocumentEntity.
         *
         * @param document The document entity to convert.
         * @param fullText The full text to read, or null to read the abstract
         *   (or, with none, the stored markdown).
         * @return CitationInput with extracted fields.
         */
        fun fromDocument(document: DocumentEntity, fullText: CitationFullText? = null): CitationInput {
            return CitationInput(
                documentId = document.id,
                pmid = document.pmid,
                title = document.title,
                abstract = document.abstractText,
                markdownFallback = document.fullTextMarkdown,
                fullText = fullText?.text,
                fullTextTruncated = fullText?.truncated ?: false
            )
        }
    }
}
