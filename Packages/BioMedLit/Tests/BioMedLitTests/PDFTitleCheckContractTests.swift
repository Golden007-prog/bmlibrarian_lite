// BMLibrarian Lite - Biomedical Literature Research Tool
// Copyright (C) 2024-2026 Dr Horst Herb
// SPDX-License-Identifier: AGPL-3.0-or-later

import CoreGraphics
import CoreText
import PDFKit
import XCTest
@testable import BioMedLit

/// Whether a PDF is the article it was fetched for, judged by its title (#546):
/// the shared rows of `doc/cross_platform/fulltext_parity/pdf_title_check.json`,
/// then the reading of real PDFs.
final class PDFTitleCheckContractTests: XCTestCase {
    private static let title = "Caffeine and blood pressure: a critical review perspective."

    private static let contractFile: URL? = {
        let relative = "doc/cross_platform/fulltext_parity/pdf_title_check.json"
        var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        while directory.path != "/" {
            let candidate = directory.appendingPathComponent(relative)
            if FileManager.default.fileExists(atPath: candidate.path) { return candidate }
            if FileManager.default.fileExists(atPath: directory.appendingPathComponent(".git").path) { return nil }
            directory = directory.deletingLastPathComponent()
        }
        return nil
    }()

    private func contract() throws -> [String: Any] {
        let file = try XCTUnwrap(Self.contractFile, "pdf_title_check.json not found")
        let object = try JSONSerialization.jsonObject(with: Data(contentsOf: file))
        return try XCTUnwrap(object as? [String: Any])
    }

    // MARK: - The shared contract

    func testTheBoundsAreTheContracts() throws {
        let contract = try contract()
        XCTAssertEqual(contract["min_pair_share"] as? Double, BioMedLitConstants.pdfTitleMinPairShare)
        XCTAssertEqual(contract["pages_read"] as? Int, BioMedLitConstants.pdfTitlePagesRead)
        XCTAssertEqual(contract["outcomes"] as? [String], PDFTitleCheck.outcomeNames)
    }

    func testEachCase() throws {
        let cases = try XCTUnwrap(contract()["cases"] as? [[String: Any]])
        XCTAssertFalse(cases.isEmpty)
        for row in cases {
            let name = row["name"] as? String ?? ""
            let page = try XCTUnwrap(row["page"] as? String, name)
            let title = try XCTUnwrap(row["title"] as? String, name)
            let check = PDFTitleChecker.judge(pageText: page, title: title)
            XCTAssertEqual(check.outcomeName, row["outcome"] as? String, name)
            if let share = row["share"] as? Double {
                XCTAssertEqual(try XCTUnwrap(check.share, name), share, accuracy: 1e-6, name)
            } else {
                XCTAssertNil(check.share, name)
            }
        }
    }

    // MARK: - The judgement's one bar

    func testAComparedCheckIsJudgedByTheBar() {
        XCTAssertEqual(PDFTitleCheck.compared(share: 0.5), .matches(share: 0.5))
        XCTAssertEqual(PDFTitleCheck.compared(share: 0.49), .mismatch(share: 0.49))
        XCTAssertEqual(PDFTitleCheck.compared(share: 0), .mismatch(share: 0))
    }

    func testOnlyAMismatchRefusesAPDF() {
        XCTAssertTrue(PDFTitleCheck.mismatch(share: 0.2).refusesPDF)
        for check in [PDFTitleCheck.matches(share: 1), .noTitle, .noText, .translatedTitle, .unreadable] {
            XCTAssertFalse(check.refusesPDF, "\(check)")
        }
    }

    // MARK: - Reading a real PDF

