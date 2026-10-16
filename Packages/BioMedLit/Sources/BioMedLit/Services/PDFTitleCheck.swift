// BMLibrarian Lite - Biomedical Literature Research Tool
// Copyright (C) 2024-2026 Dr Horst Herb
// SPDX-License-Identifier: AGPL-3.0-or-later

import Foundation
import PDFKit

/// What checking a PDF against an article's title found (#546).
///
/// A port of Python's `pdf_title_check.py` (#532); the rules and the shared
/// cases are `doc/cross_platform/fulltext_parity/pdf_title_check.json`.
///
/// A fetched PDF can be another article: a publisher's script redirects, a
/// DOI resolves to the wrong file, a supplement is served in the article's
/// place. Its text would then feed this article's transparency rating. The
/// check asks whether the first page with text carries the title: the share
/// of the title's adjacent word pairs (each pair in the title's word order,
/// found anywhere on the page) that the page also has. Single words are not
/// enough: an article on the same subject shares most of them. A title the
/// page carries whole, read with no word breaks on either side, is carried
/// too: PDFKit can space a word apart on a title line.
///
/// A check can also find nothing to judge by — a title with no words, a PDF
/// with no text (a scan), a title PubMed translated from the article's own
/// language, a file that is no readable PDF. Those are cases of their own,
/// never a match or a mismatch: only ``mismatch(share:)`` refuses a PDF.
/// A share exists exactly when text was compared, by construction.
public enum PDFTitleCheck: Sendable, Equatable {
    /// The page read carries the title: at least
    /// ``BioMedLitConstants/pdfTitleMinPairShare`` of its word pairs.
    case matches(share: Double)
    /// The page read has text, and too little of the title.
    case mismatch(share: Double)
    /// The title has no words: there is nothing to check against.
    case noTitle
    /// The PDF has no text on any page (a scan): nothing to check.
    case noText
    /// The title is PubMed's translation of one in another language (in
    /// square brackets) and the page does not carry it: the PDF may well be
    /// in the article's own language, so its title cannot be judged.
    case translatedTitle
    /// The file could not be read as a PDF, is locked, or has no pages.
    case unreadable

    /// The contract's name for this outcome (`outcome` in the fixture).
    public var outcomeName: String {
        switch self {
        case .matches: return "matches"
        case .mismatch: return "mismatch"
        case .noTitle: return "no_title"
        case .noText: return "no_text"
        case .translatedTitle: return "translated_title"
        case .unreadable: return "unreadable"
        }
    }

    /// Every outcome's contract name, in the contract's order.
    public static let outcomeNames = ["matches", "mismatch", "no_title", "no_text", "translated_title", "unreadable"]

    /// The share of the title's word pairs the page carries, from 0 to 1,
    /// when text was compared; `nil` otherwise.
    public var share: Double? {
        switch self {
        case .matches(let share), .mismatch(let share): return share
        case .noTitle, .noText, .translatedTitle, .unreadable: return nil
        }
    }

    /// Whether the PDF is refused as another article's: a mismatch, and
    /// nothing else. A check that judged nothing leaves the PDF in use.
    public var refusesPDF: Bool {
        if case .mismatch = self { return true }
        return false
    }

    /// The check of a title compared with text, judged by its share.
    ///
    /// - Parameter share: The share of the title's word pairs the page carries.
    /// - Returns: ``matches(share:)`` when it reaches the bar, else
    ///   ``mismatch(share:)``.
    public static func compared(share: Double) -> PDFTitleCheck {
        share >= BioMedLitConstants.pdfTitleMinPairShare ? .matches(share: share) : .mismatch(share: share)
    }
}

/// The pure judgement and the PDFKit reading behind ``PDFTitleCheck``.
public enum PDFTitleChecker {
    /// Whitespace as Python's `str.isspace` has it: ICU's `\s` plus the
    /// vertical tab, NEL and the four information separators. Spelt out so
    /// the three platforms' engines read one set.
    private static let whitespace = #"[\s\p{Z}\u000B\u0085\u001C-\u001F]"#
    /// A word character as Python's `\w` has it: a letter, a number, `_`.
    private static let wordCharacter = #"[\p{L}\p{N}_]"#

