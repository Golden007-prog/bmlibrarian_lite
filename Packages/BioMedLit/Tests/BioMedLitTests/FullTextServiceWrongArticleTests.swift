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

/// Reads only a file that is there, as PDFKit does: a file gone fails to open.
private struct FileReadingStub: PDFTextExtracting {
    func extract(from fileURL: URL) -> PDFExtractionResult {
        guard FileManager.default.fileExists(atPath: fileURL.path) else {
            return PDFExtractionResult(
                text: "", success: false, pageCount: 0, convertedPages: 0, warnings: [],
                errorMessage: "no file"
            )
        }
        return ExtractingStub().extract(from: fileURL)
    }
}

/// Another fetch refusing every file as it is read: the file is removed, and
/// the read fails, as PDFKit's does for a file gone.
private struct VanishingReader: PDFTextExtracting {
    func extract(from fileURL: URL) -> PDFExtractionResult {
        try? FileManager.default.removeItem(at: fileURL)
        return FileReadingStub().extract(from: fileURL)
    }
}

/// What the title checker saw, for a test to read back.
private final class CheckLog: @unchecked Sendable {
    private let lock = NSLock()
    private var entries: [(path: String, cacheNameExisted: Bool)] = []

    func record(_ path: String, cacheNameExisted: Bool) {
        lock.lock(); defer { lock.unlock() }
        entries.append((path, cacheNameExisted))
    }

