// BMLibrarian Lite - Biomedical Literature Research Tool
// Copyright (C) 2024-2025 Dr Horst Herb
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

/// Author extraction across the two `<contrib>` conventions JATS allows.
///
/// `contrib-type="author"` on each `<contrib>` is one; declaring it once on the
/// enclosing `<contrib-group content-type="author">` and leaving the children
/// bare is the other. PLOS uses the second for every article it deposits, so
/// requiring the attribute on the `<contrib>` lost every author it has.
final class JATSContribTests: XCTestCase {

    private func parse(front: String) throws -> JATSArticle {
        let xml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <article>
          <front><article-meta>
            <title-group><article-title>T</article-title></title-group>
        \(front)
          </article-meta></front>
          <body><p>Prose.</p></body>
        </article>
        """
        return try JATSXMLParser(data: Data(xml.utf8)).parseToArticle()
    }

    /// The convention the parser already handled.
    func testContribTypeOnEachContrib() throws {
        let article = try parse(front: """
            <contrib-group>
              <contrib contrib-type="author"><name><surname>Smith</surname><given-names>John</given-names></name></contrib>
              <contrib contrib-type="author"><name><surname>Doe</surname><given-names>Jane</given-names></name></contrib>
            </contrib-group>
        """)

        XCTAssertEqual(article.authors.map(\.surname), ["Smith", "Doe"])
    }

    /// PLOS's shape: the group declares the role, the children are bare.
    func testContentTypeOnTheGroupWithBareContribs() throws {
        let article = try parse(front: """
            <contrib-group content-type="author">
              <contrib><name name-style="western"><surname>Hwang</surname><given-names initials="SH">Sun-Hee</given-names></name></contrib>
              <contrib><name name-style="western"><surname>Choi</surname><given-names initials="K">Kyungsuk</given-names></name></contrib>
            </contrib-group>
        """)

        XCTAssertEqual(article.authors.map(\.surname), ["Hwang", "Choi"])
    }

    /// A plain `<contrib-group>` with bare children: authors by JATS convention.
    func testBareGroupWithBareContribs() throws {
        let article = try parse(front: """
            <contrib-group>
              <contrib><name><surname>Solo</surname><given-names>Ann</given-names></name></contrib>
            </contrib-group>
        """)

        XCTAssertEqual(article.authors.map(\.surname), ["Solo"])
    }

    // MARK: - Non-authors must stay out

    func testEditorsAreNotAuthors() throws {
        let article = try parse(front: """
            <contrib-group content-type="author">
              <contrib><name><surname>Real</surname><given-names>Ada</given-names></name></contrib>
            </contrib-group>
            <contrib-group content-type="editor">
              <contrib><name><surname>Gatekeeper</surname><given-names>Ed</given-names></name></contrib>
            </contrib-group>
        """)

        XCTAssertEqual(article.authors.map(\.surname), ["Real"])
    }

    /// An explicit non-author type wins over a permissive group.
    func testExplicitEditorContribTypeIsNotAnAuthor() throws {
        let article = try parse(front: """
            <contrib-group>
              <contrib contrib-type="author"><name><surname>Real</surname><given-names>Ada</given-names></name></contrib>
              <contrib contrib-type="editor"><name><surname>Gatekeeper</surname><given-names>Ed</given-names></name></contrib>
            </contrib-group>
        """)

        XCTAssertEqual(article.authors.map(\.surname), ["Real"])
    }
}

/// The contributor-role rule the three platforms share (bmlib#111, bmlib#266;
/// `doc/cross_platform/jats_parsing.md`, "Contributor Roles"). Each case is one
/// where Swift's earlier rule differed from Python's `_article_authors`, which is
/// canonical; none occurs in the 1,623-article survey corpus.
final class JATSContribRoleParityTests: XCTestCase {

    private func contrib(_ surname: String, _ attrs: String = "") -> String {
        "<contrib \(attrs)><name><surname>\(surname)</surname><given-names>A</given-names></name></contrib>"
    }

    /// The surnames read from an article whose `<front>` holds `front`.
    private func authors(front: String) throws -> [String] {
        let xml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <article><front>\(front)</front><body><p>Prose.</p></body></article>
        """
        return try JATSXMLParser(data: Data(xml.utf8)).parseToArticle().authors.map(\.surname)
    }

    /// The surnames read from an `<article-meta>` holding `meta`.
    private func authors(meta: String) throws -> [String] {
        try authors(front: "<article-meta><title-group><article-title>T</article-title></title-group>\(meta)</article-meta>")
    }

