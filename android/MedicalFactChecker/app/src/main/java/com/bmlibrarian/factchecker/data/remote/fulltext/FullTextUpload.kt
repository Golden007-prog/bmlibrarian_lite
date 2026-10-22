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
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Log
import com.bmlibrarian.factchecker.data.local.entity.DocumentEntity
import com.bmlibrarian.factchecker.util.Constants
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.util.Date
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Why a file the reader chose could not be used as a paper's full text.
 *
 * @param message What to tell the reader
 */
class FullTextUploadException(message: String) : Exception(message)

/** What a chosen file holds, once read. */
sealed interface FullTextUploadContent {
    /**
     * A PDF, stored on the device, with the text read from it.
     *
     * @property path Where it is stored
     * @property text Its text; null when it has none (a scan)
     */
    data class Pdf(val path: String, val text: String?) : FullTextUploadContent

    /**
     * Text: Markdown, plain text, or HTML reduced to its text.
     *
     * @property text The text
     */
    data class Text(val text: String) : FullTextUploadContent
}

/**
 * A full text the reader supplies by hand, for a paper no source had.
 *
 * An uploaded PDF is kept in its own folder in the app's files
 * ([Constants.UPLOADED_FULLTEXT_DIR]), apart from the PDFs the retrieval chain
 * downloads: not in the cache the system may empty, nor where clearing the PDF
 * cache would delete it. Its text is extracted, so citation extraction reads it
 * rather than the abstract. HTML is kept as its text only, never as markup a
 * viewer would render. Mirrors iOS/macOS's `FullTextUpload`.
 */
