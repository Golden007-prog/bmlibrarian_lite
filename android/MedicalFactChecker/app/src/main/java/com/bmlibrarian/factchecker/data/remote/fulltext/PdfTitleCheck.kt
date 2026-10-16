package com.bmlibrarian.factchecker.data.remote.fulltext

import android.util.Log
import com.bmlibrarian.factchecker.util.Constants
import com.itextpdf.commons.exceptions.ITextException
import com.itextpdf.kernel.pdf.PdfDocument
import com.itextpdf.kernel.pdf.PdfReader
import com.itextpdf.kernel.pdf.canvas.parser.PdfTextExtractor
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException
import java.text.Normalizer
import java.util.Locale

/**
 * What checking a PDF against an article's title found (#546).
 *
 * A port of Python's `pdf_title_check.py` (#532); the rules and the shared cases
 * are `doc/cross_platform/fulltext_parity/pdf_title_check.json`.
 *
 * A fetched PDF can be another article: a publisher's script redirects, a DOI
 * resolves to the wrong file, a supplement is served in the article's place. It
 * would then be shown, and kept, as this article's full text. The check asks
 * whether the first page with text carries the title: the share of the title's
 * adjacent word pairs (each pair in the title's word order, found anywhere on the
 * page) that the page also has. Single words are not enough: an article on the
 * same subject shares most of them. A title the page carries whole, read with no
 * word breaks on either side, is carried too: iText can run a title line's words
 * together.
 *
 * A check can also find nothing to judge by: a title with no words, a PDF with no
 * text (a scan), a title PubMed translated from the article's own language, a file
 * that is no readable PDF. Those are outcomes of their own, never a match or a
 * mismatch: only [Mismatch] refuses a PDF. A share exists exactly when text was
 * compared, by construction.
 *
 * @property outcomeName The contract's name for this outcome (`outcome` in the fixture)
 */
sealed class PdfTitleCheck(val outcomeName: String) {
    /**
     * The page read carries the title: at least [Constants.PDF_TITLE_MIN_PAIR_SHARE]
     * of its word pairs, or the whole title with word breaks ignored
     * ([PdfTitleChecker.carriesWholeTitle]).
     *
     * @property share The share of the title's word pairs the page carries, 0 to 1;
     *   1 when only the whole title matched
     */
    data class Matches(override val share: Double) : PdfTitleCheck("matches") {
        init {
            require(share in 0.0..1.0) { "a share runs from 0 to 1, not $share" }
        }
    }

    /**
     * The page read has text, and too little of the title.
     *
     * @property share The share of the title's word pairs the page carries, 0 to 1
     */
    data class Mismatch(override val share: Double) : PdfTitleCheck("mismatch") {
        init {
            require(share in 0.0..1.0) { "a share runs from 0 to 1, not $share" }
        }
    }

    /** The title has no words: there is nothing to check against. */
    data object NoTitle : PdfTitleCheck("no_title")

    /** The PDF has no text on any page (a scan): nothing to check. */
    data object NoText : PdfTitleCheck("no_text")

    /**
     * The title is PubMed's translation of one in another language (in square
     * brackets) and the page does not carry it: the PDF may well be in the
     * article's own language, so its title cannot be judged.
     */
    data object TranslatedTitle : PdfTitleCheck("translated_title")

    /** The file could not be read as a PDF, needs a password, or has no pages. */
    data object Unreadable : PdfTitleCheck("unreadable")

    /**
     * The share of the title's word pairs the page carries, when text was
     * compared (1 when only the whole title matched); null otherwise.
     */
    open val share: Double?
        get() = null

    /** Whether the PDF is refused as another article's: a mismatch, and nothing else. */
    val refusesPdf: Boolean
        get() = this is Mismatch

    companion object {
        /** Every outcome's contract name, in the contract's order. */
        val OUTCOME_NAMES = listOf("matches", "mismatch", "no_title", "no_text", "translated_title", "unreadable")

        /**
         * The check of a title compared with text, judged by its share.
         *
         * @param share The share of the title's word pairs the page carries
         * @return [Matches] when it reaches the bar, else [Mismatch]
         */
        fun compared(share: Double): PdfTitleCheck =
            if (share >= Constants.PDF_TITLE_MIN_PAIR_SHARE) Matches(share) else Mismatch(share)
    }
}

/** The pure judgement and the iText reading behind [PdfTitleCheck]. */
object PdfTitleChecker {
    private const val TAG = "PdfTitleChecker"

