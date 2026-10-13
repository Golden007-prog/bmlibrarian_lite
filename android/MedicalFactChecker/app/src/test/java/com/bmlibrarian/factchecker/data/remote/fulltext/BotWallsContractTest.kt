package com.bmlibrarian.factchecker.data.remote.fulltext

import com.bmlibrarian.factchecker.domain.model.RequestFailure
import com.bmlibrarian.factchecker.domain.model.RequestFailureKind
import com.bmlibrarian.factchecker.util.Constants
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.File

class BotWallsContractTest {

    private val contract: JsonObject by lazy {
        Json.parseToJsonElement(contractFile().readText()).jsonObject
    }

    private fun contractFile(): File {
        val relative = "doc/cross_platform/fulltext_parity/challenge_detection.json"
        var directory: File? = File("").absoluteFile
        while (directory != null) {
            val candidate = File(directory, relative)
            if (candidate.isFile) return candidate
            if (File(directory, ".git").exists()) break
            directory = directory.parentFile
        }
        error("could not locate $relative above ${File("").absolutePath}")
    }

    private fun body(row: JsonObject): ByteArray {
        val padding = row["body_padding_bytes"]?.jsonPrimitive?.intOrNull ?: 0
        return ByteArray(padding) { ' '.code.toByte() } + row["body"]!!.jsonPrimitive.content.toByteArray(Charsets.UTF_8)
    }

    private fun headers(row: JsonObject): List<Pair<String, String>> =
        row["headers"]!!.jsonObject.map { (k, v) -> k to v.jsonPrimitive.content }

    @Test
    fun `the marker table is the contract's`() {
        val markers = contract["markers"]!!.jsonObject.mapValues { (_, v) -> v.jsonArray.map { it.jsonPrimitive.content } }
        assertEquals(markers, BotWalls.WALL_MARKERS)
    }

    @Test
    fun `the bounds are the contract's`() {
        assertEquals(contract["body_prefix_bytes"]!!.jsonPrimitive.int, Constants.CHALLENGE_BODY_PREFIX_BYTES)
        assertEquals(contract["gone_statuses"]!!.jsonArray.map { it.jsonPrimitive.int }.toSet(), Constants.CHALLENGE_GONE_STATUSES)
        assertEquals(contract["cloudflare_refusal_statuses"]!!.jsonArray.map { it.jsonPrimitive.int }.toSet(), Constants.CLOUDFLARE_REFUSAL_STATUSES)
    }

    @Test
    fun `each case`() {
        for (element in contract["cases"]!!.jsonArray) {
            val row = element.jsonObject
            val status = row["status"]!!.jsonPrimitive.int
            val expected = row["expected"]!!.jsonArray.map { it.jsonPrimitive.content }
            val name = row["name"]!!.jsonPrimitive.content
            assertEquals(name, expected, BotWalls.challengeMarkers(status, headers(row), body(row)))
            val failure = BotWalls.challengeFailure(status, headers(row), body(row))
            if (expected.isEmpty()) assertNull(name, failure)
            else assertEquals(name, RequestFailure(RequestFailureKind.CHALLENGED, status), failure)
        }
    }

    @Test
    fun `binary that is not a PDF is no challenge`() {
        val jpeg = byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0xE0.toByte()) + ByteArray(128) { (it + 128).toByte() }
        assertEquals(emptyList<String>(), BotWalls.challengeMarkers(200, emptyList(), jpeg))
    }

    @Test
    fun `lowercasing is ASCII only`() {
        assertEquals(emptyList<String>(), BotWalls.challengeMarkers(200, emptyList(), "A\u212AAMAIGHOST".toByteArray()))
    }
}