@Singleton
class FullTextUpload @Inject constructor(
    @ApplicationContext private val context: Context
) {
    /**
     * Read a chosen file into a full text for a document.
     *
     * @param uri The file the reader chose
     * @param documentId The document, which names a stored PDF
     * @param maxBytes The largest file read; a larger one is refused, never cut
     * @return What the file holds
     * @throws FullTextUploadException for a file of a kind not supported, one
     *   too large, or one that could not be read or stored; a PDF uploaded
     *   before for the paper is kept unless the new one is stored whole
     */
    suspend fun read(
        uri: Uri,
        documentId: String,
        maxBytes: Long = Constants.MAX_UPLOAD_BYTES
    ): FullTextUploadContent = withContext(Dispatchers.IO) {
        val resolver = context.contentResolver
        val kind = kindOf(resolver.getType(uri), displayName(uri))
            ?: throw FullTextUploadException(UNSUPPORTED_MESSAGE)
        try {
            val stream = resolver.openInputStream(uri) ?: throw IOException("the file could not be opened")
            stream.use { input ->
                when (kind) {
                    Kind.PDF -> {
                        val stored = store(input, documentId, maxBytes)
                        FullTextUploadContent.Pdf(stored.path, extractedPdfText(stored.path))
                    }
                    Kind.HTML -> textContent(htmlToText(readText(input, maxBytes)))
                    Kind.TEXT -> textContent(readText(input, maxBytes))
                }
            }
        } catch (e: IOException) {
            Log.e(TAG, "The file chosen for $documentId could not be read or stored", e)
            throw FullTextUploadException("The file could not be read: ${e.message}")
        } catch (e: SecurityException) {
            Log.e(TAG, "The file chosen for $documentId could not be opened", e)
            throw FullTextUploadException("The file could not be opened.")
        }
    }

    /** A text file's content; one with no text is refused rather than stored as a full text. */
    private fun textContent(text: String): FullTextUploadContent.Text =
        FullTextUploadContent.Text(text.trim().ifEmpty { throw FullTextUploadException(NO_TEXT_MESSAGE) })

    /** The chosen file's name, when the provider gives one. */
    private fun displayName(uri: Uri): String? = try {
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) cursor.getString(0) else null
        }
    } catch (e: SecurityException) {
        null
    }

    /**
     * Copy a chosen PDF to its stored name, replacing an earlier upload only once
     * the copy is whole.
     */
    private fun store(input: InputStream, documentId: String, maxBytes: Long): File {
        val folder = File(context.filesDir, Constants.UPLOADED_FULLTEXT_DIR).apply { mkdirs() }
        // An identifier may hold characters a file name cannot
        val name = documentId.map { if (it.isLetterOrDigit()) it else '_' }.joinToString("")
        val destination = File(folder, "uploaded-$name.pdf")
        val staged = File.createTempFile("uploaded-$name.", ".part", folder)
        try {
            staged.outputStream().use { output -> copyBounded(input, output, maxBytes) }
            if (!staged.renameTo(destination)) throw IOException("the PDF could not be stored")
        } finally {
            staged.delete()
        }
        return destination
    }

    /** Read a text file, refusing one too large to read whole. */
    private fun readText(input: InputStream, maxBytes: Long): String {
        val bytes = java.io.ByteArrayOutputStream().also { copyBounded(input, it, maxBytes) }.toByteArray()
        return bytes.toString(Charsets.UTF_8)
    }

    /** Copy a stream, refusing past [maxBytes] rather than keeping part of it. */
    private fun copyBounded(input: InputStream, output: java.io.OutputStream, maxBytes: Long) {
        val buffer = ByteArray(COPY_BUFFER_BYTES)
        var total = 0L
        while (true) {
            val read = input.read(buffer)
            if (read < 0) return
            total += read
            if (total > maxBytes) throw FullTextUploadException(TOO_LARGE_MESSAGE)
            output.write(buffer, 0, read)
        }
    }

    /** The kinds of file an upload reads. */
    internal enum class Kind { PDF, HTML, TEXT }

    companion object {
        private const val TAG = "FullTextUpload"
        private const val COPY_BUFFER_BYTES = 64 * 1024

        /** The file types the picker offers. */
        val PICKER_TYPES = arrayOf("application/pdf", "text/html", "text/markdown", "text/plain", "text/x-markdown")

        /** Told when the chosen file is of a kind not supported. */
        const val UNSUPPORTED_MESSAGE = "Unsupported file format. Please use PDF, HTML, Markdown, or plain text"

        /** Told when the chosen text file holds no text. */
        const val NO_TEXT_MESSAGE = "The file holds no text, so it was not used."

        /** Told when the chosen file is larger than an upload may be. */
        val TOO_LARGE_MESSAGE = "The file is larger than ${Constants.MAX_UPLOAD_BYTES / (1024 * 1024)} MB, " +
            "so it was not used."

        /**
         * What kind of file this is: by its type, else by its name's extension.
         *
         * @param mimeType The type the provider gives, if any
         * @param name The file's name, if any
         * @return Its kind, or null for a kind not supported
         */
        internal fun kindOf(mimeType: String?, name: String?): Kind? {
            when (mimeType?.lowercase(Locale.ROOT)) {
                "application/pdf" -> return Kind.PDF
                "text/html", "application/xhtml+xml" -> return Kind.HTML
            }
            when (name?.substringAfterLast('.', "")?.lowercase(Locale.ROOT)) {
                "pdf" -> return Kind.PDF
                "html", "htm" -> return Kind.HTML
                "md", "markdown", "txt" -> return Kind.TEXT
            }
            return if (mimeType?.startsWith("text/") == true) Kind.TEXT else null
        }

        /**
         * The text of an HTML page: scripts and styles dropped, block ends kept as
         * line breaks, tags removed, the common entities decoded.
         *
         * shortcut: a regex reading of HTML, enough for the prose citation
         * extraction needs; use a real parser if uploaded pages lose their text.
         *
         * @param html The page
         * @return Its text
         */
        internal fun htmlToText(html: String): String = html
            .replace(Regex("(?is)<(script|style|head)\\b.*?</\\1\\s*>"), " ")
            .replace(Regex("(?i)<br\\s*/?>|</(p|div|h[1-6]|li|tr|section|article|blockquote)\\s*>"), "\n")
            .replace(Regex("(?s)<[^>]*>"), " ")
            .replace(Regex("&#(\\d+);")) { match ->
                match.groupValues[1].toIntOrNull()?.takeIf(Character::isValidCodePoint)
                    ?.let { String(Character.toChars(it)) } ?: match.value
            }
            .replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", "\"").replace("&#39;", "'").replace("&amp;", "&")
            .lines().joinToString("\n") { it.replace(Regex("[ \\t]+"), " ").trim() }
            .replace(Regex("\n{3,}"), "\n\n")
            .trim()
    }
}

/**
 * The document as an upload leaves it: the reader's text, recorded as uploaded.
 *
 * Replaces whatever full text the document held; a PDF's text is stored beside
 * it for the analysis. Clears what earlier fetches recorded about sources: an
 * upload settles the paper's full text.
 *
 * @param content What the chosen file holds
 * @return The document to store
 */
fun DocumentEntity.recordingUpload(content: FullTextUploadContent): DocumentEntity {
    val uploaded = copy(
        fullTextSource = Constants.FULLTEXT_SOURCE_UPLOADED,
        fullTextFetchedAt = Date(),
        fullTextUnavailable = false,
        fullTextAbstractOnly = false,
        fullTextHTML = null,
        fullTextOpenAccessShortfallJson = null,
        fullTextPdfNotSavedFrom = null
    )
    return when (content) {
        is FullTextUploadContent.Pdf -> uploaded.copy(pdfPath = content.path, pdfExtractedText = content.text, fullTextMarkdown = null)
        is FullTextUploadContent.Text -> uploaded.copy(fullTextMarkdown = content.text, pdfPath = null, pdfExtractedText = null)
    }
}
