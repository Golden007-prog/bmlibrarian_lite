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
    /**
     * The defences against programs, by name, and the lowercase needles each is
     * recognised by in the start of a page. Equal to the contract's `markers`
     * table. Header lines are not searched for these: a defence's cookie, its
     * CDN's `Server` value or a CSP naming a captcha comes with ordinary answers too.
     */
    val WALL_MARKERS: Map<String, List<String>> = mapOf(
        // Akamai's refusal carries no Server header, and escapes its reference URL
        // as entities; "you don't have permission to access" is also Apache's stock
        // 403 text, so it is no needle.
        "akamai" to listOf("errors.edgesuite.net", "errors&#46;edgesuite&#46;net"),
        "anubis" to listOf("anubis", "making sure you're not a bot"),
        "aws-waf" to listOf("awswaf", "aws-waf-token"),
        "captcha" to listOf("g-recaptcha", "hcaptcha", "turnstile", "captcha"),
        // "_cf_chl_opt" is the challenge's own script; "challenge-platform" also
        // names the bot-management script Cloudflare adds to ordinary pages.
        "cloudflare" to listOf("just a moment", "cf-chl", "_cf_chl_opt", "attention required! | cloudflare"),
        "datadome" to listOf("datadome", "captcha-delivery.com"),
        "imperva" to listOf("incapsula", "_incapsula_resource", "visid_incap"),
        "perimeterx" to listOf("px-captcha", "perimeterx", "_pxhd"),
        "proof-of-work" to listOf("proof of work", "pow-challenge", "checking your browser", "preparing to download"),
        "robot-text" to listOf(
            "verify you are human", "are you a robot", "unusual traffic", "not a robot",
            "automated access", "bot detection", "ai crawlers", "hostile web agent",
        ),
    )

    /**
     * The header names, lowercase, that a defence sends only with a challenge.
     * Matched as whole names; values are not read. Equal to the contract's
     * `header_markers` table.
     */
    val WALL_HEADER_MARKERS: Map<String, List<String>> = mapOf(
        "aws-waf" to listOf("x-amzn-waf-action"),
        "cloudflare" to listOf("cf-mitigated"),
    )

    /** The defence named by Cloudflare's own refusal (a key of both tables). */
    const val CLOUDFLARE_DEFENCE = "cloudflare"

    /** Lowercase ASCII letters only: `lowercase()` also folds other letters (the Kelvin sign to "k"). */
    internal fun asciiLowercase(text: String): String =
        buildString(text.length) { for (c in text) append(if (c in 'A'..'Z') c + ('a' - 'A') else c) }

    /** The sorted defence names a page's text and its header names name. */
    internal fun namedDefences(text: String, headers: List<Pair<String, String>>): List<String> {
        val page = asciiLowercase(text)
        val names = headers.map { (name, _) -> asciiLowercase(name) }.toSet()
        val found = WALL_MARKERS.filter { (_, needles) -> needles.any { page.contains(it) } }.keys +
            WALL_HEADER_MARKERS.filter { (_, needles) -> needles.any { it in names } }.keys
        return found.sorted()
    }

    /** Trim the whitespace HTTP allows around a header's value, and nothing else. */
    private fun trimHeaderValue(value: String): String =
        value.trim { it in Constants.HTTP_HEADER_WHITESPACE }

    /** The defences an answer names, if it is a challenge page; empty otherwise. */
    fun challengeMarkers(status: Int, headers: List<Pair<String, String>>, body: ByteArray): List<String> {
        if (status in Constants.CHALLENGE_GONE_STATUSES) return emptyList()
        val prefix = body.copyOf(minOf(body.size, Constants.CHALLENGE_BODY_PREFIX_BYTES))
        if (looksLikePdf(prefix)) return emptyList()
        val found = namedDefences(prefix.decodeToString(), headers).toMutableSet()
        val server = headers.firstOrNull { asciiLowercase(it.first) == "server" }
            ?.let { asciiLowercase(trimHeaderValue(it.second)) } ?: ""
        if (status in Constants.CLOUDFLARE_REFUSAL_STATUSES && server == Constants.CLOUDFLARE_SERVER) {
            found.add(CLOUDFLARE_DEFENCE)
        }
        return found.sorted()
    }

    /** The failure to record for a challenge page, or null for any other answer. */
    fun challengeFailure(status: Int, headers: List<Pair<String, String>>, body: ByteArray): RequestFailure? =
        if (challengeMarkers(status, headers, body).isEmpty()) null
        else RequestFailure(RequestFailureKind.CHALLENGED, status)
}