    /**
     * Whitespace as Python's `str.isspace` has it: Java's `\s`, the separators,
     * NEL and the four information separators. Spelt out so the three
     * platforms' engines read one set.
     */
    private const val WHITESPACE = """[\s\p{Z}\u0085\u001C-\u001F]"""

    /** A word character as Python's `\w` has it: a letter, a number, `_`. */
    private const val WORD_CHARACTER = """[\p{L}\p{N}_]"""

    /**
     * Markup a stored title may carry (`<i>E. coli</i>`): an element's tag, never a
     * bare `<` or `>` (`IL-6 < 5 pg/mL`).
     */
    private val TAG_PATTERN = Regex("""</?[A-Za-z][\p{L}\p{N}_:-]*(?:$WHITESPACE[^<>]*)?/?>""")

    /**
     * A word hyphenated over a line break, as a PDF's text layer gives it: an ASCII
     * hyphen, a Unicode hyphen (U+2010) or a soft hyphen (U+00AD).
     */
    private val LINE_BREAK_HYPHEN = Regex("""($WORD_CHARACTER)[-‐­][ \t]*\r?\n$WHITESPACE*($WORD_CHARACTER)""")

    /** A word: a run of letters and numbers. Anything else separates words. */
    private val WORD = Regex("""[\p{L}\p{N}]+""")

    /**
     * A title PubMed translated into English, in square brackets: one bracketed
     * whole, which may hold bracketed parts of its own (`[[18F]FDG PET in ...]`),
     * with at most a full stop after.
     */
    private val TRANSLATED_TITLE = Regex("""$WHITESPACE*\[(?:[^\[\]]|\[[^\[\]]*\])*\]\.?$WHITESPACE*""")

    /** The soft hyphen: where a word may break, never a separator of words. */
    private const val SOFT_HYPHEN = "­"

    /** The dotless i, which full case folding keeps and an upper-casing would dot. */
    private const val DOTLESS_I = 0x0131

    /** Cherokee's capitals, which full case folding keeps and a lower-casing would not. */
    private val CHEROKEE_CAPITAL = 0x13A0..0x13F5

    /** Cherokee's small letters, which full case folding folds to its capitals. */
    private val CHEROKEE_SMALL = 0xAB70..0xABBF
    private const val CHEROKEE_SMALL_TO_CAPITAL = 0xAB70 - 0x13A0
    private val CHEROKEE_SMALL_LATE = 0x13F8..0x13FD
    private const val CHEROKEE_SMALL_LATE_TO_CAPITAL = 0x13F8 - 0x13F0

    /**
     * Text case-folded as Python's `str.casefold` folds it.
     *
     * The JDK has no full case folding. Upper-casing then lower-casing each code
     * point gives it (an iota subscript expands, a final sigma folds; one code
     * point at a time, so no sigma is given its final form) but for
     * three places, measured over every letter and number after NFKC (#546): the
     * dotless i, which it would dot; the capital sharp s, which it leaves sharp;
     * and Cherokee, which Unicode folds to its capitals. Each is mapped here, so
     * the folding agrees with Python's on all 137,936.
     *
     * @param text NFKC-normalised text
     * @return The folded text
     */
    internal fun caseFolded(text: String): String {
        val folded = StringBuilder(text.length)
        var index = 0
        while (index < text.length) {
            val codePoint = text.codePointAt(index)
            index += Character.charCount(codePoint)
            when (codePoint) {
                DOTLESS_I, in CHEROKEE_CAPITAL -> folded.appendCodePoint(codePoint)
                in CHEROKEE_SMALL -> folded.appendCodePoint(codePoint - CHEROKEE_SMALL_TO_CAPITAL)
                in CHEROKEE_SMALL_LATE -> folded.appendCodePoint(codePoint - CHEROKEE_SMALL_LATE_TO_CAPITAL)
                else -> {
                    val single = String(Character.toChars(codePoint))
                    folded.append(single.uppercase(Locale.ROOT).lowercase(Locale.ROOT))
                }
            }
        }
        // The capital sharp s upper-cases to itself and lower-cases to the sharp
        // s, which folds further
        return folded.toString().replace("ß", "ss")
    }

