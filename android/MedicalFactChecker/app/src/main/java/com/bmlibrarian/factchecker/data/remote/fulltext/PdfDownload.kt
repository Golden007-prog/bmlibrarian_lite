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

import android.util.Log
import com.bmlibrarian.factchecker.domain.model.RequestFailure
import com.bmlibrarian.factchecker.util.Constants
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import okhttp3.ResponseBody
import okio.Buffer

private const val TAG = "PdfDownload"

/**
 * What downloading a PDF came to.
 *
 * Typed rather than a nullable path, because a PDF Unpaywall named that could
 * not be obtained is refused with its cause (#478): a null said only that
 * something went wrong, and the copy was recorded as found.
 */
sealed class PdfDownload {
    /**
     * The PDF is in the cache.
     *
     * @property path Where it was saved.
     */
    data class Saved(val path: String) : PdfDownload()

    /**
     * The source did not serve the PDF.
     *
     * @property failure Why: the status, the transport failure, `CHALLENGED`
     *   (with its status) for a bot wall's challenge page, `MALFORMED_RESPONSE`
     *   for any other body that is not a PDF, or `REQUEST_FAILED` for an
     *   address that cannot be requested.
     */
    data class Failed(val failure: RequestFailure) : PdfDownload()

    /**
     * The source served the PDF, and it could not be saved: the cache could not
     * be written. A fault of ours, which says nothing about the copy, so it is
     * no [Failed] blamed on the source; the PDF's link is kept, as BioMedLit
     * keeps it (#478).
     */
    object NotSaved : PdfDownload()

    /** The saved file's path, or null when nothing was saved. */
    val savedPath: String?
        get() = (this as? Saved)?.path
}

/**
 * Whether bytes begin as a PDF file does.
 *
 * @param prefix The first bytes of a body or file
 * @return true when they begin with [Constants.PDF_MAGIC_BYTES]
 */
fun looksLikePdf(prefix: ByteArray): Boolean =
    prefix.size >= Constants.PDF_MAGIC_BYTES.size &&
        prefix.copyOfRange(0, Constants.PDF_MAGIC_BYTES.size).contentEquals(Constants.PDF_MAGIC_BYTES)

/**
 * Whether a cached file is a PDF: present, and beginning with `%PDF`.
 *
 * @param file The file to check
 * @return true when it can be shown as the PDF
 */
internal fun isCachedPdf(file: File): Boolean {
    if (!file.isFile || file.length() == 0L) return false
    val prefix = ByteArray(Constants.PDF_MAGIC_BYTES.size)
    val read = try {
        file.inputStream().use { it.read(prefix) }
    } catch (e: IOException) {
        // A file we cannot read is no PDF we can show; it is fetched again
        return false
    }
    return read == prefix.size && looksLikePdf(prefix)
}

/**
 * Set aside a cached file that is not a PDF, so it stops being served as one.
 *
 * Quarantined rather than deleted: its bytes stay for whoever investigates. A
 * rename that fails is logged and the file deleted instead, so a recoverable
 * cache miss never becomes a thrown error.
 *
 * @param file The cached file
 * @param quarantine Where to move it; an earlier quarantined file is replaced
 */
internal fun quarantineCachedFile(file: File, quarantine: File) {
    if (quarantine.exists()) quarantine.delete()
    if (!file.renameTo(quarantine)) {
        Log.w(TAG, "Could not quarantine ${file.absolutePath}; deleting it")
        file.delete()
    }
}

/**
 * Set a saved PDF of another article aside, so the cache never serves it again (#546).
 *
 * Renamed beside itself with [Constants.PDF_MISMATCH_SUFFIX], which no lookup
 * reads, rather than deleted: the check can be wrong (a preprint retitled between
 * versions), as Python's `pdf_library_check` keeps its set-aside PDFs. One copy per
 * name is kept, the latest, replacing an earlier one: the app has no keep to
 * restore one by. A rename that fails is logged and the file deleted instead, as
 * [quarantineCachedFile] does, so the next source's download never finds it in
 * its place.
 *
 * @param file The saved PDF
 * @return Where it was set aside, or null when it could not be moved
 */
