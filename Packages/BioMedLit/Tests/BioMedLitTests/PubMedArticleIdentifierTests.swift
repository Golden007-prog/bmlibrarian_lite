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
@testable import BioMedLit

/// A PubMed record's own DOI and PMC ID reach the article it becomes.
///
/// The parser never read `<ArticleId>`, so every document found through a
/// PubMed search carried no DOI and no PMC ID. Transparency analysis looks
/// funders up in CrossRef by DOI, so for those documents it never asked, and
/// a Pfizer-funded study (PMID 42225758, below) was reported with no funders
/// checked.
final class PubMedArticleIdentifierTests: XCTestCase {

    /// PMID 42225758 as efetch returns it, trimmed to the elements that
    /// matter: its own identifiers, and a reference carrying another's.
    private let record = """
    <?xml version="1.0"?>
    <PubmedArticleSet>
      <PubmedArticle>
        <MedlineCitation Status="MEDLINE" Owner="NLM">
          <PMID Version="1">42225758</PMID>
          <Article PubModel="Electronic">
            <Journal><Title>Scientific reports</Title></Journal>
            <ArticleTitle>Tea and coffee consumption in relation to depression and anxiety symptoms: findings from the multicentric LIPOKAP study.</ArticleTitle>
            <ELocationID EIdType="pii" ValidYN="Y">24883</ELocationID>
            <ELocationID EIdType="doi" ValidYN="Y">10.1038/s41598-026-55251-z</ELocationID>
          </Article>
        </MedlineCitation>
        <PubmedData>
          <ArticleIdList>
            <ArticleId IdType="pubmed">42225758</ArticleId>
            <ArticleId IdType="pmc">PMC13458455</ArticleId>
            <ArticleId IdType="doi">10.1038/s41598-026-55251-z</ArticleId>
            <ArticleId IdType="pii">10.1038/s41598-026-55251-z</ArticleId>
          </ArticleIdList>
          <ReferenceList>
            <Reference>
              <Citation>A cited paper.</Citation>
              <ArticleIdList>
                <ArticleId IdType="doi">10.5812/ijpbs.101524</ArticleId>
                <ArticleId IdType="pmc">PMC1111111</ArticleId>
              </ArticleIdList>
            </Reference>
          </ReferenceList>
        </PubmedData>
      </PubmedArticle>
    </PubmedArticleSet>
    """

    private func parse(_ xml: String) -> [SearchArticle] {
        PubMedXMLParser(data: Data(xml.utf8)).parseArticleSet().articles
    }

    func testTheRecordsOwnDOIIsRead() {
        XCTAssertEqual(parse(record).first?.doi, "10.1038/s41598-026-55251-z")
    }

    func testTheRecordsOwnPMCIDIsRead() {
        let article = parse(record).first
        XCTAssertEqual(article?.pmcId, "PMC13458455")
        XCTAssertEqual(article?.hasFullText, true)
    }

    /// A cited paper's identifiers are not the article's. With the article's
    /// own list removed, only the reference's remain, and none may be taken.
    func testAReferencesIdentifiersAreNotTheArticles() {
        let referencesOnly = record
            .replacingOccurrences(of: #"<ArticleId IdType="pmc">PMC13458455</ArticleId>"#, with: "")
            .replacingOccurrences(of: #"<ArticleId IdType="doi">10.1038/s41598-026-55251-z</ArticleId>"#, with: "")
            .replacingOccurrences(of: #"<ELocationID EIdType="doi" ValidYN="Y">10.1038/s41598-026-55251-z</ELocationID>"#, with: "")
        let article = parse(referencesOnly).first
        XCTAssertNil(article?.doi)
        XCTAssertNil(article?.pmcId)
    }

    /// Older records carry the DOI only as an `ELocationID`; one marked invalid
    /// is not taken.
    func testTheELocationDOIIsReadWhenValid() {
        let withoutIdList = record.replacingOccurrences(
            of: #"<ArticleId IdType="doi">10.1038/s41598-026-55251-z</ArticleId>"#, with: ""
        )
        XCTAssertEqual(parse(withoutIdList).first?.doi, "10.1038/s41598-026-55251-z")

        let invalid = withoutIdList.replacingOccurrences(of: #"EIdType="doi" ValidYN="Y""#, with: #"EIdType="doi" ValidYN="N""#)
        XCTAssertNil(parse(invalid).first?.doi)
    }

    /// Identifiers do not leak from one record into the next.
    func testIdentifiersDoNotCarryIntoTheNextRecord() {
        let second = """
          <PubmedArticle>
            <MedlineCitation><PMID>31452104</PMID>
              <Article><Journal><Title>J</Title></Journal><ArticleTitle>No identifiers</ArticleTitle></Article>
            </MedlineCitation>
          </PubmedArticle>
        </PubmedArticleSet>
        """
        let both = record.replacingOccurrences(of: "</PubmedArticleSet>", with: second)
        let articles = parse(both)
        XCTAssertEqual(articles.count, 2)
        XCTAssertNil(articles[1].doi)
        XCTAssertNil(articles[1].pmcId)
    }

    // MARK: - The shared fixture

    /// PMID 33301246 as efetch returned it (trimmed), the one file the Python
    /// and Android suites read too (lite #404). Read from disk via #filePath,
    /// like the contract files, never from a bundle.
    private static let sharedFixture: URL? = {
        let relative = "tests/fixtures/pubmed_efetch_33301246.xml"
        var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        while directory.path != "/" {
            let candidate = directory.appendingPathComponent(relative)
            if FileManager.default.fileExists(atPath: candidate.path) { return candidate }
            if FileManager.default.fileExists(atPath: directory.appendingPathComponent(".git").path) { return nil }
            directory = directory.deletingLastPathComponent()
        }
        return nil
    }()

    private func sharedBody() throws -> String {
        let file = try XCTUnwrap(Self.sharedFixture, "pubmed_efetch_33301246.xml not found")
        return try String(contentsOf: file, encoding: .utf8)
    }

    /// The record's own ids, not those of the two papers in its ReferenceList.
    func testTheSharedFixturesOwnIdsAreRead() throws {
        let article = try XCTUnwrap(parse(sharedBody()).first)
        XCTAssertEqual(article.pmcId, "PMC7745181")
        XCTAssertEqual(article.doi, "10.1056/NEJMoa2034577")
    }

    /// With the record's own DOI gone from both places, a cited paper's
    /// (10.1056/NEJMoa2027906) must not stand in for it.
    func testTheSharedFixturesReferenceDOIIsNotTaken() throws {
        let body = try sharedBody()
        let withoutOwnDOI = body
            .replacingOccurrences(
                of: #"<ArticleId IdType="doi">10.1056/NEJMoa2034577</ArticleId></ArticleIdList><ReferenceList>"#,
                with: "</ArticleIdList><ReferenceList>"
            )
            .replacingOccurrences(
                of: #"<ELocationID EIdType="doi" ValidYN="Y">10.1056/NEJMoa2034577</ELocationID>"#,
                with: ""
            )
        XCTAssertLessThan(withoutOwnDOI.count, body.count - 80)
        let article = try XCTUnwrap(parse(withoutOwnDOI).first)
        XCTAssertNil(article.doi)
        XCTAssertEqual(article.pmcId, "PMC7745181")
    }
}