    func testAnEmptyContribTypeDeclaresNothing() throws {
        XCTAssertEqual(try authors(meta: "<contrib-group>\(contrib("Smith", "contrib-type=\"\""))</contrib-group>"), ["Smith"])
    }

    func testAnEmptyGroupContentTypeDeclaresNothing() throws {
        XCTAssertEqual(try authors(meta: #"<contrib-group content-type="">"# + contrib("Smith") + "</contrib-group>"), ["Smith"])
    }

    /// Only `author` is an author; `authors` is not a JATS role value.
    func testAuthorsPluralIsNotAnAuthorRole() throws {
        XCTAssertEqual(try authors(meta: #"<contrib-group content-type="authors">"# + contrib("Smith") + "</contrib-group>"), [])
    }

    func testRoleValuesAreTrimmed() throws {
        let meta = #"<contrib-group content-type=" Author ">"# + contrib("Smith")
            + contrib("Ed", "contrib-type=\" editor \"") + "</contrib-group>"
        XCTAssertEqual(try authors(meta: meta), ["Smith"])
    }

    func testAJournalMetaContribIsNoAuthor() throws {
        let front = "<journal-meta><contrib-group>\(contrib("Bare"))\(contrib("Own", #"contrib-type="author""#))</contrib-group></journal-meta>"
            + "<article-meta><contrib-group>\(contrib("Smith"))</contrib-group></article-meta>"
        XCTAssertEqual(try authors(front: front), ["Smith"])
    }

    func testABareContribInASupplementOrSecMetaGroupIsNoAuthor() throws {
        let meta = "<contrib-group>\(contrib("Smith"))</contrib-group>"
            + #"<supplement><contrib-group content-type="author">"# + contrib("SupAuthor") + "</contrib-group></supplement>"
            + "<sec-meta><contrib-group>\(contrib("SecAuthor"))</contrib-group></sec-meta>"
        XCTAssertEqual(try authors(meta: meta), ["Smith"])
    }

    /// Control: a contributor's own author role is read in a nested group.
    func testAnOwnAuthorRoleInASupplementGroupIsStillRead() throws {
        let meta = "<supplement><contrib-group>\(contrib("Own", #"contrib-type="author""#))\(contrib("Bare"))</contrib-group></supplement>"
        XCTAssertEqual(try authors(meta: meta), ["Own"])
    }

    func testACollabRosterIsNotListed() throws {
        let meta = #"<contrib-group content-type="author">"# + contrib("Smith")
            + "<contrib><collab>Study Group<contrib-group>\(contrib("Member"))"
            + "\(contrib("OwnMember", #"contrib-type="author""#))</contrib-group></collab></contrib>"
            + "</contrib-group>"
        XCTAssertEqual(try authors(meta: meta), ["Smith"])
    }

    /// The innermost *declared* group decides, not the innermost group.
    func testABareRosterInsideAnEditorGroupStaysEditors() throws {
        let meta = #"<contrib-group content-type="editor"><contrib-group>"# + contrib("Ed")
            + "</contrib-group></contrib-group>"
            + #"<contrib-group content-type="author">"# + contrib("Smith") + "</contrib-group>"
        XCTAssertEqual(try authors(meta: meta), ["Smith"])
    }

    /// Control: a nested undeclared group inside an author group is authors.
    func testABareRosterInsideAnAuthorGroupIsAuthors() throws {
        let meta = #"<contrib-group content-type="author"><contrib-group>"# + contrib("Inner")
            + "</contrib-group>" + #"<contrib-group content-type="editor">"# + contrib("InnerEd")
            + "</contrib-group></contrib-group>"
        XCTAssertEqual(try authors(meta: meta), ["Inner"])
    }

    /// PMC12337215's contributor, verbatim: the last spelling is kept, as in
    /// bmlib (bmlib#143), Python and Android.
    func testNameAlternativesGiveTheLastSpelling() throws {
        let meta = """
        <contrib-group><contrib contrib-type="author"><name-alternatives>\
        <name name-style="eastern"><surname>周</surname><given-names initials="诚程">诚程</given-names></name>\
        <name xml:lang="en" name-style="western"><surname>Zhou</surname><given-names initials="C">Chengcheng</given-names></name>\
        </name-alternatives><xref rid="aff1" ref-type="aff">1</xref></contrib></contrib-group>
        """
        XCTAssertEqual(try authors(meta: meta), ["Zhou"])
    }
}
