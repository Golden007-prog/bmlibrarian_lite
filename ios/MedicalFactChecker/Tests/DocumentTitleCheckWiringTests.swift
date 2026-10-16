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

import BioMedLit
import Foundation
import XCTest
@testable import MedicalFactChecker

/// Answers Unpaywall with one PDF and serves it; everything else finds nothing.
private final class OnePDFURLProtocol: URLProtocol {
    /// The one PDF Unpaywall names, and the only address served one.
    static let pdfAddress = "https://repo.example.org/title-check-wiring.pdf"

    override class func canInit(with request: URLRequest) -> Bool { true }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let urlString = request.url?.absoluteString ?? ""
        let body: Data?
        if urlString.contains("unpaywall") {
            let location = ["url_for_pdf": Self.pdfAddress, "url": Self.pdfAddress]
            body = try? JSONSerialization.data(
                withJSONObject: ["best_oa_location": location, "oa_locations": [location]]
            )
        } else if urlString == Self.pdfAddress {
            body = Data("%PDF-1.7\n".utf8) + Data(repeating: 0x30, count: 64)
        } else if urlString.contains("search") {
            body = Data(#"{"resultList": {"result": []}}"#.utf8)
        } else {
            body = nil
        }
        let response = HTTPURLResponse(
            url: request.url!, statusCode: body == nil ? 404 : 200, httpVersion: nil, headerFields: nil
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        if let body { client?.urlProtocol(self, didLoad: body) }
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

/// The titles a checker was asked about, across the service's tasks.
private final class AskedTitles: @unchecked Sendable {
    private let lock = NSLock()
    private var titles: [String] = []

    /// Note a title the checker was asked about.
    func record(_ title: String) {
        lock.withLock { titles.append(title) }
    }

    /// Every title asked about, in order.
    var all: [String] { lock.withLock { titles } }
}

/// The app's one fetch seam hands the chain the document's title, which every
/// PDF it reads is checked against (#546). Without it the check never runs, and
/// another article's PDF is read as this one's: no BioMedLit test would notice,
/// since they pass the title to the service themselves.
final class DocumentTitleCheckWiringTests: XCTestCase {
    private let doi = "10.1/title-check-wiring"

    override func setUp() {
        super.setUp()
        StringArrayTransformer.register()
        deleteCachedPDF()
    }

    override func tearDown() {
        deleteCachedPDF()
        super.tearDown()
    }

    /// Removes what a fetch cached for the test's DOI, so no run sees another's file.
    private func deleteCachedPDF() {
        if let key = ArticleCacheKey(pmid: "", pmcId: nil, doi: doi) {
            BMLFullTextService.deleteCachedPDF(for: key)
        }
    }

    /// The service, its checker recording each title and answering `outcome`.
    private func service(asked: AskedTitles, outcome: PDFTitleCheck) -> BMLFullTextService {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [OnePDFURLProtocol.self]
        let session = URLSession(configuration: config)
        return BMLFullTextService(
            email: "test@example.org",
            session: session,
            europePMCService: BMLEuropePMCService(session: session),
            titleChecker: { _, title in
                asked.record(title)
                return outcome
            }
        )
    }

    func testThePDFIsCheckedAgainstTheDocumentsTitle() async throws {
        let document = Document(pmid: "", title: "Caffeine and blood pressure", abstract: "")
        document.doi = doi
        let asked = AskedTitles()

        let result = try await service(asked: asked, outcome: .mismatch(share: 0)).fetchFullText(for: document)

        XCTAssertEqual(asked.all, ["Caffeine and blood pressure"])
        XCTAssertNil(result.localPDFPath, "another article's PDF is not kept")
        XCTAssertEqual(
            result.openAccessShortfall,
            .wrongArticle(source: .pdf, address: OnePDFURLProtocol.pdfAddress)
        )
    }

    /// The control: the same PDF, carrying the title, is the result.
    func testAPDFCarryingTheTitleIsKept() async throws {
        let document = Document(pmid: "", title: "Caffeine and blood pressure", abstract: "")
        document.doi = doi
        let asked = AskedTitles()

        let result = try await service(asked: asked, outcome: .matches(share: 1)).fetchFullText(for: document)

        XCTAssertEqual(asked.all, ["Caffeine and blood pressure"])
        XCTAssertNotNil(result.localPDFPath)
        XCTAssertNil(result.openAccessShortfall)
    }
}
