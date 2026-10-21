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

import XCTest
import CoreText
import SwiftData
@testable import MedicalFactChecker

/// A full text the reader uploads is kept, read, and redone into the analysis.
///
/// The iOS upload kept a PDF in the caches folder the system may empty, and
/// extracted none of its text, so the analysis went on reading the abstract.
@MainActor
final class FullTextUploadTests: XCTestCase {
    private var container: ModelContainer!
    private var context: ModelContext!
    private var directory: URL!

    override func setUp() async throws {
        StringArrayTransformer.register()
        container = try ModelContainer(
            for: Schema(versionedSchema: SchemaV2.self),
            configurations: ModelConfiguration(isStoredInMemoryOnly: true)
        )
        context = ModelContext(container)
        directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: directory)
        context = nil
        container = nil
    }

    private func makeDocument(score: Int = 4) -> Document {
        let document = Document(pmid: "PPR:123/4", title: "A Trial", abstract: "An abstract.")
        document.relevanceScore = score
        context.insert(document)
        return document
    }

    /// A one-page PDF holding `text`, written where the reader might pick it from.
    private func writePDF(_ text: String) throws -> URL {
        let url = directory.appendingPathComponent("picked.pdf")
        var box = CGRect(x: 0, y: 0, width: 612, height: 792)
        let pdf = try XCTUnwrap(CGContext(url as CFURL, mediaBox: &box, nil))
        pdf.beginPDFPage(nil)
        let line = CTLineCreateWithAttributedString(NSAttributedString(string: text))
        pdf.textPosition = CGPoint(x: 72, y: 700)
        CTLineDraw(line, pdf)
        pdf.endPDFPage()
        pdf.closePDF()
        return url
    }

    private func writeFile(_ name: String, _ contents: String) throws -> URL {
        let url = directory.appendingPathComponent(name)
        try contents.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    func testAnUploadedPDFIsKeptAndItsTextRead() throws {
        let document = makeDocument()
        let picked = try writePDF("Methods and results of the randomised trial.")
        let store = directory.appendingPathComponent("store", isDirectory: true)
        try FileManager.default.createDirectory(at: store, withIntermediateDirectories: true)

        let result = try FullTextUpload.result(forFileAt: picked, pmid: document.pmid, directory: store)
        document.applyFullTextResult(result)

        let kept = store.appendingPathComponent("uploaded-PPR_123_4.pdf")
        XCTAssertEqual(result.localPDFPath, kept.path, "an accession's slash makes no folder")
        XCTAssertTrue(FileManager.default.fileExists(atPath: kept.path))
        XCTAssertTrue(document.holdsUploadedFullText)
        XCTAssertTrue(document.analyzableFullText?.contains("randomised trial") ?? false, "the analysis reads the PDF")
        XCTAssertFalse(FullTextUpload.isOffered(for: document), "a paper with a readable text is offered no upload")
    }

    func testAMarkdownUploadIsReadAndAnUnknownFileRefused() throws {
        let document = makeDocument()
        let picked = try writeFile("paper.md", "# Results\n\nThe trial found a benefit.")

        document.applyFullTextResult(try FullTextUpload.result(forFileAt: picked, pmid: document.pmid))

        XCTAssertTrue(document.analyzableFullText?.contains("found a benefit") ?? false)
        XCTAssertThrowsError(
            try FullTextUpload.result(forFileAt: try writeFile("paper.docx", "x"), pmid: document.pmid)
        )
    }

    /// The reader chose the text for the analysis: read whatever the full-text
    /// setting or its score say. A fetched text keeps the setting's rule.
    func testAnUploadedTextIsReadWhateverTheSetting() throws {
        let document = makeDocument(score: 3)
        let picked = try writeFile("paper.txt", "Full text body.")
        document.applyFullTextResult(try FullTextUpload.result(forFileAt: picked, pmid: document.pmid))

        XCTAssertNotNil(FullTextAutoFetch.citationFullText(
            for: document, autoFetchEnabled: false, minScoreThreshold: 3, autoFetchMinScore: 5
        ))

        document.fullTextSource = AppFullTextSource.europePMC.rawValue
        XCTAssertNil(FullTextAutoFetch.citationFullText(
            for: document, autoFetchEnabled: false, minScoreThreshold: 3, autoFetchMinScore: 5
        ))
    }

    /// Citations taken before the full text arrived came from the abstract, so
    /// a retry redoes them; ones taken after came from the text.
    func testCitationsTakenBeforeTheFullTextArePickedUp() throws {
        let document = makeDocument()
        let early = Citation(passage: "From the abstract.")
        early.extractedAt = Date(timeIntervalSinceNow: -60)
        context.insert(early)
        early.document = document

        let picked = try writeFile("paper.md", "The full text.")
        document.applyFullTextResult(try FullTextUpload.result(forFileAt: picked, pmid: document.pmid))
        XCTAssertTrue(document.citationsPredateFullText)

        context.delete(early)
        try context.save()
        let late = Citation(passage: "From the text.")
        context.insert(late)
        late.document = document
        XCTAssertFalse(document.citationsPredateFullText)
    }
}