    var all: [(path: String, cacheNameExisted: Bool)] {
        lock.lock(); defer { lock.unlock() }
        return entries
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

    /// The service, its checker refusing a file that holds ``wrongMarker``, or
    /// answering `outcome` for every file when one is given.
    private func makeService(
        elsevierAPIKey: String? = nil,
        outcome: PDFTitleCheck? = nil,
        extractor: PDFTextExtracting = ExtractingStub(),
        before: (@Sendable (URL) -> Void)? = nil
    ) -> FullTextService {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        let session = URLSession(configuration: config)
        return FullTextService(
            email: "test@example.org",
            session: session,
            europePMCService: EuropePMCService(session: session),
            extractor: extractor,
            europePMCRetry: .noRetry,
            pmcOpenDataRetry: .noRetry,
            openAlexRetry: .noRetry,
            coreThrottle: CoreThrottle(),
            elsevierAPIKey: elsevierAPIKey,
            elsevierRetry: .noRetry,
            elsevierSession: ElsevierSession(),
            titleChecker: { url, _ in
                before?(url)
                if let outcome { return outcome }
                // A file gone is unreadable, as PDFTitleChecker finds it
                guard let bytes = try? Data(contentsOf: url) else { return .unreadable }
                return bytes.range(of: FullTextServiceWrongArticleTests.wrongMarker) == nil
                    ? .matches(share: 1) : .mismatch(share: 0.1)
            }
        )
    }

    private func fetch(
        title: String?, elsevierAPIKey: String? = nil, outcome: PDFTitleCheck? = nil
    ) async throws -> FullTextResult {
        try await makeService(elsevierAPIKey: elsevierAPIKey, outcome: outcome)
            .fetchFullText(pmcId: nil, doi: doi, pmid: "", title: title)
    }

    /// Europe PMC's record for the article, naming its render.
    private var searchNamingTheRender: Data {
        Data(#"""
            {"resultList": {"result": [{
              "id": "1", "pmcid": "PMC1", "inPMC": "Y", "doi": "\#(doi)",
              "fullTextUrlList": {"fullTextUrl": [
                {"documentStyle": "pdf", "site": "Europe_PMC", "url": "\#(render)",
                 "availability": "Open access", "availabilityCode": "OA"}
              ]}
            }]}}
            """#.utf8)
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

    /// A check that judged nothing leaves the PDF in use: only a mismatch
    /// refuses one, so a scan, a translated title, an unreadable file or a
    /// title with no words is read as the article.
    func testAPDFTheCheckCouldNotJudgeIsRead() async throws {
        for outcome in [PDFTitleCheck.noTitle, .noText, .translatedTitle, .unreadable] {
            FullTextService.deleteCachedPDF(for: cacheKey)
            StubURLProtocol.routes["unpaywall"] = (200, unpaywall([wrong, right]))
            StubURLProtocol.routes["wrong.example.org"] = (200, wrongBody)
            let result = try await fetch(title: title, outcome: outcome)
            XCTAssertEqual(result.content, .unpaywall(pdfURL: URL(string: wrong)!), "\(outcome)")
            XCTAssertEqual(result.extractedText, "The article.", "\(outcome)")
            XCTAssertNil(result.openAccessShortfall, "\(outcome)")
            XCTAssertFalse(
                FileManager.default.fileExists(atPath: cachedPath(wrong) + BioMedLitConstants.pdfMismatchSuffix),
                "\(outcome): nothing set aside"
            )
        }
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
        StubURLProtocol.routes["search"] = (200, searchNamingTheRender)
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

    /// The control: a render that carries the title is the result, and no
    /// later source is asked.
    func testEuropePMCsRenderOfThisArticleIsServed() async throws {
        StubURLProtocol.routes["search"] = (200, searchNamingTheRender)
        StubURLProtocol.routes["fullTextXML"] = (404, Data())
        StubURLProtocol.routes["pmc-oa-opendata"] = (404, Data())
        StubURLProtocol.routes["articles/PMC1/pdf"] = (200, pdfBody)
        let result = try await fetch(title: title)
        XCTAssertEqual(result.content, .europePMCPDF(pdfURL: URL(string: render)!))
        XCTAssertEqual(result.extractedText, "The article.")
        XCTAssertNil(result.openAccessShortfall)
        XCTAssertFalse(StubURLProtocol.requested("unpaywall"))
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

    // MARK: - Checked before it is cached (#560)

    /// No file left waiting for its check in the cache directory for this article.
    private func assertNoDownloadLeftWaiting(file: StaticString = #filePath, line: UInt = #line) throws {
        let names = try FileManager.default.contentsOfDirectory(atPath: FullTextService.pdfCacheDirectory.path)
        let prefix = cacheKey.filenameComponent
        let waiting = names.filter { $0.hasPrefix(prefix) && $0.hasSuffix(BioMedLitConstants.pdfUncheckedSuffix) }
        XCTAssertEqual(waiting, [], file: file, line: line)
    }

    /// A download is judged where it waits, beside its cache name: another
    /// article's never reaches the name, so no other fetch can take it from
    /// the cache while this one sets it aside.
    func testADownloadIsCheckedBeforeItReachesItsCacheName() async throws {
        StubURLProtocol.routes["unpaywall"] = (200, unpaywall([wrong, right]))
        StubURLProtocol.routes["wrong.example.org"] = (200, wrongBody)
        StubURLProtocol.routes["right.example.org"] = (200, pdfBody)
        let log = CheckLog()
        let names = [cachedPath(wrong), cachedPath(right)]
        let result = try await makeService(before: { url in
            log.record(url.path, cacheNameExisted: names.contains { url.path.hasPrefix($0) && FileManager.default.fileExists(atPath: $0) })
        }).fetchFullText(pmcId: nil, doi: doi, pmid: "", title: title)
        XCTAssertEqual(result.content, .unpaywall(pdfURL: URL(string: right)!))
        XCTAssertEqual(result.localPDFPath, cachedPath(right))
        let checked = log.all
        XCTAssertEqual(checked.count, 2, "each download checked once: \(checked.map(\.path))")
        for entry in checked {
            XCTAssertFalse(names.contains(entry.path), "checked at its cache name: \(entry.path)")
            XCTAssertTrue(entry.path.hasSuffix(BioMedLitConstants.pdfUncheckedSuffix), entry.path)
            XCTAssertFalse(entry.cacheNameExisted, "its cache name held a file while it was checked")
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: cachedPath(wrong)))
        try assertNoDownloadLeftWaiting()
    }

    /// The race #560 names: a cached PDF another fetch sets aside while this
    /// one reads it is no copy served. Asked again, the download is judged on
    /// its own: here another article's, so the question stays open.
    func testACachedPDFSetAsideByAnotherFetchIsDownloadedAgainAndJudged() async throws {
        try FileManager.default.createDirectory(
            at: FullTextService.pdfCacheDirectory, withIntermediateDirectories: true
        )
        try wrongBody.write(to: URL(fileURLWithPath: cachedPath(wrong)))
        StubURLProtocol.routes["unpaywall"] = (200, unpaywall([wrong]))
        StubURLProtocol.routes["wrong.example.org"] = (200, wrongBody)
        let cached = cachedPath(wrong)
        // The other fetch: its check refused the cached file and moved it,
        // during this fetch's check, which then finds no file
        let result = try await makeService(extractor: FileReadingStub(), before: { url in
            guard url.path == cached, FileManager.default.fileExists(atPath: cached) else { return }
            try? FileManager.default.moveItem(atPath: cached, toPath: cached + BioMedLitConstants.pdfMismatchSuffix)
        }).fetchFullText(pmcId: nil, doi: doi, pmid: "", title: title)
        XCTAssertTrue(StubURLProtocol.requested("wrong.example.org"), "asked again")
        XCTAssertNil(result.localPDFPath, "no copy served")
        XCTAssertNil(result.extractedText)
        XCTAssertEqual(result.content, .doi(webURL: URL(string: "\(BioMedLitConstants.doiBaseURL)/\(doi)")!))
        XCTAssertEqual(result.openAccessShortfall, .wrongArticle(source: .pdf, address: wrong))
        try assertNoDownloadLeftWaiting()
    }

    /// The control: asked again, the source serves this article's PDF, and it
    /// is the result.
    func testACachedPDFSetAsideByAnotherFetchThenThisArticlesDownloadIsServed() async throws {
        try FileManager.default.createDirectory(
            at: FullTextService.pdfCacheDirectory, withIntermediateDirectories: true
        )
        try wrongBody.write(to: URL(fileURLWithPath: cachedPath(wrong)))
        StubURLProtocol.routes["unpaywall"] = (200, unpaywall([wrong]))
        StubURLProtocol.routes["wrong.example.org"] = (200, pdfBody)
        let cached = cachedPath(wrong)
        let result = try await makeService(extractor: FileReadingStub(), before: { url in
            guard url.path == cached, FileManager.default.fileExists(atPath: cached) else { return }
            try? FileManager.default.moveItem(atPath: cached, toPath: cached + BioMedLitConstants.pdfMismatchSuffix)
        }).fetchFullText(pmcId: nil, doi: doi, pmid: "", title: title)
        XCTAssertEqual(result.content, .unpaywall(pdfURL: URL(string: wrong)!))
        XCTAssertEqual(result.localPDFPath, cached)
        XCTAssertEqual(result.extractedText, "The article.")
        XCTAssertNil(result.openAccessShortfall)
    }

    /// A copy gone again after it was asked for again is no copy served (#560,
    /// the review of its fix): it may have been refused by another fetch, so
    /// no link to it is kept and the question stays open.
    func testACopyGoneTwiceBeforeItIsReadIsNoCopyServed() async throws {
        StubURLProtocol.routes["unpaywall"] = (200, unpaywall([wrong]))
        StubURLProtocol.routes["wrong.example.org"] = (200, pdfBody)
        let result = try await makeService(extractor: VanishingReader())
            .fetchFullText(pmcId: nil, doi: doi, pmid: "", title: title)
        XCTAssertEqual(result.content, .doi(webURL: URL(string: "\(BioMedLitConstants.doiBaseURL)/\(doi)")!))
        XCTAssertNil(result.localPDFPath)
        XCTAssertNil(result.pdfNotSavedFrom, "not a copy served and not saved")
        XCTAssertEqual(
            result.openAccessShortfall,
            OpenAccessShortfall(source: .pdf, failure: .requestFailed, address: wrong)
        )
        XCTAssertEqual(
            StubURLProtocol.requestedURLs.filter { $0.contains("wrong.example.org") }.count, 2, "asked again once, no more"
        )
    }

    /// The render gone twice keeps no link and is told.
    func testARenderGoneTwiceKeepsNoLink() async throws {
        StubURLProtocol.routes["search"] = (200, searchNamingTheRender)
        StubURLProtocol.routes["fullTextXML"] = (404, Data())
        StubURLProtocol.routes["pmc-oa-opendata"] = (404, Data())
        StubURLProtocol.routes["articles/PMC1/pdf"] = (200, pdfBody)
        StubURLProtocol.routes["unpaywall"] = (404, Data())
        let result = try await makeService(extractor: VanishingReader())
            .fetchFullText(pmcId: nil, doi: doi, pmid: "", title: title)
        XCTAssertNotEqual(result.content, .europePMCPDF(pdfURL: URL(string: render)!), "no link to it")
        XCTAssertNil(result.localPDFPath)
        XCTAssertEqual(
            result.openAccessShortfall?.entries.first,
            OpenAccessShortfall.Entry(source: .europePMCPDF, failure: .requestFailed, address: render)
        )
    }

    /// Elsevier's cached PDF set aside by another fetch: Elsevier is asked
    /// again, and its download is judged on its own.
    func testElseviersCachedPDFSetAsideByAnotherFetchIsAskedForAgain() async throws {
        try FileManager.default.createDirectory(
            at: FullTextService.pdfCacheDirectory, withIntermediateDirectories: true
        )
        let articleURL = try XCTUnwrap(Elsevier.articleURL(doi: doi)).absoluteString
        let cached = cachedPath(articleURL)
        try wrongBody.write(to: URL(fileURLWithPath: cached))
        StubURLProtocol.routes["api.elsevier.com"] = (200, pdfBody)
        let result = try await makeService(
            elsevierAPIKey: "test-elsevier-key", extractor: FileReadingStub(), before: { url in
                guard url.path == cached, FileManager.default.fileExists(atPath: cached) else { return }
                try? FileManager.default.moveItem(atPath: cached, toPath: cached + BioMedLitConstants.pdfMismatchSuffix)
            }
        ).fetchFullText(pmcId: nil, doi: doi, pmid: "", title: title)
        XCTAssertTrue(StubURLProtocol.requested("api.elsevier.com"), "asked again")
        XCTAssertEqual(result.source, .elsevier)
        XCTAssertEqual(result.localPDFPath, cached)
        XCTAssertEqual(result.extractedText, "The article.")
        XCTAssertNil(result.openAccessShortfall)
        try assertNoDownloadLeftWaiting()
    }

    /// Elsevier's download of another article never reaches its cache name.
    func testElseviersDownloadOfAnotherArticleNeverReachesItsCacheName() async throws {
        StubURLProtocol.routes["api.elsevier.com"] = (200, wrongBody)
        let articleURL = try XCTUnwrap(Elsevier.articleURL(doi: doi)).absoluteString
        let cached = cachedPath(articleURL)
        let log = CheckLog()
        _ = try await makeService(elsevierAPIKey: "test-elsevier-key", before: { url in
            log.record(url.path, cacheNameExisted: FileManager.default.fileExists(atPath: cached))
        }).fetchFullText(pmcId: nil, doi: doi, pmid: "", title: title)
        XCTAssertEqual(log.all.count, 1)
        XCTAssertNotEqual(log.all.first?.path, cached)
        XCTAssertEqual(log.all.first?.cacheNameExisted, false)
        XCTAssertFalse(FileManager.default.fileExists(atPath: cached))
        XCTAssertEqual(
            try Data(contentsOf: URL(fileURLWithPath: cached + BioMedLitConstants.pdfMismatchSuffix)), wrongBody
        )
        try assertNoDownloadLeftWaiting()
    }

    /// A move that fails is reported, never a success.
    func testAPromotionThatFailsIsReported() {
        let missing = FullTextService.pdfCacheDirectory.appendingPathComponent("absent-\(UUID().uuidString)").path
        XCTAssertFalse(FullTextService.promote(staged: missing, to: missing + ".pdf"))
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
