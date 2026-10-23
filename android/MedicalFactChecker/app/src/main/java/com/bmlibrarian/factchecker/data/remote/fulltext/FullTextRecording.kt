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

import com.bmlibrarian.factchecker.data.local.entity.DocumentEntity
import com.bmlibrarian.factchecker.data.remote.fulltext.FullTextService.FullTextResult
import com.bmlibrarian.factchecker.domain.model.OpenAccessShortfall
import com.bmlibrarian.factchecker.domain.model.OpenAccessSource
import com.bmlibrarian.factchecker.domain.model.OpenAccessUnsettledReason
import com.bmlibrarian.factchecker.util.Constants
import java.util.Date

/**
 * What a full-text fetch comes to once its PDF was downloaded, or was not.
 *
 * @property document The document to store
 * @property result The chain's answer as the reader is to be shown it: the
 *   chain's own, except the open-access PDFs to try, which become the one
 *   obtained ([FullTextResult.OpenAccessPdf]) or, when none was, the DOI link
 *   carrying every shortfall met
 */
data class RecordedFetch(val document: DocumentEntity, val result: FullTextResult)

/**
 * The document as a full-text fetch leaves it: what the chain found, recorded on
 * the row.
 *
 * One writer for the fact-check, report and full-text screens, which each kept
 * their own copy of this mapping. Every branch writes the open-access shortfall
 * (#466) and the caching note (#480): the DOI fallback stores why the
 * open-access copy went unassessed, a PDF served but not saved stores where it
 * was found, and every other answer clears what an earlier fetch stored, so the
 * cards never say a free copy may exist after a fetch settled it.
 *
 * The PDFs are downloaded here. The open-access PDFs are walked in chain order
 * ([obtainingOpenAccessPdf]): a PDF a source named that it did not serve is
 * refused, not recorded as found (#478), and once every Unpaywall PDF was
 * refused OpenAlex is asked for the ones it names ([askOpenAlex]); once every
 * candidate, OpenAlex's included, was refused, CORE is asked for its extracted
 * text ([askCore]), last before the DOI link. A Europe PMC
 * PDF that could not be downloaded stays a link-only record of its own (#471);
 * one served and not saved also gets the caching note. Elsevier's PDF arrives
 * already saved, and is recorded as the local file it is (#480, stage C2).
 *
 * Every PDF saved is read for its text ([readPdfText]), stored beside it for the
 * analysis. When the chain held back an abstract-only deposit
 * ([FullTextResult.EuropePmcPdf.heldAbstract]), a PDF without text does not beat
 * it. Among Unpaywall's and OpenAlex's copies the walk goes on for one with
 * text, and the abstract is recorded when none arrives. Europe PMC's render and
 * Elsevier's PDF end the chain (#493, #615), so a textless one gives way to the
 * abstract directly, as does a render that was another article's, could not be
 * downloaded (noted as a shortfall) or was not saved.
 *
 * Every PDF downloaded here is checked against the document's title by the
 * download itself, before it is cached (#546, #561): one whose first page with
 * text does not carry it is another article's, set aside unread
 * ([PdfDownload.WrongArticle]); an open-access candidate's is recorded as a
 * wrong-article skip with its address and the walk goes on, a render's ends the
 * fetch on the DOI link, or with none on no link ([renderOfAnotherArticle]).
 *
 * @param result What the chain returned
 * @param downloadPdf Downloads a PDF URL, always asking it (never a cached file,
 *   which could be another address's, #561), and checks it against the title
 * @param askOpenAlex Asks OpenAlex for its steps, given the DOI and the
 *   addresses tried or refused; called at most once, and only when no
 *   Unpaywall copy was served (#480). Required: a caller that left it out
 *   would never ask OpenAlex
 * @param askCore Asks CORE for its extracted text, given the DOI; called at most
 *   once, only when every open-access candidate failed and none was served
 *   (#480, stage C). Null is no key: nothing asked, nothing recorded. Required,
 *   with no default, for the same reason as [askOpenAlex]
 * @param readPdfText Reads a saved PDF's text; null when it has none or cannot be read
 * @return The document to store, and the result to show; the document is
 *   unchanged for [FullTextResult.NotEstablished] and
 *   [FullTextResult.OpenAccessNotEstablished], which are not facts about the
 *   article and leave the fetch on offer (#434, #546)
 */
