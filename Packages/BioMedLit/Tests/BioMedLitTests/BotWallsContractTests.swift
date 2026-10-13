// BMLibrarian Lite - Biomedical Literature Research Tool
// Copyright (C) 2024-2026 Dr Horst Herb
// SPDX-License-Identifier: AGPL-3.0-or-later

import XCTest
@testable import BioMedLit

final class BotWallsContractTests: XCTestCase {
    private static let contractFile: URL? = {
        let relative = "doc/cross_platform/fulltext_parity/challenge_detection.json"
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
        let file = try XCTUnwrap(Self.contractFile, "challenge_detection.json not found")
        let object = try JSONSerialization.jsonObject(with: Data(contentsOf: file))
        return try XCTUnwrap(object as? [String: Any])
    }

    private func body(_ row: [String: Any]) -> Data {
        let padding = row["body_padding_bytes"] as? Int ?? 0
        return Data(repeating: 0x20, count: padding) + Data((row["body"] as? String ?? "").utf8)
    }

    func testTheMarkerTableIsTheContracts() throws {
        let markers = try XCTUnwrap(contract()["markers"] as? [String: [String]])
        XCTAssertEqual(BotWalls.wallMarkers, markers)
    }

    func testTheBoundsAreTheContracts() throws {
        let contract = try contract()
        XCTAssertEqual(BioMedLitConstants.challengeBodyPrefixBytes, contract["body_prefix_bytes"] as? Int)
        XCTAssertEqual(BioMedLitConstants.challengeGoneStatuses, Set(contract["gone_statuses"] as? [Int] ?? []))
        XCTAssertEqual(BioMedLitConstants.cloudflareRefusalStatuses, Set(contract["cloudflare_refusal_statuses"] as? [Int] ?? []))
    }

    func testEachCase() throws {
        let cases = try XCTUnwrap(contract()["cases"] as? [[String: Any]])
        XCTAssertFalse(cases.isEmpty)
        for row in cases {
            let status = try XCTUnwrap(row["status"] as? Int)
            let headers = row["headers"] as? [String: String] ?? [:]
            let expected = try XCTUnwrap(row["expected"] as? [String])
            XCTAssertEqual(BotWalls.challengeMarkers(status: status, headers: headers, body: body(row)), expected, "\(row["name"] ?? "")")
            let failure = BotWalls.challengeFailure(status: status, headers: headers, body: body(row))
            XCTAssertEqual(failure, expected.isEmpty ? nil : .challenged(statusCode: status), "\(row["name"] ?? "")")
        }
    }

    func testBinaryThatIsNotAPDFIsNoChallenge() {
        let jpeg = Data([0xFF, 0xD8, 0xFF, 0xE0]) + Data((128...255).map { UInt8($0) })
        XCTAssertEqual(BotWalls.challengeMarkers(status: 200, headers: [:], body: jpeg), [])
    }

    func testLowercasingIsASCIIOnly() {
        XCTAssertEqual(BotWalls.challengeMarkers(status: 200, headers: [:], body: Data("A\u{212A}AMAIGHOST".utf8)), [])
    }
}