    /// Markup a stored title may carry (`<i>E. coli</i>`): an element's tag,
    /// never a bare `<` or `>` (`IL-6 < 5 pg/mL`).
    private static let tag = regex(#"</?[A-Za-z][\p{L}\p{N}_:-]*(?:"# + whitespace + #"[^<>]*)?/?>"#)
    /// A word hyphenated over a line break, as a PDF's text layer gives it:
    /// an ASCII hyphen, a Unicode hyphen (U+2010) or a soft hyphen (U+00AD).
    private static let lineBreakHyphen = regex(
        "(" + wordCharacter + #")[-‐­][ \t]*\r?\n"# + whitespace + "*(" + wordCharacter + ")"
    )
    /// A word: a run of letters and numbers. Anything else separates words.
    private static let word = regex(#"[\p{L}\p{N}]+"#)
    /// A title PubMed translated into English, in square brackets: one
    /// bracketed whole, which may hold bracketed parts of its own
    /// (`[[18F]FDG PET in ...]`), with at most a full stop after.
    private static let translatedTitle = regex(
        "^" + whitespace + #"*\[(?:[^\[\]]|\[[^\[\]]*\])*\]\.?"# + whitespace + "*$"
    )

    private static func regex(_ pattern: String) -> NSRegularExpression {
        // The patterns are literals of this file: one that does not compile
        // is a fault no input could cause, caught by every test.
        // swiftlint:disable:next force_try
        try! NSRegularExpression(pattern: pattern)
    }

    private static func replacing(_ expression: NSRegularExpression, in text: String, with template: String) -> String {
        expression.stringByReplacingMatches(
            in: text, range: NSRange(text.startIndex..., in: text), withTemplate: template
        )
    }

    /// The Old Cyrillic variant forms Python's `str.casefold` folds to their
    /// letters and Foundation's case folding keeps.
    private static let cyrillicVariants: [UInt32: UInt32] = [
        0x1C80: 0x0432, 0x1C81: 0x0434, 0x1C82: 0x043E, 0x1C83: 0x0441, 0x1C84: 0x0442,
        0x1C85: 0x0442, 0x1C86: 0x044A, 0x1C87: 0x0463, 0x1C88: 0xA64B,
    ]

    /// Text case-folded as Python's `str.casefold` folds it.
    ///
    /// Foundation's case-insensitive folding is Unicode's full folding but
    /// for two places, measured over every letter and number after NFKC
    /// (#546): Cherokee, which Unicode folds to its capitals and Foundation to
    /// its small letters, and nine Old Cyrillic variant forms. Both are mapped
    /// here, so the folding agrees with Python's on all 137,936.
    ///
    /// - Parameter text: NFKC-normalised text.
    /// - Returns: The folded text.
    static func caseFolded(_ text: String) -> String {
        let folded = text.folding(options: .caseInsensitive, locale: nil)
        var scalars = String.UnicodeScalarView()
        for scalar in folded.unicodeScalars {
            let value = scalar.value
            let mapped: UInt32
            switch value {
            case 0xAB70...0xABBF: mapped = value - 0xAB70 + 0x13A0
            case 0x13F8...0x13FD: mapped = value - 0x13F8 + 0x13F0
            default: mapped = cyrillicVariants[value] ?? value
            }
            scalars.append(Unicode.Scalar(mapped) ?? scalar)
        }
        return String(scalars)
    }

    /// Text as words, compared alike whatever the source's encoding.
    ///
    /// NFKC folds ligatures (`ﬀ` to `ff`) and compatibility forms, a soft
    /// hyphen is dropped, and the text is fully case-folded (`ß` is `ss`, a
    /// final sigma is sigma), as Python's `str.casefold` does.
    ///
    /// - Parameter text: Any text.
    /// - Returns: Its words, in order.
    static func words(_ text: String) -> [String] {
        let folded = caseFolded(
            text.precomposedStringWithCompatibilityMapping.replacingOccurrences(of: "\u{00AD}", with: "")
        )
        let range = NSRange(folded.startIndex..., in: folded)
        return word.matches(in: folded, range: range).compactMap { match in
            Range(match.range, in: folded).map { String(folded[$0]) }
        }
    }

    /// A stored title's words, read with its markup as a word break and as
    /// none: a tag can sit between words or inside one (`H<sub>2</sub>O`).
    ///
    /// - Parameter title: The title as stored.
    /// - Returns: Each reading's words; one reading when no tag sits inside a word.
    static func titleReadings(_ title: String) -> [[String]] {
        let spaced = words(replacing(tag, in: title, with: " "))
        let joined = words(replacing(tag, in: title, with: ""))
        return joined == spaced ? [spaced] : [spaced, joined]
    }

    private static func isTranslated(_ title: String) -> Bool {
        let bare = replacing(tag, in: title, with: "")
        return translatedTitle.firstMatch(in: bare, range: NSRange(bare.startIndex..., in: bare)) != nil
    }

    private struct WordPair: Hashable {
        let first: String
        let second: String
    }

    private static func pairs(_ words: [String]) -> [WordPair] {
        zip(words, words.dropFirst()).map { WordPair(first: $0, second: $1) }
    }

    private static func readingShare(_ titleWords: [String], joined: [String], split: [String]) -> Double {
        if titleWords.count == 1 {
            return Set(joined).union(split).contains(titleWords[0]) ? 1 : 0
        }
        let wanted = pairs(titleWords)
        let have = Set(pairs(joined)).union(pairs(split))
        return Double(wanted.filter(have.contains).count) / Double(wanted.count)
    }

    /// The share of a title's adjacent word pairs that the page text also has.
    ///
    /// The page is read with a word hyphenated at a line break joined
    /// (`hyper-`/`tension`) and split (`SARS-`/`CoV-2`), since the text layer
    /// cannot say which hyphen was the author's; a pair in either counts. A
    /// title with markup inside a word is read both ways too; the better
    /// reading counts. A one-word title counts that word.
    ///
    /// - Parameters:
    ///   - pageText: The text of the page read.
    ///   - title: The article's title, as stored; markup is not counted.
    /// - Returns: From 0 to 1; `nil` when the title has no words.
    public static func titlePairShare(pageText: String, title: String) -> Double? {
        let readings = titleReadings(title).filter { !$0.isEmpty }
        guard !readings.isEmpty else { return nil }
        let joined = words(replacing(lineBreakHyphen, in: pageText, with: "$1$2"))
        let split = words(pageText)
        return readings.map { readingShare($0, joined: joined, split: split) }.max()
    }

    /// Whether the page carries the whole title, word breaks ignored (#546).
    ///
    /// The title's words and the page's are each joined with nothing between
    /// them, so a word the text layer spaced apart (`res ponse`) or words it
    /// ran together (`ClinicalOutcome`) read alike. Only a title of at least
    /// ``BioMedLitConstants/pdfTitleMinJoinedCharacters`` letters and numbers
    /// counts, so a short one cannot match inside a longer word.
    ///
    /// - Parameters:
    ///   - pageText: The text of the page read.
    ///   - title: The article's title, as stored; markup is not counted.
    /// - Returns: Whether the joined title appears in the joined page.
    public static func carriesWholeTitle(pageText: String, title: String) -> Bool {
        let joinedTitle = words(replacing(tag, in: title, with: "")).joined()
        // Counted and searched by code point, as Python's `len` and `in` are
        guard joinedTitle.unicodeScalars.count >= BioMedLitConstants.pdfTitleMinJoinedCharacters else {
            return false
        }
        return words(pageText).joined().range(of: joinedTitle, options: .literal) != nil
    }

    /// Whether the text of a PDF's first page with text carries a title.
    ///
    /// - Parameters:
    ///   - pageText: The text of the page read.
    ///   - title: The article's title.
    /// - Returns: ``PDFTitleCheck/noTitle`` when the title has no words,
    ///   ``PDFTitleCheck/noText`` when the page has none; otherwise the share
    ///   judged against the bar, except that a title the page carries whole,
    ///   word breaks ignored (``carriesWholeTitle(pageText:title:)``), matches
    ///   with a share of 1, and a translated title the page does not carry is
    ///   ``PDFTitleCheck/translatedTitle``, not a mismatch.
    public static func judge(pageText: String, title: String) -> PDFTitleCheck {
        guard let share = titlePairShare(pageText: pageText, title: title) else { return .noTitle }
        guard !words(pageText).isEmpty else { return .noText }
        let check = PDFTitleCheck.compared(share: share)
        guard check.refusesPDF else { return check }
        if carriesWholeTitle(pageText: pageText, title: title) { return .matches(share: 1) }
        if isTranslated(title) { return .translatedTitle }
        return check
    }

    /// The text of a PDF's first pages that carry any words.
    ///
    /// A page with no words before them (an image-only cover, a full-page
    /// figure) is passed over: the title is looked for where the text starts.
    ///
    /// - Parameters:
    ///   - document: The opened, unlocked PDF.
    ///   - pages: How many pages with text to read.
    /// - Returns: Their text, joined; empty when no page has text (a scan).
    static func openingText(of document: PDFDocument, pages: Int = BioMedLitConstants.pdfTitlePagesRead) -> String {
        var texts: [String] = []
        for index in 0..<document.pageCount {
            guard let text = document.page(at: index)?.string, !words(text).isEmpty else { continue }
            texts.append(text)
            if texts.count == pages { break }
        }
        return texts.joined()
    }

    /// Whether an opened PDF's first page with text carries a title.
    ///
    /// - Parameters:
    ///   - document: The PDF, or `nil` when it could not be opened.
    ///   - title: The article's title.
    /// - Returns: The check; ``PDFTitleCheck/unreadable`` for a PDF that could
    ///   not be opened, is locked or has no pages. A title with no words is
    ///   ``PDFTitleCheck/noTitle`` whatever the file.
    public static func check(document: PDFDocument?, title: String) -> PDFTitleCheck {
        guard titleReadings(title).contains(where: { !$0.isEmpty }) else { return .noTitle }
        guard let document, !document.isLocked, document.pageCount > 0 else { return .unreadable }
        return judge(pageText: openingText(of: document), title: title)
    }

    /// Whether a PDF file's first page with text carries a title.
    ///
    /// - Parameters:
    ///   - fileURL: The PDF on disk.
    ///   - title: The article's title.
    /// - Returns: As ``check(document:title:)``.
    public static func check(fileURL: URL, title: String) -> PDFTitleCheck {
        guard titleReadings(title).contains(where: { !$0.isEmpty }) else { return .noTitle }
        return check(document: PDFDocument(url: fileURL), title: title)
    }

    /// Whether a PDF's bytes carry a title on their first page with text.
    ///
    /// - Parameters:
    ///   - data: The PDF's bytes.
    ///   - title: The article's title.
    /// - Returns: As ``check(document:title:)``.
    public static func check(data: Data, title: String) -> PDFTitleCheck {
        guard titleReadings(title).contains(where: { !$0.isEmpty }) else { return .noTitle }
        return check(document: PDFDocument(data: data), title: title)
    }
}