suspend fun DocumentEntity.recordingFullTextFetch(
    result: FullTextResult,
    downloadPdf: suspend (String) -> PdfDownload,
    askOpenAlex: suspend (doi: String, tried: List<String>) -> List<OpenAccessStep>,
    askCore: suspend (doi: String) -> CoreFetch?,
    readPdfText: suspend (path: String) -> String? = ::extractedPdfText
): RecordedFetch = when (result) {
    is FullTextResult.OpenAccessPdfs -> obtainingOpenAccessPdf(result, downloadPdf, askOpenAlex, askCore, readPdfText)
    is FullTextResult.EuropePmcPdf -> when (val download = downloadPdf(result.pdfUrl)) {
        PdfDownload.NotSaved -> result.heldAbstract?.let { recordingHeld(it.notingHeldAbstract(null, result.pdfUrl)) }
            ?: RecordedFetch(recording(result, pdfPath = null).copy(fullTextPdfNotSavedFrom = result.pdfUrl), result)
        is PdfDownload.Saved -> recordingPdf(result, download.path, result.heldAbstract, readPdfText)
        PdfDownload.WrongArticle -> renderOfAnotherArticle(result)
        // An unreachable render is no answer: the held abstract carries it, so the
        // question stays open rather than settling on the abstract
        is PdfDownload.Failed -> result.heldAbstract?.let {
            val unreachable = OpenAccessShortfall(OpenAccessSource.EUROPE_PMC_PDF, download.failure, result.pdfUrl)
            recordingHeld(it.notingHeldAbstract(unreachable, pdfNotSavedFrom = null))
        } ?: RecordedFetch(recording(result, pdfPath = null), result)
    }
    // Already saved by the chain: a local file, never a URL to download (#480, stage C2).
    // shortcut: one without text gives way to a held abstract here, where Swift walks on to Unpaywall
    // for a copy with text (#615); walk on too if textless Elsevier PDFs turn up in practice
    is FullTextResult.ElsevierPdf -> recordingPdf(result, result.pdfPath, result.heldAbstract, readPdfText)
    else -> RecordedFetch(recording(result, pdfPath = null), result)
}

/**
 * A saved PDF recorded with its text, unless it has none and an abstract was held back.
 *
 * @param result The PDF result
 * @param path Where the PDF is saved
 * @param heldAbstract The abstract-only deposit the chain held back, or null
 * @param readPdfText Reads the PDF's text
 * @return The PDF with its text; or the held abstract when the PDF has no text.
 *   The copy was served either way, so nothing about open access is left unsettled
 */
private suspend fun DocumentEntity.recordingPdf(
    result: FullTextResult,
    path: String,
    heldAbstract: FullTextResult?,
    readPdfText: suspend (String) -> String?
): RecordedFetch {
    val text = readPdfText(path)
    if (text == null && heldAbstract != null) return recordingHeld(heldAbstract)
    return RecordedFetch(recording(result, path).copy(pdfExtractedText = text), result)
}

/** The abstract-only deposit the chain held back, recorded as what the fetch came to. */
private fun DocumentEntity.recordingHeld(heldAbstract: FullTextResult): RecordedFetch =
    RecordedFetch(recording(heldAbstract, pdfPath = null), heldAbstract)

/**
 * Refuse a held abstract that is not a JATS deposit: only Europe PMC's or the
 * bucket's can carry what the chain met ([notingHeldAbstract]), and any other
 * would lose it silently.
 *
 * @param heldAbstract The deposit held back, or null
 * @throws IllegalArgumentException for any other result
 */
internal fun requireJatsDeposit(heldAbstract: FullTextResult?) {
    require(heldAbstract == null || heldAbstract is FullTextResult.EuropePmcXml || heldAbstract is FullTextResult.PmcOpenDataXml) {
        "only a JATS deposit is held back as an abstract, not $heldAbstract"
    }
}

/**
 * A held abstract-only deposit carrying what the chain met after holding it back.
 *
 * @param shortfall What the open-access walk left unsettled, or null
 * @param pdfNotSavedFrom A PDF served that could not be saved here, or null
 * @return The deposit with both noted; any other result unchanged, which
 *   [requireJatsDeposit] keeps from being held
 */
