package com.bmlibrarian.factchecker.data.remote.fulltext

import com.itextpdf.io.font.constants.StandardFonts
import com.itextpdf.kernel.font.PdfFontFactory
import com.itextpdf.kernel.pdf.PdfDocument
import com.itextpdf.kernel.pdf.PdfWriter
import com.itextpdf.kernel.pdf.canvas.PdfCanvas
import java.io.File

/** Where the first line of a test PDF's text is drawn, in points from the bottom. */
private const val FIRST_LINE_Y = 700.0

/** The left margin of a test PDF's text, in points. */
private const val LEFT_MARGIN = 72.0

/** The distance between lines of a test PDF's text, in points. */
private const val LINE_SPACING = 20.0

/** The size of a test PDF's text, in points. */
private const val FONT_SIZE = 12f

/**
 * Write a real PDF with one page per text given, each line drawn as text iText
 * reads back; an empty text leaves its page blank, as a scan's or an image-only
 * cover's is (#546). Helvetica: ASCII text only.
 *
 * @param file Where to write it
 * @param pages Each page's text, lines split at `\n`
 * @return [file]
 */
fun writeTitledPdf(file: File, vararg pages: String): File {
    file.parentFile?.mkdirs()
    PdfDocument(PdfWriter(file)).use { pdf ->
        val font = PdfFontFactory.createFont(StandardFonts.HELVETICA)
        for (text in pages) {
            val canvas = PdfCanvas(pdf.addNewPage())
            var y = FIRST_LINE_Y
            for (line in text.split('\n').filter { it.isNotEmpty() }) {
                canvas.beginText().setFontAndSize(font, FONT_SIZE).moveText(LEFT_MARGIN, y).showText(line).endText()
                y -= LINE_SPACING
            }
        }
    }
    return file
}