    /// A real PDF with one page per text given; an empty text leaves a page blank.
    private func pdf(_ pages: String...) throws -> Data {
        let data = NSMutableData()
        let consumer = try XCTUnwrap(CGDataConsumer(data: data as CFMutableData))
        var box = CGRect(x: 0, y: 0, width: 612, height: 792)
        let context = try XCTUnwrap(CGContext(consumer: consumer, mediaBox: &box, nil))
        for text in pages {
            context.beginPage(mediaBox: &box)
            var y: CGFloat = 700
            for line in text.split(separator: "\n") where !text.isEmpty {
                let font = CTFontCreateWithName("Helvetica" as CFString, 12, nil)
                let attributed = NSAttributedString(
                    string: String(line), attributes: [kCTFontAttributeName as NSAttributedString.Key: font]
                )
                context.textPosition = CGPoint(x: 72, y: y)
                CTLineDraw(CTLineCreateWithAttributedString(attributed), context)
                y -= 20
            }
            context.endPage()
        }
        context.closePDF()
        return data as Data
    }

    func testAPDFCarryingTheTitleMatches() throws {
        let data = try pdf("Caffeine and blood pressure: a critical review perspective\nAbstract")
        XCTAssertEqual(PDFTitleChecker.check(data: data, title: Self.title), .matches(share: 1))
    }

    func testAnotherArticlesPDFDoesNotMatch() throws {
        let data = try pdf("Using Effect Size - or Why the P Value Is Not Enough\nGail M. Sullivan, Richard Feinn")
        let check = PDFTitleChecker.check(
            data: data, title: "Acute Effects of Coffee Consumption on Blood Pressure and Endothelial Function"
        )
        XCTAssertTrue(check.refusesPDF, "\(check)")
    }

    /// A title further in (a cited article, an issue's contents) is not this PDF's.
    func testOnlyTheFirstPageWithTextIsRead() throws {
        let data = try pdf("Supplementary table S1", Self.title)
        XCTAssertTrue(PDFTitleChecker.check(data: data, title: Self.title).refusesPDF)
    }

    /// An image-only cover is passed over: the title is looked for where the text starts.
    func testAFirstPageWithNoTextIsPassedOver() throws {
        XCTAssertEqual(PDFTitleChecker.check(data: try pdf("", Self.title, "Body"), title: Self.title), .matches(share: 1))
        XCTAssertTrue(PDFTitleChecker.check(data: try pdf("", "Supplementary methods S1 to S9"), title: Self.title).refusesPDF)
    }

    func testAScanIsNotChecked() throws {
        XCTAssertEqual(PDFTitleChecker.check(data: try pdf("", ""), title: Self.title), .noText)
    }

    func testAFileThatIsNoPDFIsUnreadable() {
        let truncated = Data("%PDF-1.7\n".utf8) + Data(repeating: 0x30, count: 64)
        XCTAssertEqual(PDFTitleChecker.check(data: truncated, title: Self.title), .unreadable)
        XCTAssertEqual(PDFTitleChecker.check(data: Data(), title: Self.title), .unreadable)
        XCTAssertEqual(
            PDFTitleChecker.check(fileURL: URL(fileURLWithPath: "/nonexistent/gone.pdf"), title: Self.title),
            .unreadable
        )
    }

    /// A locked file hands back no text, which must not read as a scan.
    func testALockedPDFIsUnreadable() throws {
        let encrypted = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("Fixtures/PDF/encrypted.pdf")
        let document = try XCTUnwrap(PDFDocument(url: encrypted))
        XCTAssertTrue(document.isLocked, "the fixture is the locked file")
        XCTAssertEqual(PDFTitleChecker.check(document: document, title: Self.title), .unreadable)
    }

    func testNoTitleOpensNoFile() {
        XCTAssertEqual(PDFTitleChecker.check(data: Data("not a pdf".utf8), title: "<i></i>"), .noTitle)
        XCTAssertEqual(
            PDFTitleChecker.check(fileURL: URL(fileURLWithPath: "/nonexistent/gone.pdf"), title: " "),
            .noTitle
        )
    }

    /// PDFKit's text of a typeset title, read back, carries it: the share is
    /// computed on what the platform's own reader gives, not only on the rows.
    func testATitleTypesetOverTwoLinesMatches() throws {
        let data = try pdf("Journal of Hypertension\nCaffeine and blood pressure: a critical review\nperspective\nA. Author")
        XCTAssertEqual(PDFTitleChecker.check(data: data, title: Self.title), .matches(share: 1))
    }
}