internal fun FullTextResult.notingHeldAbstract(shortfall: OpenAccessShortfall?, pdfNotSavedFrom: String?): FullTextResult =
    when (this) {
        is FullTextResult.EuropePmcXml -> copy(openAccessShortfall = shortfall, pdfNotSavedFrom = pdfNotSavedFrom)
        is FullTextResult.PmcOpenDataXml -> copy(openAccessShortfall = shortfall, pdfNotSavedFrom = pdfNotSavedFrom)
        else -> this
    }

/**
 * What the walk left unsettled, once it is known whether a copy was served.
 *
 * A copy served, whether not saved or saved without text, settles the
 * open-access question, so nothing is told then but CORE's entry: a copy
 * without text is no full text obtained, so a CORE left unreachable keeps it
 * open (#499), as Swift's `settledOpenAccessShortfall` does.
 */
private fun settledShortfall(shortfall: OpenAccessShortfall?, copyServed: Boolean): OpenAccessShortfall? {
    if (!copyServed || shortfall == null) return shortfall
    return shortfall.entries.filter { it.source == OpenAccessSource.CORE }.ifEmpty { null }?.let(::OpenAccessShortfall)
}

/**
 * A Europe PMC render that is another article's PDF, set aside (#546).
 *
 * The chain returned the render before the later tiers ran, so this fetch ends on
 * it, as a render that could not be downloaded does (#471), but never on the
 * render's link, which names that other article: on the DOI link with the
 * wrong-article shortfall stored and told, or, with no DOI, on
 * [FullTextResult.OpenAccessNotEstablished], which records nothing and tells the
 * shortfall, as BioMedLit's chain ends. Either way the absence stays open.
 *
 * @param result The render the chain returned
 * @return The document to store, and the result to show
 */
private fun DocumentEntity.renderOfAnotherArticle(result: FullTextResult.EuropePmcPdf): RecordedFetch {
    val shortfall = OpenAccessShortfall.wrongArticle(OpenAccessSource.EUROPE_PMC_PDF, result.pdfUrl)
    result.heldAbstract?.let { return recordingHeld(it.notingHeldAbstract(shortfall, pdfNotSavedFrom = null)) }
    val usableDoi = doi?.trim()?.ifEmpty { null }
        ?: return RecordedFetch(this, FullTextResult.OpenAccessNotEstablished(shortfall))
    val link = FullTextResult.DoiUrl(doiLink(usableDoi), shortfall)
    return RecordedFetch(recording(link, pdfPath = null), link)
}

/**
 * Walk the open-access steps in chain order (#480, stage B).
 *
 * The first candidate served ends the walk: saved, it is the result; served but
 * not saved, its link is kept with a caching note, and nothing further is asked
 * (saving is our problem, not the source's), and a served copy settles the
 * open-access question, so no shortfall is recorded. When no step was served
 * and the chain had not yet asked OpenAlex, it is asked, once, with every
 * address tried, and its steps are walked the same way (the maintainer's
 * decision of 2026-10-05: no OpenAlex request that cannot raise the odds).
 * When still nothing was served, CORE is asked, once, for its extracted text
 * (#480, stage C): served, it is the result, recorded as plain text; unreachable,
 * it is the last shortfall; a refused key adds the key_refused skip, which keeps
 * the absence open (#498); absent, or without a key, it adds nothing.
 * Otherwise the DOI link carries every shortfall met, in chain order: an
 * unsettled lookup, or a candidate refused under its namer's source with its
 * address (#478's rule), or another article's PDF with its address (#546).
 *
 * @param result The steps the chain found
 * @param downloadPdf Downloads a PDF URL, checked against the title
 * @param askOpenAlex Asks OpenAlex for its steps, given the DOI and the
 *   addresses tried or refused, in chain order
 * @param askCore Asks CORE for its extracted text, given the DOI
 * @return The document to store, and the result to show
 */
