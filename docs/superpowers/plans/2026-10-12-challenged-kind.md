# The `challenged` kind: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recognise a bot wall's challenge page in place of an open-access PDF on all three platforms, record it as a new failure kind `challenged`, and tell the reader so. Today it is a 403 ("did not serve it") or an unreadable answer.

**Architecture:** One pure detector per platform, `challenge_markers(status, headers, body_prefix)`, pinned by a shared fixture `challenge_detection.json`, which also carries the marker table itself. Each platform's open-access PDF download asks the detector when an answer is not a PDF and records `RequestFailure(CHALLENGED, status)` in place of `http_status` / `malformed_response`. The kind joins the two existing shared contracts (`answered_lookup_verb.json`, `open_access_unsettled_notice.json`). It is never an answer, so every existing sentence builder says "could not be asked" with no new code.

**Tech Stack:** Python 3.12 (`requests`, pytest), Swift 5.9+ (BioMedLit SwiftPM package, XCTest, `StubURLProtocol`), Kotlin (OkHttp, kotlinx.serialization, JUnit 4, MockWebServer).

**Spec:** `docs/superpowers/specs/2026-10-12-browser-rescue-design.md`, section 1 (stage 1 in its Stages table). Read that section before starting.

## Global Constraints

- The persisted raw value is exactly `challenged`, on every platform. Never rename it.
- A `challenged` failure carries the HTTP status the challenge came with (200, 403, 503 …); the kind joins the status-carrying kinds next to `http_status` and `redirect_refused`.
- `is_answer` / `isAnswer` is **false** for `challenged`, whatever its status.
- The reason clause is exactly `the host asked for a robot check`.
- The body is matched over at most **65536** bytes (`CHALLENGE_BODY_PREFIX_BYTES`), lowercased in **ASCII only** (A–Z → a–z), decoded as UTF-8 with invalid bytes replaced.
- A 404 or 410 is **never** challenged; a body beginning `%PDF` is never challenged.
- Cloudflare's own refusal: status 403 or 503 **and** a `Server` header whose trimmed, ASCII-lowercased value is exactly `cloudflare` (header names compared case-insensitively).
- Detection applies only to the open-access PDF downloads: Python `PDFSourceType.UNPAYWALL_OA` and `OPENALEX_OA`; Swift `downloadAndCachePDF`; Kotlin `FullTextService.downloadPdf`. It does not apply to Elsevier, PMC, `DOI_DIRECT`, or the landing page.
- No impersonation: no User-Agent or header changes anywhere.
- Shared fixtures: add a row to the fixture, never a test on one platform alone. Every platform's marker table must equal the fixture's.
- Code style: Google-style docstrings and type hints (Python); no magic numbers (constants live in `constants.py`, `BioMedLitConstants`, `Constants.kt`); pure functions in focused modules.
- Gates before the PR: `pytest tests/`, `python .github/scripts/lint_delta.py --base-ref origin/master` (no new ruff/mypy findings), `cd Packages/BioMedLit && swift test`, `cd ios/MedicalFactChecker && swift test`, `xcodebuild -scheme MedicalFactChecker -destination 'platform=macOS' build` (from `ios/MedicalFactChecker/`), `cd android/MedicalFactChecker && ./gradlew test`.
- SwiftPM can hang 7–11 minutes behind `syspolicyd` on a new binary. Type-check first with `swiftc -typecheck`, then chain the real runs in one background job. Never run two Swift builds at once (they deadlock).

## Review Focus

Inputs the spec implies that are easy to miss; each has its test in the owning task.

