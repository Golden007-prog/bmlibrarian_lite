// BMLibrarian Lite - Biomedical Literature Research Tool
// Copyright (C) 2024-2026 Dr Horst Herb
// SPDX-License-Identifier: AGPL-3.0-or-later

import XCTest
@testable import BioMedLit

private struct ExtractingStub: PDFTextExtracting {
    func extract(from fileURL: URL) -> PDFExtractionResult {
        PDFExtractionResult(
            text: "The article.", success: true, pageCount: 1, convertedPages: 1, warnings: []
        )
    }
}

fileprivate extension RetryConfiguration {
    /// One attempt, no wait.
    static let noRetry = RetryConfiguration(
        maxAttempts: 1, initialDelay: 0, maxDelay: 0, backoffMultiplier: 1, jitterFactor: 0
    )
}

/// A PDF of another article is set aside unread, recorded with its address
/// as a source not asked, and the next source is tried; it is never an
/// absence (#546, the apps' port of #541).
final class FullTextServiceWrongArticleTests: XCTestCase {
    private let doi = "10.1016/j.wrong.2020.01.001"
    private let title = "Caffeine and blood pressure: a critical review"
    private let wrong = "https://wrong.example.org/a.pdf"
    private let right = "https://right.example.org/b.pdf"
    private let render = "https://europepmc.org/articles/PMC1/pdf"
    /// The marker the stub checker refuses a cached file by.
    private static let wrongMarker = Data("WRONG".utf8)
    private let pdfBody = Data("%PDF-1.7\n".utf8) + Data(repeating: 0x30, count: 64)
    private var wrongBody: Data { pdfBody + Self.wrongMarker }

    /// The article's cache key: no PMID, so the DOI names it.
    private var cacheKey: ArticleCacheKey { ArticleCacheKey(pmid: "", pmcId: nil, doi: doi)! }