private suspend fun DocumentEntity.obtainingOpenAccessPdf(
    result: FullTextResult.OpenAccessPdfs,
    downloadPdf: suspend (String) -> PdfDownload,
    askOpenAlex: suspend (String, List<String>) -> List<OpenAccessStep>,
    askCore: suspend (String) -> CoreFetch?,
    readPdfText: suspend (String) -> String?
): RecordedFetch {
    var shortfall: OpenAccessShortfall? = null
    val tried = mutableListOf<String>()
    val held = result.heldAbstract
    // A copy saved without text while an abstract is held: it settles the open-access
    // question, and the walk goes on for a copy with text
    var copyServed = false

    /** Walk [steps]; the recorded fetch when a copy was served, else null. */
    suspend fun walk(steps: List<OpenAccessStep>): RecordedFetch? {
        for (step in steps) {
            when (step) {
                is OpenAccessStep.Unsettled -> {
                    tried += step.addresses
                    shortfall = OpenAccessShortfall.adding(step.shortfall, shortfall)
                }
                is OpenAccessStep.Candidate -> {
                    tried += step.addresses
                    val found = FullTextResult.OpenAccessPdf(step.pdfUrl, result.doi, step.namedBy)
                    when (val download = downloadPdf(step.pdfUrl)) {
                        is PdfDownload.Saved -> {
                            val text = readPdfText(download.path)
                            if (text != null || held == null) {
                                return RecordedFetch(recording(found, download.path).copy(pdfExtractedText = text), found)
                            }
                            copyServed = true
                        }
                        // Another article's, set aside by the download (#546, #561): it
                        // settles nothing about this one, so the walk goes on, as for a
                        // copy refused
                        PdfDownload.WrongArticle -> shortfall = OpenAccessShortfall.adding(
                            OpenAccessShortfall.wrongArticle(step.namedBy.refusedAs, step.pdfUrl), shortfall
                        )
                        PdfDownload.NotSaved -> {
                            // Ends the walk, as with no abstract held: saving is our problem
                            if (held != null) {
                                return recordingHeld(held.notingHeldAbstract(settledShortfall(shortfall, true), step.pdfUrl))
                            }
                            val linked = found.copy(notSaved = true)
                            return RecordedFetch(recording(linked, pdfPath = null), linked)
                        }
                        is PdfDownload.Failed -> shortfall = OpenAccessShortfall.adding(
                            OpenAccessShortfall(step.namedBy.refusedAs, download.failure, step.pdfUrl), shortfall
                        )
                    }
                }
            }
        }
        return null
    }

    walk(result.steps)?.let { return it }
    if (!result.openAlexAsked) {
        walk(askOpenAlex(result.doi, tried.toList()))?.let { return it }
    }
    // CORE's extracted text (#480, stage C): every candidate failed, so it can
    // raise the odds. Reached only when no copy was served: a Saved or NotSaved
    // download has already returned above
    when (val fetched = askCore(result.doi)) {
        is CoreFetch.Served -> {
            val text = FullTextResult.CoreText(fetched.text)
            return RecordedFetch(recording(text, pdfPath = null), text)
        }
        is CoreFetch.Unreachable -> shortfall = OpenAccessShortfall.adding(
            OpenAccessShortfall(OpenAccessSource.CORE, OpenAccessUnsettledReason.Failed(fetched.failure)),
            shortfall
        )
        // A skip, not a failure: told as the key, and it keeps the absence unsettled (#498)
        CoreFetch.KeyRefused -> shortfall = OpenAccessShortfall.adding(OpenAccessShortfall.CORE_KEY_REFUSED, shortfall)
        CoreFetch.Absent, null -> Unit
    }
    held?.let { return recordingHeld(it.notingHeldAbstract(settledShortfall(shortfall, copyServed), pdfNotSavedFrom = null)) }
    val refused = FullTextResult.DoiUrl(doiLink(result.doi), shortfall)
    return RecordedFetch(recording(refused, pdfPath = null), refused)
}

/**
 * The document as an answer leaves it, its PDF already downloaded or not.
 *
 * Every answer clears the caching note an earlier fetch left, except an
 * open-access PDF served and not saved, which writes its own.
 *
 * An answer that brings no text (a PDF, the DOI link, unavailable) clears the
 * text an earlier fetch stored (#495), so no text is ever shown under another
 * answer's source: CORE's text, untrusted, is kept out of the JavaScript-enabled
 * WebView by its source label alone.
 *
 * @param result What the chain came to
 * @param pdfPath Where its PDF was saved; null when nothing was
 * @return The document to store
 */