    /**
     * Text as words, compared alike whatever the source's encoding.
     *
     * NFKC folds ligatures (`ﬀ` to `ff`) and compatibility forms, a soft hyphen is
     * dropped, and the text is fully case-folded (`ß` is `ss`, a final sigma is
     * sigma), as Python's `str.casefold` does.
     *
     * @param text Any text
     * @return Its words, in order
     */
    internal fun words(text: String): List<String> {
        val normalised = Normalizer.normalize(text, Normalizer.Form.NFKC).replace(SOFT_HYPHEN, "")
        return WORD.findAll(caseFolded(normalised)).map { it.value }.toList()
    }

    /**
     * A stored title's words, read with its markup as a word break and as none: a
     * tag can sit between words or inside one (`H<sub>2</sub>O`).
     *
     * @param title The title as stored
     * @return Each reading's words; one reading when no tag sits inside a word
     */
    internal fun titleReadings(title: String): List<List<String>> {
        val spaced = words(TAG_PATTERN.replace(title, " "))
        val joined = words(TAG_PATTERN.replace(title, ""))
        return if (joined == spaced) listOf(spaced) else listOf(spaced, joined)
    }

    private fun isTranslated(title: String): Boolean = TRANSLATED_TITLE.matches(TAG_PATTERN.replace(title, ""))

    private fun pairs(words: List<String>): List<Pair<String, String>> = words.zipWithNext()

    private fun readingShare(titleWords: List<String>, joined: List<String>, split: List<String>): Double {
        if (titleWords.size == 1) {
            return if (titleWords[0] in joined || titleWords[0] in split) 1.0 else 0.0
        }
        val wanted = pairs(titleWords)
        val have = pairs(joined).toSet() + pairs(split)
        return wanted.count { it in have }.toDouble() / wanted.size
    }

    /**
     * The share of a title's adjacent word pairs that the page text also has.
     *
     * The page is read with a word hyphenated at a line break joined
     * (`hyper-`/`tension`) and split (`SARS-`/`CoV-2`), since the text layer cannot
     * say which hyphen was the author's; a pair in either counts. A title with
     * markup inside a word is read both ways too; the better reading counts. A
     * one-word title counts that word.
     *
     * @param pageText The text of the page read
     * @param title The article's title, as stored; markup is not counted
     * @return From 0 to 1; null when the title has no words
     */
    fun titlePairShare(pageText: String, title: String): Double? {
        val readings = titleReadings(title).filter { it.isNotEmpty() }
        if (readings.isEmpty()) return null
        val joined = words(LINE_BREAK_HYPHEN.replace(pageText, "$1$2"))
        val split = words(pageText)
        return readings.maxOf { readingShare(it, joined, split) }
    }

    /**
     * Whether the page carries the whole title, word breaks ignored (#546).
     *
     * The title's words and the page's are each joined with nothing between them,
     * so a word the text layer spaced apart (`res ponse`) or words it ran together
     * (`ClinicalOutcome`) read alike. Only a title of at least
     * [Constants.PDF_TITLE_MIN_JOINED_CHARACTERS] letters and numbers counts, so a
     * short one cannot match inside a longer word.
     *
     * @param pageText The text of the page read
     * @param title The article's title, as stored; markup is not counted
     * @return Whether the joined title appears in the joined page
     */
    fun carriesWholeTitle(pageText: String, title: String): Boolean {
        val joinedTitle = words(TAG_PATTERN.replace(title, "")).joinToString("")
        // Counted by code point, as Python's `len` is: a letter beyond the BMP is one
        if (joinedTitle.codePointCount(0, joinedTitle.length) < Constants.PDF_TITLE_MIN_JOINED_CHARACTERS) return false
        return joinedTitle in words(pageText).joinToString("")
    }

    /**
     * Whether the text of a PDF's first page with text carries a title.
     *
     * @param pageText The text of the page read
     * @param title The article's title
     * @return [PdfTitleCheck.NoTitle] when the title has no words,
     *   [PdfTitleCheck.NoText] when the page has none; otherwise the share judged
     *   against the bar, except that a title the page carries whole, word breaks
     *   ignored ([carriesWholeTitle]), matches with a share of 1, and a translated
     *   title the page does not carry is [PdfTitleCheck.TranslatedTitle], not a
     *   mismatch
     */
    fun judge(pageText: String, title: String): PdfTitleCheck {
        val share = titlePairShare(pageText, title) ?: return PdfTitleCheck.NoTitle
        if (words(pageText).isEmpty()) return PdfTitleCheck.NoText
        val check = PdfTitleCheck.compared(share)
        return when {
            !check.refusesPdf -> check
            carriesWholeTitle(pageText, title) -> PdfTitleCheck.Matches(1.0)
            isTranslated(title) -> PdfTitleCheck.TranslatedTitle
            else -> check
        }
    }

