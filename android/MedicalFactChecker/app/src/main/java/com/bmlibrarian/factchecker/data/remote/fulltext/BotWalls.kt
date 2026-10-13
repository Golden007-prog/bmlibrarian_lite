package com.bmlibrarian.factchecker.data.remote.fulltext

import com.bmlibrarian.factchecker.domain.model.RequestFailure
import com.bmlibrarian.factchecker.domain.model.RequestFailureKind
import com.bmlibrarian.factchecker.util.Constants

/**
 * Recognise a bot wall's challenge page from an answer alone (#480).
 *
 * A port of Python's `bot_walls.py`; the rules and the shared cases are
 * `doc/cross_platform/fulltext_parity/challenge_detection.json`.
 */
object BotWalls {
    /** The defences by name, and the lowercase needles each is recognised by. */
    val WALL_MARKERS: Map<String, List<String>> = mapOf(
        "akamai" to listOf("akamaighost", "errors.edgesuite.net", "you don't have permission to access"),
        "anubis" to listOf("anubis", "making sure you're not a bot"),
        "aws-waf" to listOf("awswaf", "aws-waf-token", "x-amzn-waf-action"),
        "captcha" to listOf("g-recaptcha", "hcaptcha", "turnstile", "captcha"),
        "cloudflare" to listOf("just a moment", "cf-chl", "challenge-platform", "cf-mitigated", "attention required! | cloudflare"),
        "datadome" to listOf("datadome", "captcha-delivery.com"),
        "imperva" to listOf("incapsula", "_incapsula_resource", "visid_incap"),
        "perimeterx" to listOf("px-captcha", "perimeterx", "_pxhd"),
        "proof-of-work" to listOf("proof of work", "pow-challenge", "checking your browser", "preparing to download"),
        "robot-text" to listOf(
            "verify you are human", "are you a robot", "unusual traffic", "not a robot",
            "automated access", "bot detection", "ai crawlers", "hostile web agent",
        ),
    )

    /** Lowercase ASCII letters only: `lowercase()` also folds other letters. */
    internal fun asciiLowercase(text: String): String =
        buildString(text.length) { for (c in text) append(if (c in 'A'..'Z') c + ('a' - 'A') else c) }

    internal fun namedDefences(text: String, headers: List<Pair<String, String>>): List<String> {
        val haystacks = listOf(asciiLowercase(text)) + headers.map { (k, v) -> asciiLowercase("$k: $v") }
        return WALL_MARKERS.filter { (_, needles) -> needles.any { n -> haystacks.any { it.contains(n) } } }
            .keys.sorted()
    }

    /** The defences an answer names, if it is a challenge page; empty otherwise. */
    fun challengeMarkers(status: Int, headers: List<Pair<String, String>>, body: ByteArray): List<String> {
        if (status in Constants.CHALLENGE_GONE_STATUSES) return emptyList()
        val prefix = body.copyOf(minOf(body.size, Constants.CHALLENGE_BODY_PREFIX_BYTES))
        if (looksLikePdf(prefix)) return emptyList()
        val found = namedDefences(prefix.decodeToString(), headers).toMutableSet()
        val server = headers.firstOrNull { asciiLowercase(it.first) == "server" }
            ?.let { asciiLowercase(it.second.trim()) } ?: ""
        if (status in Constants.CLOUDFLARE_REFUSAL_STATUSES && server == Constants.CLOUDFLARE_SERVER) {
            found.add("cloudflare")
        }
        return found.sorted()
    }

    /** The failure to record for a challenge page, or null for any other answer. */
    fun challengeFailure(status: Int, headers: List<Pair<String, String>>, body: ByteArray): RequestFailure? =
        if (challengeMarkers(status, headers, body).isEmpty()) null
        else RequestFailure(RequestFailureKind.CHALLENGED, status)
}