private fun DocumentEntity.recording(result: FullTextResult, pdfPath: String?): DocumentEntity = when (result) {
    is FullTextResult.EuropePmcXml -> copy(
        fullTextMarkdown = result.markdown,
        fullTextHTML = result.html,
        fullTextAbstractOnly = result.abstractOnly,
        fullTextSource = Constants.FULLTEXT_SOURCE_EUROPE_PMC,
        fullTextFetchedAt = Date(),
        fullTextOpenAccessShortfallJson = result.openAccessShortfall?.toJson(),
        fullTextPdfNotSavedFrom = result.pdfNotSavedFrom
    )
    is FullTextResult.PmcOpenDataXml -> copy(
        fullTextMarkdown = result.markdown,
        fullTextHTML = result.html,
        fullTextAbstractOnly = result.abstractOnly,
        fullTextSource = Constants.FULLTEXT_SOURCE_PMC_OPEN_DATA,
        fullTextFetchedAt = Date(),
        fullTextOpenAccessShortfallJson = result.openAccessShortfall?.toJson(),
        fullTextPdfNotSavedFrom = result.pdfNotSavedFrom
    )
    // Plain text, untrusted: no HTML is stored, so nothing renders it as HTML
    is FullTextResult.CoreText -> copy(
        fullTextMarkdown = result.text,
        fullTextHTML = null,
        fullTextAbstractOnly = false,
        fullTextSource = Constants.FULLTEXT_SOURCE_CORE,
        fullTextFetchedAt = Date(),
        fullTextOpenAccessShortfallJson = null,
        fullTextPdfNotSavedFrom = null
    )
    is FullTextResult.EuropePmcPdf -> copy(
        pdfPath = pdfPath,
        pdfExtractedText = null,
        fullTextMarkdown = null,
        fullTextHTML = null,
        fullTextAbstractOnly = false,
        fullTextSource = Constants.FULLTEXT_SOURCE_EUROPE_PMC,
        fullTextFetchedAt = Date(),
        fullTextOpenAccessShortfallJson = null,
        fullTextPdfNotSavedFrom = null
    )
    // A local file alone: Elsevier's article URL needs the key, so no column ever
    // holds it, and a served PDF settles the open-access question (#480, stage C2)
    is FullTextResult.ElsevierPdf -> copy(
        pdfPath = pdfPath,
        pdfExtractedText = null,
        fullTextMarkdown = null,
        fullTextHTML = null,
        fullTextAbstractOnly = false,
        fullTextSource = Constants.FULLTEXT_SOURCE_ELSEVIER,
        fullTextFetchedAt = Date(),
        fullTextOpenAccessShortfallJson = null,
        fullTextPdfNotSavedFrom = null
    )
    is FullTextResult.OpenAccessPdfs -> error("resolved by obtainingOpenAccessPdf before recording")
    is FullTextResult.OpenAccessPdf -> copy(
        pdfPath = pdfPath,
        pdfExtractedText = null,
        fullTextMarkdown = null,
        fullTextHTML = null,
        fullTextAbstractOnly = false,
        fullTextSource = result.namedBy.fullTextSource,
        fullTextFetchedAt = Date(),
        fullTextOpenAccessShortfallJson = null,
        fullTextPdfNotSavedFrom = if (result.notSaved) result.pdfUrl else null
    )
    is FullTextResult.DoiUrl -> copy(
        fullTextMarkdown = null,
        fullTextHTML = null,
        fullTextAbstractOnly = false,
        fullTextSource = Constants.FULLTEXT_SOURCE_DOI,
        fullTextFetchedAt = Date(),
        fullTextOpenAccessShortfallJson = result.openAccessShortfall?.toJson(),
        fullTextPdfNotSavedFrom = null
    )
    is FullTextResult.Unavailable -> copy(
        fullTextMarkdown = null,
        fullTextHTML = null,
        fullTextAbstractOnly = false,
        fullTextUnavailable = true,
        fullTextFetchedAt = Date(),
        fullTextOpenAccessShortfallJson = null,
        fullTextPdfNotSavedFrom = null
    )
    is FullTextResult.NotEstablished, is FullTextResult.OpenAccessNotEstablished -> this
}