    /**
     * The text of a PDF's first pages that carry any words.
     *
     * A page with no words before them (an image-only cover, a full-page figure) is
     * passed over: the title is looked for where the text starts.
     *
     * @param document The opened PDF
     * @param pages How many pages with text to read
     * @return Their text, joined; empty when no page has text (a scan)
     */
    private fun openingText(document: PdfDocument, pages: Int = Constants.PDF_TITLE_PAGES_READ): String {
        val texts = mutableListOf<String>()
        for (number in 1..document.numberOfPages) {
            val text = PdfTextExtractor.getTextFromPage(document.getPage(number))
            if (words(text).isEmpty()) continue
            texts += text
            if (texts.size == pages) break
        }
        return texts.joinToString("")
    }

    /**
     * Whether a PDF file's first page with text carries a title.
     *
     * A title with no words is not checked, so the file is not opened for it.
     *
     * @param file The PDF on disk
     * @param title The article's title
     * @return The check; [PdfTitleCheck.Unreadable] for a file iText cannot read as
     *   a PDF, one that needs a password, or one with no pages (logged)
     */
    fun check(file: File, title: String): PdfTitleCheck {
        if (titleReadings(title).none { it.isNotEmpty() }) return PdfTitleCheck.NoTitle
        val text = try {
            // The reader closed too when the document cannot be built from it
            PdfReader(file).use { reader ->
                PdfDocument(reader).use { document ->
                    if (document.numberOfPages == 0) null else openingText(document)
                }
            }
        } catch (e: IOException) {
            Log.w(TAG, "${file.name} could not be read to check its title", e)
            return PdfTitleCheck.Unreadable
        } catch (e: ITextException) {
            // iText's own: a damaged file, a password (BadPasswordException)
            Log.w(TAG, "${file.name} could not be read to check its title", e)
            return PdfTitleCheck.Unreadable
        } catch (e: StackOverflowError) {
            // A page tree nested past the stack (a damaged or hostile file): the
            // file's, and the app must not die of it
            Log.w(TAG, "${file.name} is nested too deeply to read for its title", e)
            return PdfTitleCheck.Unreadable
        } catch (e: RuntimeException) {
            // iText documents no closed set of errors, and no caller may be left
            // without an answer: one that is not iText's is a fault of the code or
            // the install, logged as one, and the file is still only unread
            Log.e(TAG, "Reading ${file.name} to check its title failed in the code, not the file", e)
            return PdfTitleCheck.Unreadable
        }
        if (text == null) {
            Log.w(TAG, "${file.name} has no pages, so its title could not be checked")
            return PdfTitleCheck.Unreadable
        }
        return judge(text, title)
    }
}

/**
 * Whether a saved PDF is another article's, and so refused: its first page with
 * text does not carry [title] (#546). A refused PDF is set aside
 * ([setAsideWrongArticle]) and never read; a check that judged nothing (no title,
 * no text, a translated title, a file iText cannot read) leaves it in use.
 *
 * Read off the main thread: iText parses the page.
 *
 * @param path The saved PDF
 * @param title The article's title; null checks nothing
 * @return Whether it was refused
 */
internal suspend fun refusedAsAnotherArticle(path: String, title: String?): Boolean {
    val file = File(path)
    // A missing file is never refused (it would read as unreadable), so it is
    // answered here without a thread hop
    if (title == null || !file.isFile) return false
    return withContext(Dispatchers.IO) {
        val check = PdfTitleChecker.check(file, title)
        if (!check.refusesPdf) {
            // Told apart from a match in the log: a PDF kept unjudged was never
            // checked. Unreadable was logged by the check, with its cause
            if (check.share == null && check != PdfTitleCheck.Unreadable) {
                Log.i("PdfTitleChecker", "${file.name} kept unjudged: ${check.outcomeName}")
            }
            return@withContext false
        }
        val percent = Math.round((check.share ?: 0.0) * PERCENT)
        Log.w(
            "PdfTitleChecker",
            "${file.name} is another article's: its first page with text carries $percent% of the " +
                "title's word pairs; set aside unread"
        )
        setAsideWrongArticle(file)
        true
    }
}

/** A share as a percentage, for the log. */
private const val PERCENT = 100
