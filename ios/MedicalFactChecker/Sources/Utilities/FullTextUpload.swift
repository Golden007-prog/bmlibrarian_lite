// BMLibrarian Lite - Biomedical Literature Research Tool
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program. If not, see <https://www.gnu.org/licenses/>.

import Foundation
import UniformTypeIdentifiers
import BioMedLit

/// Why a file the reader chose could not be used as a paper's full text.
enum FullTextUploadError: LocalizedError {
    /// The file format is not supported.
    case unsupportedFormat

    var errorDescription: String? {
        "Unsupported file format. Please use PDF, HTML, or Markdown"
    }
}

/// A full text the reader supplies by hand, for a paper no source had.
///
/// One reader for both platforms. An uploaded PDF is kept with the PDFs the
/// retrieval chain downloads, not in the caches folder the system may empty,
/// and its text is extracted, so citation extraction and transparency
/// analysis read it rather than the abstract.
enum FullTextUpload {
    /// The file types the picker offers.
    static let allowedContentTypes: [UTType] = [
        .pdf, .html, .plainText, UTType(filenameExtension: "md") ?? .plainText,
    ]

    /// Whether to offer an upload for the document: it has no text the analysis can read.
    static func isOffered(for document: Document) -> Bool {
        FullTextAutoFetch.citationText(for: document) == nil
    }

    /// Read a chosen file into a full-text result.
    ///
    /// Slow for a long PDF (its text is extracted), so call it off the main actor.
    ///
    /// - Parameters:
    ///   - url: The file the reader chose, possibly security-scoped.
    ///   - pmid: The document's identifier, naming the stored PDF.
    ///   - directory: Where an uploaded PDF is kept.
    /// - Returns: The result to apply to the document.
    /// - Throws: ``FullTextUploadError/unsupportedFormat``, or the file system's error.
    static func result(
        forFileAt url: URL,
        pmid: String,
        directory: URL = BMLFullTextService.pdfCacheDirectory
    ) throws -> AppFullTextResult {
        // A picked file is security-scoped; a refused one fails at the read below
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }

        switch url.pathExtension.lowercased() {
        case "pdf":
            // An accession may hold characters a file name cannot
            let name = String(pmid.map { $0.isLetter || $0.isNumber ? $0 : "_" })
            let destination = directory.appendingPathComponent("uploaded-\(name).pdf")
            if FileManager.default.fileExists(atPath: destination.path) {
                try FileManager.default.removeItem(at: destination)
            }
            try FileManager.default.copyItem(at: url, to: destination)

            let extraction = PDFKitTextExtractor().extract(from: destination)
            let text = extraction.success && extraction.charCount > 0 ? extraction.text : nil
            return AppFullTextResult(
                content: .pdfURL(destination),
                source: .uploaded,
                contentKind: text == nil ? .none : .extracted,
                extractedText: text,
                localPDFPath: destination.path,
                extractionCoverage: extraction.pageCount > 0 ? extraction.coverage : nil
            )
        case "html", "htm":
            // shortcut: the HTML stands in for its markdown too; convert it if analysis reads markup badly
            let html = try String(contentsOf: url, encoding: .utf8)
            return .uploaded(content: .html(content: html, markdown: html))
        case "md", "markdown", "txt":
            return .uploaded(content: .markdown(try String(contentsOf: url, encoding: .utf8)))
        default:
            throw FullTextUploadError.unsupportedFormat
        }
    }
}