internal fun setAsideWrongArticle(file: File): File? {
    val aside = File(file.parentFile, file.name + Constants.PDF_MISMATCH_SUFFIX)
    if (aside.exists()) aside.delete()
    if (file.renameTo(aside)) return aside
    Log.e(TAG, "Could not set aside the PDF of another article at ${file.absolutePath}; deleting it")
    if (!file.delete()) Log.e(TAG, "Could not delete ${file.absolutePath} either")
    return null
}

/**
 * A fault of ours while saving a PDF: the cache could not be written.
 *
 * Not an [IOException], so that `RequestFailure.fromException` can never read
 * it as the source's connection failing.
 *
 * @param cause The write's own failure
 */
internal class CacheWriteException(cause: IOException) : Exception(cause)

/**
 * Copy a body into a file, telling the two sides' failures apart.
 *
 * A read that fails is the transport's, and propagates as it is; a write that
 * fails, or a file that cannot be opened, is ours, and is raised as a
 * [CacheWriteException].
 *
 * @param file Where to write the body
 * @throws IOException if the body could not be read
 * @throws CacheWriteException if the file could not be written
 */
internal fun InputStream.copyToCache(file: File) {
    val output = try {
        FileOutputStream(file)
    } catch (e: IOException) {
        throw CacheWriteException(e)
    }
    var copied = false
    try {
        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
        while (true) {
            val read = read(buffer)
            if (read < 0) break
            try {
                output.write(buffer, 0, read)
            } catch (e: IOException) {
                throw CacheWriteException(e)
            }
        }
        copied = true
    } finally {
        try {
            output.close()
        } catch (e: IOException) {
            // Only a close that fails after a whole copy is the outcome; after a
            // failed one, the failure already propagating is
            if (copied) throw CacheWriteException(e)
        }
    }
}

/**
 * The DOI link the full-text chain falls back to.
 *
 * @param doi The article's DOI
 * @return Its doi.org URL
 */
fun doiLink(doi: String): String = "${Constants.DOI_URL_PREFIX}$doi"

/**
 * The first bytes of a body, enough to tell a PDF ([Constants.PDF_MAGIC_BYTES]),
 * read without consuming it.
 *
 * A read that fails is the transport's and propagates: swallowed, it would
 * leave an empty start, taken for a page that is not the PDF (#478).
 *
 * @param body The answer's body
 * @return Up to [Constants.PDF_MAGIC_BYTES]'s length of its first bytes; fewer for a shorter body
 * @throws IOException if the body could not be read
 */
internal fun peekStart(body: ResponseBody): ByteArray {
    val source = body.source()
    source.request(Constants.PDF_MAGIC_BYTES.size.toLong())
    val available = minOf(source.buffer.size, Constants.PDF_MAGIC_BYTES.size.toLong())
    return source.buffer.snapshot(available.toInt()).toByteArray()
}

/**
 * Up to [limit] bytes of a body, read without consuming it, keeping what was
 * read when the stream breaks: a challenge page is not lost to its own
 * connection closing, as Python's `read_more_of_prefix` keeps it (#480).
 *
 * @param body The answer's body
 * @param limit The most bytes to read
 * @return The bytes read; fewer than [limit] for a short body or one that broke off
 */
internal fun peekPrefix(body: ResponseBody, limit: Int): ByteArray {
    val peeked = body.source().peek()
    val read = Buffer()
    try {
        while (read.size < limit && peeked.read(read, limit - read.size) >= 0) {
            // read() appends to the buffer; the loop only bounds it
        }
    } catch (e: IOException) {
        Log.d(TAG, "A page broke off after ${read.size} bytes: ${e.javaClass.simpleName}")
    }
    return read.readByteArray()
}
