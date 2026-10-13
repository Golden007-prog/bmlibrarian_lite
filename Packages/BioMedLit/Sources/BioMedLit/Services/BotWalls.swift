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
    /// is recognised by in the start of a page. Equal to the contract's
    /// `markers` table. Header lines are not searched for these: a defence's
    /// cookie, its CDN's `Server` value or a CSP naming a captcha comes with
    /// ordinary answers too.
    public static let wallMarkers: [String: [String]] = [
        // Akamai's refusal carries no Server header, and escapes its reference
        // URL as entities; "you don't have permission to access" is also
        // Apache's stock 403 text, so it is no needle.
        "akamai": ["errors.edgesuite.net", "errors&#46;edgesuite&#46;net"],
        "anubis": ["anubis", "making sure you're not a bot"],
        "aws-waf": ["awswaf", "aws-waf-token"],
        "captcha": ["g-recaptcha", "hcaptcha", "turnstile", "captcha"],
        // "_cf_chl_opt" is the challenge's own script; "challenge-platform"
        // also names the bot-management script Cloudflare adds to ordinary pages.
        "cloudflare": ["just a moment", "cf-chl", "_cf_chl_opt", "attention required! | cloudflare"],
        "datadome": ["datadome", "captcha-delivery.com"],
        "imperva": ["incapsula", "_incapsula_resource", "visid_incap"],
        "perimeterx": ["px-captcha", "perimeterx", "_pxhd"],
        "proof-of-work": ["proof of work", "pow-challenge", "checking your browser", "preparing to download"],
        "robot-text": ["verify you are human", "are you a robot", "unusual traffic", "not a robot",
                       "automated access", "bot detection", "ai crawlers", "hostile web agent"],
    ]

    /// The header names, lowercase, that a defence sends only with a
    /// challenge. Matched as whole names; values are not read. Equal to the
    /// contract's `header_markers` table.
    public static let wallHeaderMarkers: [String: [String]] = [
        "aws-waf": ["x-amzn-waf-action"],
        "cloudflare": ["cf-mitigated"],
    ]

    /// The defence named by Cloudflare's own refusal (a key of both tables).
    public static let cloudflareDefence = "cloudflare"

    /// The scalar values of A-Z, the only letters lowercased.
    private static let asciiUppercase = UInt32(UInt8(ascii: "A"))...UInt32(UInt8(ascii: "Z"))
    /// What adding to an A-Z scalar value makes it a-z.
    private static let asciiCaseOffset = UInt32(UInt8(ascii: "a") - UInt8(ascii: "A"))

    /// Lowercase ASCII letters only: `lowercased()` also folds other letters
    /// (the Kelvin sign to "k"), which Python's and Kotlin's do not.
    static func asciiLowercased(_ text: String) -> String {
        String(String.UnicodeScalarView(text.unicodeScalars.map { scalar in
            asciiUppercase.contains(scalar.value)
                ? Unicode.Scalar(scalar.value + asciiCaseOffset) ?? scalar
                : scalar
        }))
    }

    /// The sorted defence names a page's text and its header names name.
    static func namedDefences(text: String, headers: [String: String]) -> [String] {
        let page = Array(asciiLowercased(text).utf8)
        var found = Set(wallMarkers.compactMap { name, needles in
            needles.contains { containsBytes(page, Array($0.utf8)) } ? name : nil
        })
        let names = Set(headers.keys.map { asciiLowercased($0) })
        for (name, needles) in wallHeaderMarkers where needles.contains(where: names.contains) {
            found.insert(name)
        }
        return found.sorted()
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
            .map { asciiLowercased($0.value.trimmingCharacters(in: BioMedLitConstants.httpHeaderWhitespace)) } ?? ""
        if BioMedLitConstants.cloudflareRefusalStatuses.contains(status), server == BioMedLitConstants.cloudflareServer {
            found.insert(cloudflareDefence)
        }
        return found.sorted()
    }

    /// The failure to record for a challenge page, or `nil` for any other answer.
    public static func challengeFailure(status: Int, headers: [String: String], body: Data) -> RequestFailure? {
        challengeMarkers(status: status, headers: headers, body: body).isEmpty ? nil : .challenged(statusCode: status)
    }
}
