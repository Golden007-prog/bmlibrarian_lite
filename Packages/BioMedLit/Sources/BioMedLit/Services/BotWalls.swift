// BMLibrarian Lite - Biomedical Literature Research Tool
// Copyright (C) 2024-2026 Dr Horst Herb
// SPDX-License-Identifier: AGPL-3.0-or-later

import Foundation

/// Recognise a bot wall's challenge page from an answer alone (#480).
///
/// A port of Python's `bot_walls.py`; the rules and the shared cases are
/// `doc/cross_platform/fulltext_parity/challenge_detection.json`.
public enum BotWalls {
    /// The defences against programs, by name, and the lowercase needles each
    /// is recognised by. Equal to the contract's `markers` table.
    public static let wallMarkers: [String: [String]] = [
        "akamai": ["akamaighost", "errors.edgesuite.net", "you don't have permission to access"],
        "anubis": ["anubis", "making sure you're not a bot"],
        "aws-waf": ["awswaf", "aws-waf-token", "x-amzn-waf-action"],
        "captcha": ["g-recaptcha", "hcaptcha", "turnstile", "captcha"],
        "cloudflare": ["just a moment", "cf-chl", "challenge-platform", "cf-mitigated", "attention required! | cloudflare"],
        "datadome": ["datadome", "captcha-delivery.com"],
        "imperva": ["incapsula", "_incapsula_resource", "visid_incap"],
        "perimeterx": ["px-captcha", "perimeterx", "_pxhd"],
        "proof-of-work": ["proof of work", "pow-challenge", "checking your browser", "preparing to download"],
        "robot-text": ["verify you are human", "are you a robot", "unusual traffic", "not a robot",
                       "automated access", "bot detection", "ai crawlers", "hostile web agent"],
    ]

    /// Lowercase ASCII letters only: `lowercased()` also folds other letters.
    static func asciiLowercased(_ text: String) -> String {
        String(String.UnicodeScalarView(text.unicodeScalars.map { scalar in
            (65...90).contains(scalar.value) ? Unicode.Scalar(scalar.value + 32)! : scalar
        }))
    }

    /// The sorted defence names a page's text and header lines name.
    static func namedDefences(text: String, headers: [String: String]) -> [String] {
        let haystacks = ([text] + headers.map { "\($0.key): \($0.value)" })
            .map { Array(asciiLowercased($0).utf8) }
        return wallMarkers.compactMap { name, needles in
            needles.contains { needle in
                let bytes = Array(needle.utf8)
                return haystacks.contains { containsBytes($0, bytes) }
            } ? name : nil
        }.sorted()
    }

    /// Whether `needle` occurs in `haystack` as a run of bytes.
    ///
    /// Not `String.contains`: that compares grapheme clusters, so a needle
    /// followed by a combining mark would not match, where Python and Kotlin
    /// (code points, code units) find it. Every needle is ASCII.
    static func containsBytes(_ haystack: [UInt8], _ needle: [UInt8]) -> Bool {
        guard !needle.isEmpty, haystack.count >= needle.count else { return needle.isEmpty }
        for start in 0...(haystack.count - needle.count)
        where haystack[start] == needle[0] && haystack[start..<(start + needle.count)].elementsEqual(needle) {
            return true
        }
        return false
    }

    /// The defences an answer names, if it is a challenge page; empty otherwise.
    public static func challengeMarkers(status: Int, headers: [String: String], body: Data) -> [String] {
        if BioMedLitConstants.challengeGoneStatuses.contains(status) { return [] }
        let prefix = body.prefix(BioMedLitConstants.challengeBodyPrefixBytes)
        if prefix.starts(with: BioMedLitConstants.pdfMagicBytes) { return [] }
        var found = Set(namedDefences(text: String(decoding: prefix, as: UTF8.self), headers: headers))
        let server = headers.first { asciiLowercased($0.key) == "server" }
            .map { asciiLowercased($0.value.trimmingCharacters(in: .whitespaces)) } ?? ""
        if BioMedLitConstants.cloudflareRefusalStatuses.contains(status), server == BioMedLitConstants.cloudflareServer {
            found.insert("cloudflare")
        }
        return found.sorted()
    }

    /// The failure to record for a challenge page, or `nil` for any other answer.
    public static func challengeFailure(status: Int, headers: [String: String], body: Data) -> RequestFailure? {
        challengeMarkers(status: status, headers: headers, body: body).isEmpty ? nil : .challenged(statusCode: status)
    }
}
