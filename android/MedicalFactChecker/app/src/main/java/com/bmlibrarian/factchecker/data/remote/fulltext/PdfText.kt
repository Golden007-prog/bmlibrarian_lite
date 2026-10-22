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
import com.itextpdf.commons.exceptions.ITextException
import com.itextpdf.kernel.pdf.PdfDocument
import com.itextpdf.kernel.pdf.PdfReader
import com.itextpdf.kernel.pdf.canvas.parser.PdfTextExtractor
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException

private const val TAG = "PdfText"

/**
 * The text of a PDF on disk, every page in order, for the analysis to read.
 *
 * What citation extraction reads of a PDF full text, downloaded or uploaded: a
 * PDF is shown as itself, so its text is stored beside it
 * ([com.bmlibrarian.factchecker.data.local.entity.DocumentEntity.pdfExtractedText]).
 * Read off the main thread: iText parses every page.
 *
 * @param path The PDF on disk
 * @return Its text, trimmed; null when it has none (a scan) or cannot be read as
 *   a PDF (damaged, a password, no pages, gone), which is logged
 */
suspend fun extractedPdfText(path: String): String? = withContext(Dispatchers.IO) {
    val file = File(path)
    val text = try {
        // The reader closed too when the document cannot be built from it
        PdfReader(file).use { reader ->
            PdfDocument(reader).use { document ->
                (1..document.numberOfPages).joinToString("\n\n") { number ->
                    PdfTextExtractor.getTextFromPage(document.getPage(number))
                }
            }
        }
    } catch (e: IOException) {
        Log.w(TAG, "${file.name} could not be read for its text", e)
        return@withContext null
    } catch (e: ITextException) {
        // iText's own: a damaged file, a password (BadPasswordException)
        Log.w(TAG, "${file.name} could not be read for its text", e)
        return@withContext null
    } catch (e: StackOverflowError) {
        // A page tree nested past the stack (a damaged or hostile file): the file's,
        // and the app must not die of it
        Log.w(TAG, "${file.name} is nested too deeply to read for its text", e)
        return@withContext null
    } catch (e: RuntimeException) {
        // iText documents no closed set of errors: one that is not iText's is a
        // fault of the code or the install, and the file is still only unread
        Log.e(TAG, "Reading ${file.name} for its text failed in the code, not the file", e)
        return@withContext null
    }
    text.trim().ifEmpty {
        Log.w(TAG, "${file.name} has no text on its pages")
        null
    }
}