    /// Where a PDF from `address` is cached for this article.
    private func cachedPath(_ address: String) -> String {
        FullTextService.pdfCacheDirectory
            .appendingPathComponent(FullTextService.cacheFilename(key: cacheKey, url: URL(string: address)!)).path
    }

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        FullTextService.deleteCachedPDF(for: cacheKey)
        // Europe PMC knows no record, so the open-access tiers decide the outcome
        StubURLProtocol.routes["search"] = (200, Data(#"{"resultList": {"result": []}}"#.utf8))
    }

    override func tearDown() {
        StubURLProtocol.reset()
        FullTextService.deleteCachedPDF(for: cacheKey)
        super.tearDown()
    }

    private func makeService(elsevierAPIKey: String? = nil) -> FullTextService {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        let session = URLSession(configuration: config)
        return FullTextService(
            email: "test@example.org",
            session: session,
            europePMCService: EuropePMCService(session: session),
            extractor: ExtractingStub(),
            europePMCRetry: .noRetry,
            pmcOpenDataRetry: .noRetry,
            openAlexRetry: .noRetry,
            coreThrottle: CoreThrottle(),
            elsevierAPIKey: elsevierAPIKey,
            elsevierRetry: .noRetry,
            elsevierSession: ElsevierSession(),
            titleChecker: { url, _ in
                let bytes = (try? Data(contentsOf: url)) ?? Data()
                return bytes.range(of: FullTextServiceWrongArticleTests.wrongMarker) == nil
                    ? .matches(share: 1) : .mismatch(share: 0.1)
            }
        )
    }

    private func fetch(title: String?, elsevierAPIKey: String? = nil) async throws -> FullTextResult {
        try await makeService(elsevierAPIKey: elsevierAPIKey)
            .fetchFullText(pmcId: nil, doi: doi, pmid: "", title: title)
    }

    private func unpaywall(_ pdfs: [String]) -> Data {
        let locations = pdfs.map { ["url_for_pdf": $0, "url": $0] }
        let body: [String: Any] = ["best_oa_location": locations.first as Any, "oa_locations": locations]
        return try! JSONSerialization.data(withJSONObject: body)
    }

    // MARK: - The open-access walk

    func testAnotherArticlesPDFIsSetAsideAndTheNextIsTried() async throws {
        StubURLProtocol.routes["unpaywall"] = (200, unpaywall([wrong, right]))
        StubURLProtocol.routes["wrong.example.org"] = (200, wrongBody)
        StubURLProtocol.routes["right.example.org"] = (200, pdfBody)
        let result = try await fetch(title: title)
        XCTAssertEqual(result.content, .unpaywall(pdfURL: URL(string: right)!))
        XCTAssertEqual(result.extractedText, "The article.")
        XCTAssertEqual(result.localPDFPath, cachedPath(right))
        XCTAssertNil(result.openAccessShortfall, "a copy served settles the question")
        XCTAssertFalse(FileManager.default.fileExists(atPath: cachedPath(wrong)), "never left to be served")
        XCTAssertEqual(
            try Data(contentsOf: URL(fileURLWithPath: cachedPath(wrong) + BioMedLitConstants.pdfMismatchSuffix)),
            wrongBody, "set aside, not deleted"
        )
    }

    /// The control: with no title nothing is checked, and the same PDF is read.
    func testWithNoTitleNothingIsChecked() async throws {
        StubURLProtocol.routes["unpaywall"] = (200, unpaywall([wrong, right]))
        StubURLProtocol.routes["wrong.example.org"] = (200, wrongBody)
        let result = try await fetch(title: nil)
        XCTAssertEqual(result.content, .unpaywall(pdfURL: URL(string: wrong)!))
        XCTAssertEqual(result.localPDFPath, cachedPath(wrong))
        XCTAssertFalse(StubURLProtocol.requested("right.example.org"))
    }

    /// The only copy another article's: told as a source not asked, no link to
    /// it kept, never an absence.
    func testTheOnlyCopyBeingAnotherArticlesLeavesTheQuestionOpen() async throws {
        StubURLProtocol.routes["unpaywall"] = (200, unpaywall([wrong]))
        StubURLProtocol.routes["wrong.example.org"] = (200, wrongBody)
        let result = try await fetch(title: title)
        XCTAssertEqual(result.content, .doi(webURL: URL(string: "\(BioMedLitConstants.doiBaseURL)/\(doi)")!))
        XCTAssertNil(result.localPDFPath)
        XCTAssertNil(result.extractedText)
        XCTAssertEqual(result.openAccessShortfall, .wrongArticle(source: .pdf, address: wrong))
        XCTAssertEqual(
            result.openAccessShortfall?.notice,
            "Failed to obtain a PDF from the following tried sources: wrong.example.org, named by Unpaywall "
                + "(another article's PDF). A freely available copy may exist. Whether this document is open "
                + "access was not established."
        )
    }

    /// A PDF cached by an earlier run is checked as a download is.
    func testACachedPDFOfAnotherArticleIsSetAside() async throws {
        try FileManager.default.createDirectory(
            at: FullTextService.pdfCacheDirectory, withIntermediateDirectories: true
        )
        try wrongBody.write(to: URL(fileURLWithPath: cachedPath(wrong)))
        StubURLProtocol.routes["unpaywall"] = (200, unpaywall([wrong, right]))
        StubURLProtocol.routes["right.example.org"] = (200, pdfBody)
        let result = try await fetch(title: title)
        XCTAssertFalse(StubURLProtocol.requested("wrong.example.org"), "the cached copy was the one checked")
        XCTAssertEqual(result.content, .unpaywall(pdfURL: URL(string: right)!))
        XCTAssertTrue(FileManager.default.fileExists(atPath: cachedPath(wrong) + BioMedLitConstants.pdfMismatchSuffix))
        XCTAssertFalse(FileManager.default.fileExists(atPath: cachedPath(wrong)))
    }

    /// A later refusal of the same name replaces the copy set aside before.
    func testASecondRefusalReplacesTheCopySetAside() async throws {
        StubURLProtocol.routes["unpaywall"] = (200, unpaywall([wrong]))
        StubURLProtocol.routes["wrong.example.org"] = (200, wrongBody)
        _ = try await fetch(title: title)
        StubURLProtocol.routes["wrong.example.org"] = (200, wrongBody + Data("again".utf8))
        _ = try await fetch(title: title)
        XCTAssertEqual(
            try Data(contentsOf: URL(fileURLWithPath: cachedPath(wrong) + BioMedLitConstants.pdfMismatchSuffix)),
            wrongBody + Data("again".utf8)
        )
    }

    // MARK: - The render and Elsevier's API

    func testEuropePMCsRenderOfAnotherArticleIsToldAndUnpaywallTried() async throws {
        StubURLProtocol.routes["search"] = (200, Data(#"""
            {"resultList": {"result": [{
              "id": "1", "pmcid": "PMC1", "inPMC": "Y", "doi": "\#(doi)",
              "fullTextUrlList": {"fullTextUrl": [
                {"documentStyle": "pdf", "site": "Europe_PMC", "url": "\#(render)",
                 "availability": "Open access", "availabilityCode": "OA"}
              ]}
            }]}}
            """#.utf8))
        StubURLProtocol.routes["fullTextXML"] = (404, Data())
        StubURLProtocol.routes["pmc-oa-opendata"] = (404, Data())
        StubURLProtocol.routes["articles/PMC1/pdf"] = (200, wrongBody)
        StubURLProtocol.routes["unpaywall"] = (404, Data())
        let result = try await fetch(title: title)
        XCTAssertNotEqual(result.content, .europePMCPDF(pdfURL: URL(string: render)!), "no link to the wrong PDF")
        XCTAssertNil(result.localPDFPath)
        XCTAssertTrue(StubURLProtocol.requested("unpaywall"))
        XCTAssertEqual(result.openAccessShortfall, .wrongArticle(source: .europePMCPDF, address: render))
        XCTAssertEqual(
            result.openAccessShortfall?.notice,
            "Failed to obtain a PDF from the following tried sources: europepmc.org, named by Europe PMC "
                + "(another article's PDF). A freely available copy may exist. Whether this document is open "
                + "access was not established."
        )
    }

    func testElseviersPDFOfAnotherArticleIsToldAndUnpaywallTried() async throws {
        StubURLProtocol.routes["api.elsevier.com"] = (200, wrongBody)
        StubURLProtocol.routes["unpaywall"] = (404, Data())
        let result = try await fetch(title: title, elsevierAPIKey: "test-elsevier-key")
        XCTAssertNotEqual(result.source, .elsevier)
        XCTAssertNil(result.localPDFPath)
        XCTAssertTrue(StubURLProtocol.requested("unpaywall"))
        let articleURL = try XCTUnwrap(Elsevier.articleURL(doi: doi)).absoluteString
        XCTAssertFalse(articleURL.contains("test-elsevier-key"), "the address carries no key")
        XCTAssertEqual(result.openAccessShortfall, .wrongArticle(source: .elsevier, address: articleURL))
    }

    /// The control: Elsevier's PDF that carries the title is served.
    func testElseviersPDFOfThisArticleIsServed() async throws {
        StubURLProtocol.routes["api.elsevier.com"] = (200, pdfBody)
        let result = try await fetch(title: title, elsevierAPIKey: "test-elsevier-key")
        XCTAssertEqual(result.source, .elsevier)
        XCTAssertNil(result.openAccessShortfall)
    }

    // MARK: - Recorded, never an absence

    /// A refusal is a shortfall, so a chain that ends with nothing else to
    /// return is never `noFullTextAvailable`.
    func testAWrongArticleShortfallIsNotAnAbsence() {
        let error = FullTextService.exhaustedChainError(
            primarySlot: "",
            primaryKind: nil,
            europePMCShortfall: nil,
            pmcOpenDataShortfall: nil,
            openAccessShortfall: .wrongArticle(source: .europePMCPDF, address: render),
            articleName: "a test article"
        )
        guard case .openAccessNotEstablished(let shortfall) = error else {
            return XCTFail("expected a shortfall, got \(error)")
        }
        XCTAssertEqual(shortfall.reason, .wrongArticle)
    }
}