1. **A challenge served as `application/pdf` with a 200.** It must be `challenged`, not `malformed_response` (Task 3, Task 4, Task 5).
2. **A stream that breaks while the longer prefix is read.** Python reads 4 bytes, then up to 64 KiB more; a transport error in that second read must not turn a 403 challenge into a connection failure. Classify what was read (Task 3).
3. **Binary that is not a PDF** (a JPEG "PDF" from `ars.els-cdn.com`, invalid UTF-8). No crash; not challenged unless a marker matches; still `malformed_response` (Task 2, Task 4, Task 5).
4. **A marker beyond 64 KiB, and one ending exactly at 64 KiB.** Unseen and seen respectively, identically on every platform (fixture cases, Tasks 2, 4, 5).
5. **A stored `challenged` entry read by this build, and by an older one.** It round-trips with its status. An older build reads it as `request_failed` (existing rule), so no crash (Task 1's persisted rows; the "read" rows already cover unknown kinds).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/bmlibrarian_lite/data_models.py` (modify) | `RequestFailureKind.CHALLENGED`, a status-carrying kind, and its reason clause |
| `src/bmlibrarian_lite/constants.py` (modify) | `CHALLENGE_BODY_PREFIX_BYTES`, `CLOUDFLARE_REFUSAL_STATUSES`, `CHALLENGE_GONE_STATUSES` |
| `src/bmlibrarian_lite/bot_walls.py` (create) | `WALL_MARKERS`, `named_defences`, `challenge_markers`, `challenge_failure`: pure |
| `src/bmlibrarian_lite/pdf_discovery.py` (modify) | `_try_download` reads a longer prefix of a non-PDF answer and records a challenge |
| `scripts/unpaywall_pdf_survey.py` (modify) | imports `WALL_MARKERS` / `named_defences` from `bot_walls` (one list, not two) |
| `doc/cross_platform/fulltext_parity/challenge_detection.json` (create) | the marker table, the bounds, and the cases |
| `doc/cross_platform/request_failure_parity/answered_lookup_verb.json` (modify) | `challenged` predicate rows |
| `doc/cross_platform/fulltext_parity/open_access_unsettled_notice.json` (modify) | `challenged` notice and persisted rows |
| `doc/cross_platform/search_failure_reporting.md`, `fulltext_retrieval.md` (modify) | the enum, the reason table, a "A challenged PDF (#480)" section |
| `Packages/BioMedLit/Sources/BioMedLit/Search/RetrievalShortfall.swift` (modify) | Swift kind, factory, clause |
| `Packages/BioMedLit/Sources/BioMedLit/Services/BotWalls.swift` (create) | Swift detector |
| `Packages/BioMedLit/Sources/BioMedLit/Utilities/Constants.swift` (modify) | Swift bounds |
| `Packages/BioMedLit/Sources/BioMedLit/Services/FullTextService.swift` (modify) | `downloadAndCachePDF` records a challenge |
| `android/.../domain/model/RetrievalShortfall.kt` (modify) | Kotlin kind, clause |
| `android/.../data/remote/fulltext/BotWalls.kt` (create) | Kotlin detector |
| `android/.../util/Constants.kt` (modify) | Kotlin bounds |
| `android/.../data/remote/fulltext/FullTextService.kt` (modify) | `downloadPdf` records a challenge |

`android/...` is `android/MedicalFactChecker/app/src/main/java/com/bmlibrarian/factchecker`; tests are under `android/MedicalFactChecker/app/src/test/java/com/bmlibrarian/factchecker`.

---

### Task 1: The `challenged` kind on all three platforms, in both shared contracts

The kind, its clause, and its rows land together: the Swift and Kotlin contract tests decode every fixture row and fail on an unknown kind, so no platform may lag.

**Files:**
- Modify: `src/bmlibrarian_lite/data_models.py:89-110` (enum), `:571` (`_STATUS_CODE_KINDS`), `:575-582` (`_REQUEST_FAILURE_REASONS`)
- Modify: `tests/test_retrieval_shortfall.py:73-85` (the raw-value set)
- Modify: `doc/cross_platform/request_failure_parity/answered_lookup_verb.json` (`predicate`)
- Modify: `doc/cross_platform/fulltext_parity/open_access_unsettled_notice.json` (`notices`, `persisted.written`, `persisted.read`)
- Modify: `doc/cross_platform/search_failure_reporting.md:93-103` (enum) and `:184-193` (reason table)
- Modify: `Packages/BioMedLit/Sources/BioMedLit/Search/RetrievalShortfall.swift:29-58` (enum, `carriesStatusCode`), add a factory near `:121-143`, `describe()` `:214-234`
- Modify: `android/.../domain/model/RetrievalShortfall.kt:39-66` (enum, `carriesStatusCode`), `:90-97` (`REQUEST_FAILURE_REASONS`)
- Modify (as the compiler and tests demand): Swift `OpenAccessShortfallContractTests.testEveryShortfallRoundTrips` (`:161`), Kotlin `OpenAccessShortfallContractTest` round-trip list (`:152-181`), Kotlin `RetrievalShortfallTest.kt:50-58` (literal kind → value map)

**Interfaces:**
- Produces (Python): `RequestFailureKind.CHALLENGED` (value `"challenged"`); `RequestFailure(RequestFailureKind.CHALLENGED, 403)` is valid; `.describe() == "the host asked for a robot check"`; `.is_answer is False`.
- Produces (Swift): `RequestFailureKind.challenged` (raw `"challenged"`), `carriesStatusCode == true`; `RequestFailure.challenged(statusCode: Int) -> RequestFailure`.
- Produces (Kotlin): `RequestFailureKind.CHALLENGED("challenged")`, `carriesStatusCode == true`; `RequestFailure(RequestFailureKind.CHALLENGED, 403)` is valid.

- [ ] **Step 1: Add the fixture rows (the failing tests on all three platforms)**

In `answered_lookup_verb.json`, append to `predicate` (after the `request_failed` row):

```json
{"kind": "challenged", "status_code": 200, "is_answer": false},
{"kind": "challenged", "status_code": 403, "is_answer": false},
{"kind": "challenged", "status_code": 503, "is_answer": false},
{"kind": "challenged", "status_code": null, "is_answer": false}
```

In `open_access_unsettled_notice.json`, append to `notices`:

```json
{"source": "unpaywall_pdf", "kind": "challenged", "status_code": 403, "notice": "The open-access copy's PDF (the host asked for a robot check) could not be asked, so a freely available copy may exist. Whether this document is open access was not established."},
{"source": "unpaywall_pdf", "kind": "challenged", "status_code": 200, "notice": "The open-access copy's PDF (the host asked for a robot check) could not be asked, so a freely available copy may exist. Whether this document is open access was not established."},
{"source": "openalex_pdf", "kind": "challenged", "status_code": 503, "notice": "OpenAlex's copy (the host asked for a robot check) could not be asked, so a freely available copy may exist. Whether this document is open access was not established."}
```

Append to `persisted.written`:

```json
{"source": "unpaywall_pdf", "kind": "challenged", "status_code": 403, "stored": {"schema_version": 1, "source": "unpaywall_pdf", "failure": {"kind": "challenged", "status_code": 403}}}
```

Append to `persisted.read` (its `stored` is a JSON *string*, as in the existing rows):

```json
{"name": "a challenged copy keeps its status", "stored": "{\"schema_version\":1,\"source\":\"openalex_pdf\",\"failure\":{\"kind\":\"challenged\",\"status_code\":200}}", "source": "openalex_pdf", "kind": "challenged", "status_code": 200}
```

Extend the file's `description` string: after "A row names a failed lookup by kind and status_code," add " (a `challenged` kind is a bot wall's challenge page served in place of the PDF, #480, with the status it came with)".

In `search_failure_reporting.md`, add `CHALLENGED = "challenged"  # a bot wall's challenge page in place of the answer (#480)` to the enum block, and the row `| \`challenged\` | \`"the host asked for a robot check"\` |` to the reason table.

- [ ] **Step 2: Run the three contract suites and see them fail**

```bash
pytest tests/test_answered_lookup_verb.py tests/test_open_access_unsettled_notice.py tests/test_retrieval_shortfall.py -q
```
Expected: FAIL (`'challenged' is not a valid RequestFailureKind` / set mismatch).

Kotlin: `cd android/MedicalFactChecker && ./gradlew testDebugUnitTest --tests '*AnsweredLookupVerbContractTest' --tests '*OpenAccessShortfallContractTest'`. Expected: FAIL (`unknown kind`).

Swift: deferred to Step 6 (a single chained run, see Global Constraints).

- [ ] **Step 3: Python kind**

`data_models.py`, in `RequestFailureKind` after `REQUEST_FAILED`:

```python
    # A bot wall's challenge page served in place of the answer: a defence
    # against programs, not the source's answer about the article (#480).
    # Carries the status the challenge came with (200, 403, 503 ...).
    CHALLENGED = "challenged"
```

`_STATUS_CODE_KINDS`:

```python
_STATUS_CODE_KINDS = (
    RequestFailureKind.HTTP_STATUS,
    RequestFailureKind.REDIRECT_REFUSED,
    RequestFailureKind.CHALLENGED,
)
```

`_REQUEST_FAILURE_REASONS`, add:

```python
    RequestFailureKind.CHALLENGED: "the host asked for a robot check",
```

Update the `RequestFailure` docstring's `status_code` attribute: "The HTTP status, for `HTTP_STATUS`, `REDIRECT_REFUSED` and `CHALLENGED`; …".

`tests/test_retrieval_shortfall.py`: add `"challenged",` to the raw-value set in `test_the_raw_values_are_the_contracts`. Add to `TestRequestFailureDescription`'s parametrised cases:

```python
            (RequestFailure(RequestFailureKind.CHALLENGED, 403), "the host asked for a robot check"),
```

and a test beside it:

```python
    def test_a_challenge_is_never_an_answer(self) -> None:
        """A wall's page says nothing about the article, whatever its status."""
        for status in (200, 403, 404, 503, None):
            assert RequestFailure(RequestFailureKind.CHALLENGED, status).is_answer is False

    def test_a_stored_challenge_keeps_its_status(self) -> None:
        """The status travels, as an HTTP status's does."""
        stored = RequestFailure(RequestFailureKind.CHALLENGED, 503).to_dict()
        assert RequestFailure.from_dict(stored) == RequestFailure(RequestFailureKind.CHALLENGED, 503)
```

- [ ] **Step 4: Swift kind**

`RetrievalShortfall.swift`, in the enum after `requestFailed`:

```swift
    /// A bot wall's challenge page in place of the answer (#480): a defence
    /// against programs, not the source's answer about the article. Carries
    /// the status the challenge came with.
    case challenged
```

`carriesStatusCode`:

```swift
        self == .httpStatus || self == .redirectRefused || self == .challenged
```

A factory next to `redirectRefused(statusCode:)`:

```swift
    /// A bot wall's challenge page, served with this status (#480).
    ///
    /// - Parameter statusCode: The status the challenge came with.
    /// - Returns: The failure, with the status kept when it is a three-digit HTTP status.
    public static func challenged(statusCode: Int) -> RequestFailure {
        RequestFailure(kind: .challenged, statusCode: checkedStatusCode(statusCode))
    }
```

`describe()`, add before `case .requestFailed:`:

```swift
        case .challenged:
            return "the host asked for a robot check"
```

Add `.challenged(statusCode: 403)` to the hard-coded failure list in `OpenAccessShortfallContractTests.testEveryShortfallRoundTrips`.

- [ ] **Step 5: Kotlin kind**

`RetrievalShortfall.kt`, replace `REQUEST_FAILED("request_failed");` with:

```kotlin
    /** Anything else. */
    REQUEST_FAILED("request_failed"),

    /**
     * A bot wall's challenge page in place of the answer (#480): a defence
     * against programs, not the source's answer about the article. Carries the
     * status the challenge came with.
     */
    CHALLENGED("challenged");
```

`carriesStatusCode`:

```kotlin
        get() = this == HTTP_STATUS || this == REDIRECT_REFUSED || this == CHALLENGED
```

`REQUEST_FAILURE_REASONS`, add:

```kotlin
    RequestFailureKind.CHALLENGED to "the host asked for a robot check",
```

Read `describe()` (`:170-177`): it handles `HTTP_STATUS` and `REDIRECT_REFUSED` by status, so `CHALLENGED` falls to the map, which is right. Add `RequestFailureKind.CHALLENGED to "challenged"` to `RetrievalShortfallTest.kt`'s literal map, and `RequestFailure(RequestFailureKind.CHALLENGED, 403)` to `OpenAccessShortfallContractTest`'s round-trip list.

- [ ] **Step 6: Run every suite**

```bash
pytest tests/test_answered_lookup_verb.py tests/test_open_access_unsettled_notice.py tests/test_retrieval_shortfall.py tests/test_unreachable_is_not_absent.py -q
cd android/MedicalFactChecker && ./gradlew testDebugUnitTest; cd -
cd Packages/BioMedLit && swiftc -typecheck $(find Sources -name '*.swift') 2>&1 | head -20; cd -
```

Then, in one background job: `cd Packages/BioMedLit && swift test 2>&1 | tail -30`.
Expected: all PASS. If a Swift `switch` elsewhere fails to compile for want of `.challenged`, add the case with the same meaning as `.malformedResponse` (a failure that is not an answer). The reason goes in a comment there.

- [ ] **Step 7: Commit**

```bash
git add src/bmlibrarian_lite/data_models.py tests/test_retrieval_shortfall.py doc/cross_platform Packages/BioMedLit android/MedicalFactChecker/app/src
git commit -m "feat(fulltext): the challenged failure kind on all three platforms (#480)"
```

---

### Task 2: Python detector and the shared detection fixture

**Files:**
- Create: `src/bmlibrarian_lite/bot_walls.py`
- Modify: `src/bmlibrarian_lite/constants.py` (beside `PDF_MAGIC_BYTES`, `:1255`)
- Create: `doc/cross_platform/fulltext_parity/challenge_detection.json`
- Create: `tests/test_bot_walls.py`
- Modify: `scripts/unpaywall_pdf_survey.py:177-253` (import the table and matcher)

**Interfaces:**
- Consumes: `RequestFailureKind.CHALLENGED` (Task 1).
- Produces:
  - `WALL_MARKERS: dict[str, tuple[str, ...]]`
  - `named_defences(text: str, headers: Mapping[str, str] | None = None) -> tuple[str, ...]` (sorted names; ASCII-lowercases its inputs)
  - `challenge_markers(status: int, headers: Mapping[str, str], body_prefix: bytes) -> tuple[str, ...]`
  - `challenge_failure(status: int, headers: Mapping[str, str], body_prefix: bytes) -> RequestFailure | None`

- [ ] **Step 1: Write the fixture**

`doc/cross_platform/fulltext_parity/challenge_detection.json`:

```json
{
  "_readme": [
    "Which answers are a bot wall's challenge page (#480). Read by tests/test_bot_walls.py",
    "(Python), BotWallsContractTests.swift (BioMedLit) and BotWallsContractTest.kt (Android).",
    "markers: the defence names and the needles each is recognised by; every platform's",
    "table must equal it. A needle matches the body, ASCII-lowercased and decoded as UTF-8",
    "with invalid bytes replaced, or a header line 'name: value', ASCII-lowercased.",
    "body_prefix_bytes: only this many bytes of the body are read. gone_statuses: never",
    "challenged. cloudflare_refusal_statuses: with a Server header that trims and",
    "lowercases to 'cloudflare', a challenge whatever the page says. A body beginning",
    "%PDF is never challenged. cases: body_padding_bytes spaces are put before body.",
    "expected: the sorted marker names; empty means not challenged. Rules:",
    "doc/cross_platform/fulltext_retrieval.md, 'A challenged PDF (#480)'.",
    "Sources: doc/developer/unpaywall_pdf_survey/. Add a row here, not a test on one platform."
  ],
  "body_prefix_bytes": 65536,
  "gone_statuses": [404, 410],
  "cloudflare_refusal_statuses": [403, 503],
  "markers": {
    "akamai": ["akamaighost", "errors.edgesuite.net", "you don't have permission to access"],
    "anubis": ["anubis", "making sure you're not a bot"],
    "aws-waf": ["awswaf", "aws-waf-token", "x-amzn-waf-action"],
    "captcha": ["g-recaptcha", "hcaptcha", "turnstile", "captcha"],
    "cloudflare": ["just a moment", "cf-chl", "challenge-platform", "cf-mitigated", "attention required! | cloudflare"],
    "datadome": ["datadome", "captcha-delivery.com"],
    "imperva": ["incapsula", "_incapsula_resource", "visid_incap"],
    "perimeterx": ["px-captcha", "perimeterx", "_pxhd"],
    "proof-of-work": ["proof of work", "pow-challenge", "checking your browser", "preparing to download"],
    "robot-text": ["verify you are human", "are you a robot", "unusual traffic", "not a robot", "automated access", "bot detection", "ai crawlers", "hostile web agent"]
  },
  "cases": [
    {"name": "cloudflare interstitial", "status": 403, "headers": {"Server": "cloudflare"}, "body": "<!DOCTYPE html><html><head><title>Just a moment...</title>", "expected": ["cloudflare"]},
    {"name": "ScienceDirect 403: markup past the prefix", "status": 403, "headers": {"server": "cloudflare"}, "body": "<html><head><style>body{font-size:72px}</style></head>", "expected": ["cloudflare"]},
    {"name": "cloudflare 503, empty body", "status": 503, "headers": {"Server": "cloudflare"}, "body": "", "expected": ["cloudflare"]},
    {"name": "server header in another case, padded", "status": 403, "headers": {"SERVER": " Cloudflare "}, "body": "", "expected": ["cloudflare"]},
    {"name": "control: a 200 cloudflare merely relays", "status": 200, "headers": {"Server": "cloudflare"}, "body": "<html><title>Abstract</title></html>", "expected": []},
    {"name": "control: an ordinary 403", "status": 403, "headers": {"Server": "nginx"}, "body": "<html>Forbidden</html>", "expected": []},
    {"name": "control: a 401 cloudflare serves", "status": 401, "headers": {"Server": "cloudflare"}, "body": "<html>Unauthorized</html>", "expected": []},
    {"name": "PMC proof of work, served as a 200", "status": 200, "headers": {}, "body": "<html><head><title>Preparing to download ...</title>", "expected": ["proof-of-work"]},
    {"name": "PMC reCAPTCHA", "status": 200, "headers": {}, "body": "<div class=\"g-recaptcha\"></div><p>Checking your browser</p>", "expected": ["captcha", "proof-of-work"]},
    {"name": "Thieme's AI-crawler page", "status": 200, "headers": {}, "body": "Please wait... protecting Thieme-Connect from AI crawlers", "expected": ["robot-text"]},
    {"name": "Akamai refusal", "status": 403, "headers": {"Server": "AkamaiGHost"}, "body": "<H1>Access Denied</H1>You don't have permission to access", "expected": ["akamai"]},
    {"name": "a header name alone", "status": 403, "headers": {"cf-mitigated": "challenge"}, "body": "", "expected": ["cloudflare"]},
    {"name": "AWS WAF header", "status": 202, "headers": {"x-amzn-waf-action": "challenge"}, "body": "", "expected": ["aws-waf"]},
    {"name": "DataDome", "status": 403, "headers": {}, "body": "<script src=\"https://ct.captcha-delivery.com/c.js\"></script>", "expected": ["captcha", "datadome"]},
    {"name": "Imperva", "status": 200, "headers": {}, "body": "<script src=\"/_Incapsula_Resource?x=1\"></script>", "expected": ["imperva"]},
    {"name": "PerimeterX", "status": 403, "headers": {}, "body": "<div id=\"px-captcha\"></div>", "expected": ["captcha", "perimeterx"]},
    {"name": "Anubis", "status": 200, "headers": {}, "body": "<h1>Making sure you're not a bot!</h1>", "expected": ["anubis"]},
    {"name": "upper-case robot text", "status": 200, "headers": {}, "body": "PLEASE VERIFY YOU ARE HUMAN", "expected": ["robot-text"]},
    {"name": "control: a 404 with challenge markup is gone", "status": 404, "headers": {"Server": "cloudflare"}, "body": "Just a moment...", "expected": []},
    {"name": "control: a 410 with challenge markup is gone", "status": 410, "headers": {}, "body": "captcha", "expected": []},
    {"name": "control: a PDF that mentions a captcha", "status": 200, "headers": {}, "body": "%PDF-1.7 captcha study", "expected": []},
    {"name": "control: an abstract page", "status": 200, "headers": {"Content-Type": "text/html"}, "body": "<html><title>Effect of aspirin</title><p>Abstract</p></html>", "expected": []},
    {"name": "a marker ending exactly at the prefix's end", "status": 200, "headers": {}, "body_padding_bytes": 65529, "body": "captcha", "expected": ["captcha"]},
    {"name": "a marker beyond the prefix", "status": 200, "headers": {}, "body_padding_bytes": 65536, "body": "captcha", "expected": []}
  ]
}
```

(65529 + 7 bytes of `captcha` = 65536: the last needle byte is the prefix's last byte.)

- [ ] **Step 2: Write the failing tests**

`tests/test_bot_walls.py`:

```python
# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A bot wall's challenge page, recognised from the answer alone (#480).

The rows are the shared contract,
``doc/cross_platform/fulltext_parity/challenge_detection.json``; the Swift and
Android ports read the same rows.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from bmlibrarian_lite.bot_walls import (
    WALL_MARKERS,
    challenge_failure,
    challenge_markers,
)
from bmlibrarian_lite.constants import (
    CHALLENGE_BODY_PREFIX_BYTES,
    CHALLENGE_GONE_STATUSES,
    CLOUDFLARE_REFUSAL_STATUSES,
)
from bmlibrarian_lite.data_models import RequestFailure, RequestFailureKind

CONTRACT: dict[str, Any] = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "doc"
        / "cross_platform"
        / "fulltext_parity"
        / "challenge_detection.json"
    ).read_text(encoding="utf-8")
)


def _body(case: dict[str, Any]) -> bytes:
    """The case's body, after its padding."""
    return b" " * case.get("body_padding_bytes", 0) + case["body"].encode("utf-8")


def test_the_marker_table_is_the_contracts() -> None:
    """One table on every platform."""
    assert {name: list(needles) for name, needles in WALL_MARKERS.items()} == CONTRACT["markers"]


def test_the_bounds_are_the_contracts() -> None:
    """The prefix, the gone statuses and Cloudflare's refusals."""
    assert CHALLENGE_BODY_PREFIX_BYTES == CONTRACT["body_prefix_bytes"]
    assert set(CHALLENGE_GONE_STATUSES) == set(CONTRACT["gone_statuses"])
    assert set(CLOUDFLARE_REFUSAL_STATUSES) == set(CONTRACT["cloudflare_refusal_statuses"])


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda c: c["name"])
def test_each_case(case: dict[str, Any]) -> None:
    """Each row's markers, in sorted order."""
    assert list(challenge_markers(case["status"], case["headers"], _body(case))) == case["expected"]


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda c: c["name"])
def test_a_challenge_is_recorded_with_its_status(case: dict[str, Any]) -> None:
    """Challenged rows become the kind, with the status; the rest none."""
    failure = challenge_failure(case["status"], case["headers"], _body(case))
    if case["expected"]:
        assert failure == RequestFailure(RequestFailureKind.CHALLENGED, case["status"])
    else:
        assert failure is None


def test_binary_that_is_not_a_pdf_is_no_challenge() -> None:
    """A JPEG listed as a PDF, invalid UTF-8 throughout (Review Focus 3)."""
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes(range(128, 256)) * 8
    assert challenge_markers(200, {}, jpeg) == ()


def test_lowercasing_is_ascii_only() -> None:
    """str.lower() turns the Kelvin sign into 'k', which would match; no platform does."""
    assert challenge_markers(200, {}, "A\u212aAMAIGHOST".encode()) == ()
```

- [ ] **Step 3: Run them and see them fail**

Run: `pytest tests/test_bot_walls.py -q`
Expected: FAIL (`ModuleNotFoundError: bmlibrarian_lite.bot_walls`).

- [ ] **Step 4: Constants**

`constants.py`, after `PDF_MAGIC_BYTES`:

```python
# Bot walls (#480): how much of a non-PDF answer is read to recognise a
# challenge page (a bounded read of a lookup page, never research content),
# the statuses that say the file is gone whatever the page shows, and the
# statuses Cloudflare answers a challenge or a block with. The shared contract
# is doc/cross_platform/fulltext_parity/challenge_detection.json.
CHALLENGE_BODY_PREFIX_BYTES = 64 * 1024
CHALLENGE_GONE_STATUSES = frozenset({404, 410})
CLOUDFLARE_REFUSAL_STATUSES = frozenset({403, 503})
CLOUDFLARE_SERVER = "cloudflare"
```

- [ ] **Step 5: The detector**

`src/bmlibrarian_lite/bot_walls.py`:

```python
# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Recognise a bot wall's challenge page from an answer alone (#480).

A source that names an open-access PDF can be walled against programs: the
answer is a challenge page (a 200, or a 403/503 from Cloudflare) in place of
the PDF. That says nothing about the article, so it is recorded as its own
kind, ``CHALLENGED``, never as the host's answer. The rules and the shared
cases are ``doc/cross_platform/fulltext_parity/challenge_detection.json``;
the survey that measured them is ``doc/developer/unpaywall_pdf_survey/``.

Pure functions only: no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping

from .constants import (
    CHALLENGE_BODY_PREFIX_BYTES,
    CHALLENGE_GONE_STATUSES,
    CLOUDFLARE_REFUSAL_STATUSES,
    CLOUDFLARE_SERVER,
    PDF_MAGIC_BYTES,
)
from .data_models import RequestFailure, RequestFailureKind

__all__ = [
    "WALL_MARKERS",
    "challenge_failure",
    "challenge_markers",
    "named_defences",
]

#: The defences against programs, by name, and the lowercase needles each is
#: recognised by in a page's start or a header line. Equal to the contract's
#: ``markers`` table on every platform.
WALL_MARKERS: dict[str, tuple[str, ...]] = {
    "akamai": ("akamaighost", "errors.edgesuite.net", "you don't have permission to access"),
    "anubis": ("anubis", "making sure you're not a bot"),
    "aws-waf": ("awswaf", "aws-waf-token", "x-amzn-waf-action"),
    "captcha": ("g-recaptcha", "hcaptcha", "turnstile", "captcha"),
    "cloudflare": (
        "just a moment",
        "cf-chl",
        "challenge-platform",
        "cf-mitigated",
        "attention required! | cloudflare",
    ),
    "datadome": ("datadome", "captcha-delivery.com"),
    "imperva": ("incapsula", "_incapsula_resource", "visid_incap"),
    "perimeterx": ("px-captcha", "perimeterx", "_pxhd"),
    "proof-of-work": ("proof of work", "pow-challenge", "checking your browser", "preparing to download"),
    "robot-text": (
        "verify you are human",
        "are you a robot",
        "unusual traffic",
        "not a robot",
        "automated access",
        "bot detection",
        "ai crawlers",
        "hostile web agent",
    ),
}

# A-Z to a-z and nothing else: str.lower() also folds non-ASCII letters (the
# Kelvin sign to "k"), which Swift's and Kotlin's ports would not match.
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _ascii_lower(text: str) -> str:
    """Lowercase ASCII letters only."""
    return text.translate(_ASCII_LOWER)


def named_defences(text: str, headers: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The defences a page's text and its header lines name.

    Args:
        text: The start of a non-PDF body, any case.
        headers: The answer's headers, when there are any; each is matched as
            the line ``name: value``.

    Returns:
        The sorted names in :data:`WALL_MARKERS` that matched.
    """
    haystacks = [_ascii_lower(text)]
    if headers:
        haystacks.extend(_ascii_lower(f"{name}: {value}") for name, value in headers.items())
    return tuple(sorted(
        name
        for name, needles in WALL_MARKERS.items()
        if any(needle in haystack for haystack in haystacks for needle in needles)
    ))


def _server(headers: Mapping[str, str]) -> str:
    """The ``Server`` header, trimmed and ASCII-lowercased; ``""`` without one."""
    for name, value in headers.items():
        if _ascii_lower(name) == "server":
            return _ascii_lower(value.strip())
    return ""


def challenge_markers(
    status: int, headers: Mapping[str, str], body_prefix: bytes
) -> tuple[str, ...]:
    """The defences an answer names, if it is a challenge page.

    Args:
        status: The answer's HTTP status.
        headers: The answer's headers.
        body_prefix: The start of its body; only the first
            :data:`~bmlibrarian_lite.constants.CHALLENGE_BODY_PREFIX_BYTES`
            are read.

    Returns:
        The sorted defence names; empty when the answer is not a challenge:
        a gone status (404, 410), a body that is a PDF, or nothing named.
    """
    if status in CHALLENGE_GONE_STATUSES:
        return ()
    body = body_prefix[:CHALLENGE_BODY_PREFIX_BYTES]
    if body.startswith(PDF_MAGIC_BYTES):
        return ()
    found = set(named_defences(body.decode("utf-8", errors="replace"), headers))
    if status in CLOUDFLARE_REFUSAL_STATUSES and _server(headers) == CLOUDFLARE_SERVER:
        # Cloudflare's own refusal, whatever the page says: ScienceDirect's
        # challenge markup lies beyond the bytes read.
        found.add("cloudflare")
    return tuple(sorted(found))


def challenge_failure(
    status: int, headers: Mapping[str, str], body_prefix: bytes
) -> RequestFailure | None:
    """The failure to record for a challenge page, or ``None`` for any other answer.

    Args:
        status: The answer's HTTP status.
        headers: The answer's headers.
        body_prefix: The start of its body.

    Returns:
        ``RequestFailure(CHALLENGED, status)`` when :func:`challenge_markers`
        names a defence; ``None`` otherwise.
    """
    if not challenge_markers(status, headers, body_prefix):
        return None
    return RequestFailure(RequestFailureKind.CHALLENGED, status)
```

Note that the `"server: cloudflare"` header *line* matches no needle by itself. Only the status rule adds `cloudflare` for it, which is why the "a 200 cloudflare merely relays" control is empty.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_bot_walls.py -q`
Expected: PASS.

- [ ] **Step 7: The survey reads the same table**

In `scripts/unpaywall_pdf_survey.py`, delete the `WALL_MARKERS` dict (`:175-200`, with its comment) and the body of `wall_markers` (`:238-253`). Add to the imports: `from bmlibrarian_lite.bot_walls import WALL_MARKERS, named_defences`. Keep `wall_markers` as:

```python
def wall_markers(text: str, headers: Mapping[str, str] | None = None) -> list[str]:
    """The defences named in a page's start and its headers.

    The table and the matching are the app's own (``bmlibrarian_lite.bot_walls``),
    so the survey measures exactly what the app recognises.

    Args:
        text: The start of a non-PDF body, any case.
        headers: The answer's headers, when there are any.

    Returns:
        The sorted names in ``WALL_MARKERS`` that matched.
    """
    return list(named_defences(text, headers))
```

`WALL_MARKERS` stays importable from the script (other code in it refers to it by that name). Run: `pytest tests/test_unpaywall_pdf_survey.py -q`. Expected: PASS. The figures it pins must not move. If one does, stop and report it: the ASCII-only lowercasing changed a row.

- [ ] **Step 8: Commit**

```bash
git add src/bmlibrarian_lite/bot_walls.py src/bmlibrarian_lite/constants.py tests/test_bot_walls.py doc/cross_platform/fulltext_parity/challenge_detection.json scripts/unpaywall_pdf_survey.py
git commit -m "feat(fulltext): recognise a bot wall's challenge page (#480)"
```

---

### Task 3: Python records a challenged open-access PDF

**Files:**
- Modify: `src/bmlibrarian_lite/pdf_discovery.py`: `_try_download` (`:2023-2178`)
- Create: `tests/test_challenged_pdf_download.py`

**Interfaces:**
- Consumes: `challenge_failure(status, headers, body_prefix)` (Task 2); `CHALLENGE_BODY_PREFIX_BYTES` (Task 2); `_UNOBTAINED_PDF_SERVICE` (existing, `pdf_discovery.py:610`).
- Produces: `DiscoveryResult.failure == RequestFailure(CHALLENGED, status)` for an Unpaywall or OpenAlex PDF answered with a challenge page. Control flow (`is_paywall`, which branch returns) is unchanged; only the recorded kind changes.

- [ ] **Step 1: Write the failing tests**

`tests/test_challenged_pdf_download.py`:

```python
# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""An open-access PDF met by a bot wall is recorded as challenged (#480).

Only the recorded kind changes: which branch of ``_try_download`` answers,
and whether it is a paywall, stay as they were.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
import requests

from bmlibrarian_lite.data_models import RequestFailure, RequestFailureKind
from bmlibrarian_lite.pdf_discovery import PDFDiscoverer, PDFSource, PDFSourceType

URL = "https://walled.example.org/article.pdf"


class _Response:
    """A streamed answer: status, headers, and the body in chunks.

    Attributes:
        status_code: The HTTP status.
        headers: The response headers.
        url: The URL the answer came from.
        is_redirect: Never a redirect.
    """

    is_redirect = False

    def __init__(
        self,
        status_code: int,
        headers: dict[str, str],
        chunks: list[bytes],
        break_after: int | None = None,
    ) -> None:
        """Answer with these chunks, optionally breaking after some.

        Args:
            status_code: The HTTP status.
            headers: The headers.
            chunks: The body, chunk by chunk.
            break_after: Raise ``ChunkedEncodingError`` after this many chunks.
        """
        self.status_code = status_code
        self.headers = headers
        self.url = URL
        self._chunks = chunks
        self._break_after = break_after

    def iter_content(self, chunk_size: int = 8192) -> Iterator[bytes]:
        """Yield the chunks, breaking where scripted."""
        for index, chunk in enumerate(self._chunks):
            if self._break_after is not None and index >= self._break_after:
                raise requests.exceptions.ChunkedEncodingError("broken")
            yield chunk

    def raise_for_status(self) -> None:
        """Raise for an error status, as ``requests`` does."""
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)


class _Session:
    """Answers every GET with one response."""

    def __init__(self, response: _Response) -> None:
        self._response = response

    def get(self, url: str, **kwargs: object) -> _Response:
        """The scripted response."""
        return self._response


@pytest.fixture
def discovery() -> PDFDiscoverer:
    """A discoverer with no network."""
    return PDFDiscoverer(unpaywall_email="test@example.org")


def _download(
    discovery: PDFDiscoverer,
    tmp_path: Path,
    response: _Response,
    source_type: PDFSourceType = PDFSourceType.UNPAYWALL_OA,
):
    discovery._session = _Session(response)  # type: ignore[assignment]
    source = PDFSource(url=URL, source_type=source_type, is_open_access=True)
    return discovery._try_download(source, tmp_path / "out.pdf", None)


def test_a_cloudflare_403_is_challenged(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """ScienceDirect's wall: its markup lies past what is read."""
    result = _download(
        discovery, tmp_path, _Response(403, {"Server": "cloudflare"}, [b"<html><style>"])
    )
    assert result.failure == RequestFailure(RequestFailureKind.CHALLENGED, 403)
    assert result.is_paywall is True  # the branch it took is unchanged


def test_a_cloudflare_503_is_challenged(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """A challenge with an outage's status is not an outage."""
    result = _download(
        discovery, tmp_path, _Response(503, {"Server": "cloudflare"}, [b"Just a moment..."])
    )
    assert result.failure == RequestFailure(RequestFailureKind.CHALLENGED, 503)


def test_a_challenge_served_as_a_pdf_is_challenged(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """Review Focus 1: a 200 labelled application/pdf whose body is a challenge."""
    result = _download(
        discovery,
        tmp_path,
        _Response(200, {"Content-Type": "application/pdf"}, [b"<html>", b"<title>Preparing to download"]),
    )
    assert result.failure == RequestFailure(RequestFailureKind.CHALLENGED, 200)
    assert not (tmp_path / "out.pdf").exists()


def test_a_marker_past_the_first_chunk_is_read(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """The prefix is read past the four bytes the PDF check needs."""
    chunks = [b"<htm", b"l>" + b" " * 9000, b"g-recaptcha"]
    result = _download(discovery, tmp_path, _Response(200, {}, chunks))
    assert result.failure == RequestFailure(RequestFailureKind.CHALLENGED, 200)


def test_a_stream_breaking_in_the_longer_read_keeps_what_was_read(
    discovery: PDFDiscoverer, tmp_path: Path
) -> None:
    """Review Focus 2: the challenge already seen is not lost to a broken stream."""
    chunks = [b"<htm", b"l>Just a moment...", b"never read"]
    result = _download(
        discovery, tmp_path, _Response(403, {"Server": "nginx"}, chunks, break_after=2)
    )
    assert result.failure == RequestFailure(RequestFailureKind.CHALLENGED, 403)


def test_control_an_ordinary_403_keeps_its_status(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """No marker: the refusal is still the host's answer."""
    result = _download(discovery, tmp_path, _Response(403, {"Server": "nginx"}, [b"<html>Forbidden</html>"]))
    assert result.failure == RequestFailure(RequestFailureKind.HTTP_STATUS, 403)


def test_control_a_404_with_challenge_markup_is_gone(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """Gone is gone, whatever the page says."""
    result = _download(discovery, tmp_path, _Response(404, {"Server": "cloudflare"}, [b"Just a moment..."]))
    assert result.failure == RequestFailure(RequestFailureKind.HTTP_STATUS, 404)


def test_control_a_non_pdf_without_markers_is_malformed(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """Review Focus 3: a JPEG listed as a PDF."""
    result = _download(discovery, tmp_path, _Response(200, {}, [b"\xff\xd8\xff\xe0" + bytes(range(128, 256))]))
    assert result.failure == RequestFailure(RequestFailureKind.MALFORMED_RESPONSE)


def test_control_a_pdf_is_saved_whole(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """The longer read never runs for a PDF, and the file keeps its header."""
    body = [b"%PDF", b"-1.7 rest of the file"]
    result = _download(discovery, tmp_path, _Response(200, {"Content-Type": "application/pdf"}, body))
    assert result.success is True
    assert (tmp_path / "out.pdf").read_bytes() == b"".join(body)


def test_control_other_sources_keep_their_reporting(discovery: PDFDiscoverer, tmp_path: Path) -> None:
    """Only the open-access copies are recognised: a DOI download keeps its 403."""
    result = _download(
        discovery,
        tmp_path,
        _Response(403, {"Server": "cloudflare"}, [b"Just a moment..."]),
        source_type=PDFSourceType.DOI_DIRECT,
    )
    assert result.failure == RequestFailure(RequestFailureKind.HTTP_STATUS, 403)
```

Check the `PDFDiscoverer` constructor and `PDFSource` fields before running: copy the `discovery` fixture from `tests/test_pdf_discovery_publisher_patterns.py:39-45` if its arguments differ, and drop `is_open_access=True` if `PDFSource` has no such field.

- [ ] **Step 2: Run them and see them fail**

Run: `pytest tests/test_challenged_pdf_download.py -q`
Expected: the five non-control tests FAIL (`HTTP_STATUS 403` / `MALFORMED_RESPONSE` where `CHALLENGED` was expected); the controls PASS.

- [ ] **Step 3: Implement**

In `pdf_discovery.py`, import `challenge_failure` from `.bot_walls` and `CHALLENGE_BODY_PREFIX_BYTES` from `.constants`. Add a module-level helper beside `refused_download_failure`:

```python
def read_more_of_prefix(chunks: Iterator[bytes], prefix: bytes, up_to: int) -> bytes:
    """Read on from an answer's prefix, as far as ``up_to`` bytes in all.

    For a non-PDF answer, whose page is read to recognise a challenge
    (#480). A stream that breaks here ends the read: what was already
    read still decides, so a challenge page is not lost to its own
    connection closing.

    Args:
        chunks: The rest of the body.
        prefix: What was read already.
        up_to: The most bytes to hold in all.

    Returns:
        ``prefix`` and what followed it, up to ``up_to`` bytes.
    """
    more = bytearray(prefix)
    try:
        for chunk in chunks:
            more += chunk
            if len(more) >= up_to:
                break
    except requests.exceptions.RequestException:
        logger.debug("The answer's stream broke while its page was read")
    return bytes(more[:up_to])
```

In `_try_download`, right after `body_prefix = read_body_prefix(content_iter, len(PDF_MAGIC_BYTES))`:

```python
            # An open-access copy whose answer is not a PDF is read further,
            # to recognise a bot wall's challenge page: that is no answer
            # about the article, and is recorded as its own kind (#480).
            challenge = None
            if (
                source.source_type in _UNOBTAINED_PDF_SERVICE
                and not self._looks_like_pdf(body_prefix)
            ):
                body_prefix = read_more_of_prefix(
                    content_iter, body_prefix, CHALLENGE_BODY_PREFIX_BYTES
                )
                challenge = challenge_failure(
                    response.status_code, response.headers, body_prefix
                )
```

Then change the three failure sites:

1. Before `response.raise_for_status()` in the `>= HTTP_ERROR_STATUS_MIN and not in PAYWALL_HTTP_STATUSES` block:

```python
            if (
                response.status_code >= HTTP_ERROR_STATUS_MIN
                and response.status_code not in PAYWALL_HTTP_STATUSES
            ):
                if challenge is not None:
                    return DiscoveryResult(
                        success=False,
                        error=challenge.describe(),
                        failure=challenge,
                    )
                response.raise_for_status()
```

2. The paywall branch: `failure=challenge or refused_download_failure(response.status_code),`
3. The non-PDF branch: `failure=challenge or RequestFailure(RequestFailureKind.MALFORMED_RESPONSE),`

`_UNOBTAINED_PDF_SERVICE` is defined above `PDFDiscoverer` (`:610`), so it is in scope. `Iterator` may already be imported in `pdf_discovery.py`; add `from collections.abc import Iterator` if not.

- [ ] **Step 4: Run the tests**

```bash
pytest tests/test_challenged_pdf_download.py tests/test_pdf_discovery_publisher_patterns.py tests/test_unobtained_unpaywall_pdf.py tests/test_openalex_discovery.py -q
```
Expected: PASS.

- [ ] **Step 5: The record reaches the reader's sentence (end to end)**

Add to `tests/test_challenged_pdf_download.py`:

```python
def test_a_challenged_copy_reads_as_could_not_be_asked() -> None:
    """The record a challenged download leaves is told as the contract words it."""
    from bmlibrarian_lite.analysis_failures import unestablished_access_clause
    from bmlibrarian_lite.pdf_discovery import DiscoveryResult, unobtained_open_access_pdf

    source = PDFSource(url=URL, source_type=PDFSourceType.UNPAYWALL_OA)
    result = DiscoveryResult(
        success=False, failure=RequestFailure(RequestFailureKind.CHALLENGED, 403)
    )
    text = unestablished_access_clause(unobtained_open_access_pdf(source, result))
    assert "walled.example.org" in text
    assert "the host asked for a robot check" in text
    assert "could not be asked" in text
```

Run it. Expected: PASS without further code: the sentence builders already pick "could not be asked" for any non-answer.

- [ ] **Step 6: Run the full Python gate**

```bash
pytest tests/ -q -x
python .github/scripts/lint_delta.py --base-ref origin/master
```
Expected: all pass; no new ruff or mypy findings.

- [ ] **Step 7: Commit**

```bash
git add src/bmlibrarian_lite/pdf_discovery.py tests/test_challenged_pdf_download.py
git commit -m "feat(fulltext): an open-access PDF behind a bot wall is recorded as challenged (#480)"
```

---

### Task 4: Swift detector and download

**Files:**
- Create: `Packages/BioMedLit/Sources/BioMedLit/Services/BotWalls.swift`
- Modify: `Packages/BioMedLit/Sources/BioMedLit/Utilities/Constants.swift` (beside `pdfMagicBytes`, `:479`)
- Modify: `Packages/BioMedLit/Sources/BioMedLit/Services/FullTextService.swift`: `downloadAndCachePDF` (`:2671-2684`)
- Create: `Packages/BioMedLit/Tests/BioMedLitTests/BotWallsContractTests.swift`
- Modify: `Packages/BioMedLit/Tests/BioMedLitTests/FullTextServiceUnpaywallLocationsTests.swift` (add download cases)

**Interfaces:**
- Consumes: `RequestFailure.challenged(statusCode:)` (Task 1); the fixture (Task 2).
- Produces: `BotWalls.wallMarkers: [String: [String]]`; `BotWalls.challengeMarkers(status: Int, headers: [String: String], body: Data) -> [String]`; `BotWalls.challengeFailure(status: Int, headers: [String: String], body: Data) -> RequestFailure?`.

- [ ] **Step 1: Write the failing contract test**

`BotWallsContractTests.swift` reads `doc/cross_platform/fulltext_parity/challenge_detection.json`. Use the same locator as `OpenAccessShortfallContractTests.contractFile` (`:32-47`): walk up from `#filePath`, stop at `.git`. It asserts:

```swift
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
```

If `RequestFailure` is not `Equatable`, compare `failure?.kind` and `failure?.statusCode` instead.

- [ ] **Step 2: Constants and the detector**

`Constants.swift`, beside `pdfMagicBytes`:

```swift
    /// Bot walls (#480): how much of a non-PDF answer is read to recognise a
    /// challenge page, the statuses that say the file is gone whatever the
    /// page shows, and the statuses Cloudflare answers a challenge with.
    /// Contract: `doc/cross_platform/fulltext_parity/challenge_detection.json`.
    public static let challengeBodyPrefixBytes = 64 * 1024
    public static let challengeGoneStatuses: Set<Int> = [404, 410]
    public static let cloudflareRefusalStatuses: Set<Int> = [403, 503]
    public static let cloudflareServer = "cloudflare"
```

`BotWalls.swift`:

```swift
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
        let haystacks = [asciiLowercased(text)] + headers.map { asciiLowercased("\($0.key): \($0.value)") }
        return wallMarkers.compactMap { name, needles in
            needles.contains { needle in haystacks.contains { $0.contains(needle) } } ? name : nil
        }.sorted()
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
```

`String.contains(_:)` on a `String` argument requires Foundation (imported). `trimmingCharacters(in: .whitespaces)` trims only spaces and tabs; Python's `strip()` also trims newlines, and a header value holds none, so the two agree.

- [ ] **Step 3: The download records it**

In `downloadAndCachePDF`, replace the status guard and the magic guard (`:2674-2684`):

```swift
        let headers = Dictionary(
            httpResponse.allHeaderFields.compactMap { key, value in
                (key as? String).map { ($0, "\(value)") }
            },
            uniquingKeysWith: { first, _ in first }
        )
        let pdfMagic = Data(BioMedLitConstants.pdfMagicBytes)
        let isPDF = data.count > pdfMagic.count && data.prefix(pdfMagic.count) == pdfMagic
        // A bot wall's challenge page in place of the PDF is no answer about
        // the article (#480), whatever status it came with.
        if !isPDF, let challenge = BotWalls.challengeFailure(
            status: httpResponse.statusCode, headers: headers, body: data
        ) {
            throw FullTextError.pdfDownloadFailed(challenge)
        }
        guard httpResponse.statusCode == BioMedLitConstants.httpStatusOK else {
            throw FullTextError.pdfDownloadFailed(.httpStatus(httpResponse.statusCode))
        }
        // A login page served with a 200 stops here (#480).
        guard isPDF else {
            throw FullTextError.pdfDownloadFailed(.malformedResponse)
        }
```

`downloadAndCachePDF` also serves Europe PMC's render tier, whose failures are not recorded as a shortfall (`pdfTierResult`), so this changes nothing a reader sees there.

- [ ] **Step 4: Download tests**

In `FullTextServiceUnpaywallLocationsTests.swift`, find the test that refuses a location with a 403 (`testEveryLocationRefusedIsToldEachByItsHost`, `:132`) and copy its `StubURLProtocol` set-up into three new tests:

- the PDF URL answers 403 with `Server: cloudflare` and body `<html>`: the stored `OpenAccessShortfall` entry's failure is `.challenged(statusCode: 403)`;
- it answers 200 with `Content-Type: application/pdf` and body `<title>Preparing to download`: `.challenged(statusCode: 200)`, and no PDF is cached (Review Focus 1);
- a control, 403 with `Server: nginx` and body `<html>Forbidden</html>`: `.httpStatus(403)`.

Assert the failure on the entry the existing test reads, in the same way it reads it.

- [ ] **Step 5: Run the Swift gate**

```bash
cd Packages/BioMedLit && swiftc -typecheck $(find Sources -name '*.swift') 2>&1 | head -20
```
Then one background job: `cd Packages/BioMedLit && swift test 2>&1 | tail -40; cd ../../ios/MedicalFactChecker && swift test 2>&1 | tail -20`.
Expected: PASS on both.

- [ ] **Step 6: Commit**

```bash
git add Packages/BioMedLit
git commit -m "feat(BioMedLit): recognise and record a challenged open-access PDF (#480)"
```

---

### Task 5: Kotlin detector and download

**Files:**
- Create: `android/.../data/remote/fulltext/BotWalls.kt`
- Modify: `android/.../util/Constants.kt` (beside `PDF_MAGIC_BYTES`, `:623`)
- Modify: `android/.../data/remote/fulltext/FullTextService.kt`: `downloadPdf` (`:1046-1066`)
- Create: `android/.../test/.../data/remote/fulltext/BotWallsContractTest.kt`
- Modify: `android/.../test/.../data/remote/fulltext/FullTextServicePdfDownloadTest.kt`

**Interfaces:**
- Consumes: `RequestFailureKind.CHALLENGED` (Task 1); the fixture (Task 2).
- Produces: `object BotWalls { val WALL_MARKERS: Map<String, List<String>>; fun challengeMarkers(status: Int, headers: List<Pair<String, String>>, body: ByteArray): List<String>; fun challengeFailure(status: Int, headers: List<Pair<String, String>>, body: ByteArray): RequestFailure? }`

- [ ] **Step 1: Write the failing contract test**

`BotWallsContractTest.kt`. The locator is copied from `OpenAccessShortfallContractTest.contractFile` (`:52-62`), with the path `doc/cross_platform/fulltext_parity/challenge_detection.json`:

```kotlin
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
```

- [ ] **Step 2: Constants and the detector**

`Constants.kt`, beside `PDF_MAGIC_BYTES`:

```kotlin
    /**
     * Bot walls (#480): how much of a non-PDF answer is read to recognise a
     * challenge page, the statuses that say the file is gone whatever the page
     * shows, and the statuses Cloudflare answers a challenge with. Contract:
     * `doc/cross_platform/fulltext_parity/challenge_detection.json`.
     */
    const val CHALLENGE_BODY_PREFIX_BYTES = 64 * 1024
    val CHALLENGE_GONE_STATUSES = setOf(404, 410)
    val CLOUDFLARE_REFUSAL_STATUSES = setOf(403, 503)
    const val CLOUDFLARE_SERVER = "cloudflare"
```

`BotWalls.kt`:

```kotlin
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
```

`looksLikePdf(prefix)` is the existing helper in `PdfDownload.kt:74`. Check its signature, and use it or compare against `Constants.PDF_MAGIC_BYTES`. `decodeToString()` replaces invalid UTF-8, as Python's `errors="replace"` does.

- [ ] **Step 3: The download records it**

In `downloadPdf`, the not-successful branch:

```kotlin
                if (!response.isSuccessful) {
                    Log.w(TAG, "PDF download failed: HTTP ${response.code}")
                    // A bot wall's challenge page is no answer about the article (#480)
                    val page = response.peekBody(Constants.CHALLENGE_BODY_PREFIX_BYTES.toLong()).bytes()
                    val challenge = BotWalls.challengeFailure(response.code, response.headers.toList(), page)
                    return@withContext PdfDownload.Failed(challenge ?: RequestFailure.forHttpStatus(response.code))
                }
```

and the not-a-PDF branch, before the partial is deleted:

```kotlin
                    if (!isCachedPdf(partial)) {
                        Log.w(TAG, "PDF download from $pdfUrl is not a PDF")
                        val page = partial.inputStream().use { it.readNBytes(Constants.CHALLENGE_BODY_PREFIX_BYTES) }
                        val challenge = BotWalls.challengeFailure(response.code, response.headers.toList(), page)
                        return@withContext PdfDownload.Failed(
                            challenge ?: RequestFailure(RequestFailureKind.MALFORMED_RESPONSE)
                        )
                    }
```

`readNBytes` needs API 33 on Android, but unit tests run on the JVM. Check `minSdk` in `app/build.gradle.kts`; if it is below 33, use a bounded loop or `it.readBytesUpTo(...)`, a small private helper that reads into a `ByteArray(CHALLENGE_BODY_PREFIX_BYTES)` until EOF or full.

- [ ] **Step 4: Download tests**

In `FullTextServicePdfDownloadTest.kt`, beside `a page served in place of the PDF is not saved` (`:99`) and `a refusal keeps its status` (`:108`), add, with the same MockWebServer set-up:

- `a cloudflare 403 is challenged`: 403, header `Server: cloudflare`, body `<html>` → `PdfDownload.Failed(RequestFailure(CHALLENGED, 403))`;
- `a challenge served as a PDF is challenged and not saved`: 200, `Content-Type: application/pdf`, body `<title>Preparing to download` → `Failed(RequestFailure(CHALLENGED, 200))`, and no `<id>.pdf` in the cache dir;
- the control `an ordinary 403 keeps its status`: 403, `Server: nginx` → `Failed(RequestFailure.forHttpStatus(403))`.

- [ ] **Step 5: Run the Android gate**

Run: `cd android/MedicalFactChecker && ./gradlew testDebugUnitTest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add android/MedicalFactChecker/app/src
git commit -m "feat(android): recognise and record a challenged open-access PDF (#480)"
```

---

### Task 6: The contract prose, the full gate, and the PR

**Files:**
- Modify: `doc/cross_platform/fulltext_retrieval.md` (the "Unpaywall PDF refusal" section `:547-592`, and a new section after it)
- Modify: `HANDOVER.md` (the in-flight section)

- [ ] **Step 1: Write the section**

In `fulltext_retrieval.md`, replace the sentence "Whether such a failure gets its own kind, or a link to open in the browser, is the maintainer's decision, not yet made." with "It gets its own kind, `challenged` (below); no link is offered." Then add after that section:

```markdown
### A challenged PDF (#480)

An open-access PDF (Unpaywall's or OpenAlex's) whose answer is a bot wall's
challenge page is recorded as `challenged`, with the status the page came
with, in place of `http_status` or `malformed_response`. It is not an answer
about the article: the reader is told the copy "could not be asked (the host
asked for a robot check)", and no absence is settled.

A non-PDF answer is challenged when `challenge_markers` names a defence:

- a needle of the shared marker table in the body's first 64 KiB, ASCII-
  lowercased and decoded as UTF-8 with invalid bytes replaced, or in a header
  line `name: value`;
- or Cloudflare's own refusal: a 403 or 503 whose `Server` header is
  `cloudflare` (ScienceDirect's markup lies past the bytes read).

Never for a 404 or 410, and never for a body beginning `%PDF`. The table, the
bounds and the cases are `fulltext_parity/challenge_detection.json`; every
platform's table must equal it. Survey: 89% of challenged failures were walls,
about 6% had no free PDF behind them
(`doc/developer/unpaywall_pdf_survey/`). The desktop's embedded browser and
review queue (stages 2 and 3 of
`docs/superpowers/specs/2026-10-12-browser-rescue-design.md`) fetch these.
```

- [ ] **Step 2: Run every gate**

```bash
pytest tests/ -q
python .github/scripts/lint_delta.py --base-ref origin/master
cd android/MedicalFactChecker && ./gradlew test; cd -
```
Then one background job (Swift, never two at once):
```bash
cd Packages/BioMedLit && swift test 2>&1 | tail -20; cd ../../ios/MedicalFactChecker && swift test 2>&1 | tail -20 && xcodebuild -scheme MedicalFactChecker -destination 'platform=macOS' build 2>&1 | tail -5
```
Expected: 0 failures everywhere, no new lint findings, `** BUILD SUCCEEDED **`.

- [ ] **Step 3: HANDOVER and commit**

Update HANDOVER.md's in-flight section: stage 1 is done (name the PR once opened), and stage 2 (the queue, the hidden service, rescue → re-analysis, the #483 removal) is next.

```bash
git add doc/cross_platform/fulltext_retrieval.md HANDOVER.md
git commit -m "docs(fulltext): the challenged kind in the retrieval contract (#480)"
```

- [ ] **Step 4: Push and open the PR**

The PR body says "Refs #480". **No closing keyword** before any issue number that stays open: #480 stays open for stages 2 and 3, and #483 is closed by stage 2, not by this PR.

```bash
git push -u origin feat/browser-rescue-480
gh pr create --base master --title "The challenged failure kind: a bot wall's page is not an answer (#480, stage 1)" --body-file <body>
```
