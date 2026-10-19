# WP-2 spike: do the mobile apps consume bmlib's Rust crate through UniFFI?

**Date:** 2026-10-17. **Plan:** `doc/plans/2026-10-17-port-bmlib-advances.md`, Phase 2 / WP-2
(the plan and the rest of `bmlib_sync/` arrive with PR #564, which merges first).

**Verdict: Phase 4 consumes Rust.**

All five acceptance criteria were met with measured numbers. The evidence for the parse is
narrower than a full parity proof, and is stated as such:

- **Swift vs Rust**, on the seven corpus articles: the agreed subset (identifiers,
  metadata, author names, figures, table labels/captions/footnotes, references, abstracts;
  listed under criterion 2) is **asserted equal**. Body paragraphs are **not asserted**:
  they were compared once, by a paragraph diff that is not committed, and every difference
  it showed is one of three things: a bmlib fix that Swift still lacks, a known divergence,
  or a field the adapter does not carry yet (the 11 rows under criterion 2).
- **Android vs Rust**: never compared. The Android test checks bmlib against `corpus.json`'s
  hand-read values on a subset of fields (identifiers, journal, year, volume, title, and
  author, figure, table and reference counts, table labels, the first figure's graphic),
  not against the Kotlin `JATSXMLParser`, and CI does not run it.
- Seven articles is a small corpus; [Before any Phase 4 switch](#before-any-phase-4-switch)
  lists what would widen it.

The
adapter costs about 0.65 MB per architecture on iOS and 0.82 MB per ABI on Android
(1.0 MB with JNA). The Xcode Cloud fresh-clone archive passes, and Gradle builds the `.so`
files itself. Hand-porting WP-4.1 to WP-4.6 would mean porting the work behind the 41 issue
numbers those packages cite twice (Swift, then Kotlin), and then keeping three parsers in step forever. Consuming Rust
turns Phase 4 into adapter work. The four items to settle first are decided (see
[Maintainer decisions](#maintainer-decisions-2026-10-17)); what remains before a switch is
listed under [Before any Phase 4 switch](#before-any-phase-4-switch).

## What was built

| Repository / branch | Commits | What |
|---|---|---|
| bmlib `spike/uniffi-ffi` | `bdcecb2` | `sqlite` and `http-ureq` become **default** features of `bmlib`, so a mobile build can leave both out. Every existing consumer builds what it built before. `cargo test -p bmlib`: 994 passed; clippy clean with and without the defaults |
| | `6724db3`, `1a96f2c`, `2b969a5`, `8cd6c39` | `rust/bmlib-ffi`: UniFFI **0.32.2** (pinned exactly, proc-macro), `parse_jats`, `parse_jats_audited`, `bmlib_version`, a `ForeignHttpClient` callback-interface sketch, a `release-ffi` profile, `scripts/build-xcframework.sh`, and a README |
| bmlibrarian_lite `spike/uniffi-jats` | `b0dbf6d3`, `b2b6d73e` | `Packages/BioMedLit`: `Frameworks/BmlibFFI.xcframework` (committed, pinned in `BmlibFFI.pin.json`), `Sources/BmlibCore` (generated Swift), `JATS/BmlibJATSAdapter.swift`, and `BmlibJATSParityTests` |
| | `4de47fa0` | Android: the generated Kotlin, the Gradle `cargoNdkBmlib`/`cargoHostBmlib` tasks, JNA, R8 keep rules, and `BmlibJatsFfiTest` |

Nothing in either app calls the Rust path yet. BioMedLit links it, so it ships in the
archive. Both test suites exercise it.

## Acceptance criteria, measured

### 1. `parse_jats` is callable from BioMedLit and from Android JVM unit tests — **met**

- **Swift:** `BmlibJATSParityTests` (3 tests) passes in debug and release. The full BioMedLit
  suite still passes: **1,643 tests, 0 failures** (19 skipped, all network-gated).
- **Android:** `BmlibJatsFfiTest` loads a host build (darwin-aarch64) of the library through
  JNA. It parses all seven corpus articles and checks each against `corpus.json`'s
  hand-read values: identifiers, journal, year, volume, title, and the author, figure, table
  and reference counts, plus table labels and the first figure's graphic. Result: **2/2
  pass, 0 skipped**. The full Android unit suite still passes: **1,703 tests, 0 failures**
  (18 skipped).
- **CI never ran the Android FFI test** at the spike: `BmlibJatsFfiTest` skipped there
  and ran on the spike's host only. *Since #583 (2026-10-18)* `android-tests.yml` checks
  out bmlib at the pin with the pinned rustc and cargo-ndk, and fails if the test skips. Wiring it
  is under [What GitHub Actions needs](#5-gradle-builds-the-so-files--prototyped-and-working).

### 2. Corpus comparison — **met; every difference classified**

`BmlibJATSParityTests.testCorpusThroughBothParsers` runs every corpus article through
`JATSXMLParser` and through `BmlibJATS.parse` (Rust, then the adapter).

**Asserted equal on all 7 articles.** This is the agreed subset:

- PMC ID, DOI, PMID, journal, year, volume, issue, pages, title;
- author full names and affiliation counts;
- for every figure: label, caption, graphic URL, footnotes;
- for every table: label, caption, footnotes;
- reference count; every reference's `label|year|doi|pmid`; and the counts of references
  carrying authors, article title, source, volume and first page;
- abstract sections, with one documented exception: bmlib keeps a title-only abstract
  section, (b) below.

Also asserted: every body-section path Swift produces exists in bmlib's tree. Nothing
Swift titles is lost.

**Reported, not asserted:** the body-section tree and its paragraphs, and the citation
strings. A paragraph-level diff, run once by hand from the `BMLIB_PARITY_REPORT` output and
**not committed**, gave the list of differences below. They are **Swift vs Rust only**; no
Kotlin-vs-Rust diff was made. Since the test does not assert paragraphs, it would not catch
a twelfth difference:

| # | Difference | Where (corpus) | Class | Evidence |
|---|---|---|---|---|
| 1 | Front-matter prose (`<author-notes>` correspondence and COI footnotes, PLOS/Sci Rep `<notes>`) becomes leading untitled body sections. Swift drops it, and in two articles leaves a **titled-and-empty / untitled-and-empty** leading section | 12755737 (+3 paras), 12759138 (+1), 13294358 (+2), 13295835 (+2: "None declared." is the COI statement) | **(a)** bmlib fixed | bmlib CHANGELOG, *"Front-matter prose reaches the article, and a front-matter section carries its prose"* (bmlib #230, #234). Swift's empty leading section is #234's described defect. Feeds WP-4.4 (COI reaches the analysers) |
| 2 | Definition lists keep their term: `AI — artificial intelligence` where Swift prints `artificial intelligence` | 12661592 (1 para), 12759138 (15 paras) | **(a)** | bmlib #228 (a `<term>` folded into its definition). Listed in WP-4.3 ("definitions (#228)") |
| 3 | Display formulas render into the paragraph (`T=logTmin−log(1+K1[H+])+…`); Swift drops them | 8754430 (5 formulas), 13295835 (1) | **(a)** | bmlib's formula renderer (`fulltext/jats_text`: `render_formula`, `latex_expression`); lite #399 / WP-4.7 |
| 4 | Funder names lose the glued registry URL: Swift `http://dx.doi.org/10.13039/100000057National Institute…` becomes `National Institute…` | 8754430 (2 paras) | **(a)** | bmlib CHANGELOG *award-group* entry: "a funder's id is one only at `award-group > funding-source > institution-wrap`" (bmlib #284). WP-4.1 |
| 5 | An element-only citation writes no `citation` string. Swift stores its leftover text, run together (`URLhttps://www.who.int/…`, `557`, `Fundamentals of Protein Structure and FunctionLondonGarland Science`), as a non-deposit citation | 12661592 (10 refs), 12785261 (13), 8754430 (11) | **(a)** | bmlib #314 (element-only text is "its parts run together") and #390. Harmless after mapping, because `formattedCitation` prints the structured parts first; the adapter sets `citationIsDeposit = !citation.isEmpty` |
| 6 | `<supplementary-material>` legends are filed as section prose ("Supporting information"). Swift drops them and counts the drop | 12755737 (12 paras), 13294358 (3) | **(b)** deliberate divergence, neither right | Swift: lite #144 (`unmodelledCaptionDrops`, "characterised, known wrong"). bmlib: its own open #137 ("legend paragraphs are filed as article prose without their titles") |
| 7 | Europe PMC's injected `<sec sec-type="associated-data">` repeats the supplementary legends, so bmlib prints them **twice** | 12755737 (+12), 13294358 (+3) | **new bmlib gap** (Swift avoids it only by dropping all legends) | bmlib suppresses the injected `<sub-article article-type="associated-data">` (its #119), but not this `<sec>` spelling. Lodged as bmlib#497 |
| 8 | A title-only abstract section (`<sec><title> Abstract</title></sec>`, BMJ) is kept by bmlib and dropped by Swift | 13295835 | **(b)** Swift ahead | bmlib's flush rule mirrors Python's (`jats_reader.rs`, `"abstract"` close). Excluded from the asserted comparison and named in the test. Lodged as bmlib#498 |
| 9 | 50 `<array>` cells reach no table. **Both parsers lose them**; only bmlib says so (a parse warning) | 12785261 | **(b)** bmlib ahead on reporting | Swift's `testParsingReportsNoContentLoss` passes on this article, so the loss is silent there |
| 10 | Table bodies: bmlib carries HTML (`html_content`), BioMedLit markdown. The adapter leaves `markdownContent` empty | all 16 tables | **(c)** adapter | The `markdownDigest`, `markdownRowCount` and `markdownColumnCount` digest fields were therefore not compared |
| 11 | `elocationId` (5 of 7 articles), funding statements and awards (3 of 7), `hasBody`, and the parse audit have no slot in BioMedLit's `JATSArticle` | — | **(c)** adapter | WP-4.3 (#265/#272) and WP-4.4 add the slots |

The digest's `unmodelledCaptionDrops`, `markdownScalarCount` and `htmlScalarCount` were
**not compared**: they measure the Swift renderers, not the parse.

Neither parser captures author affiliations: 0 on every author, in both. That is lite
#154, and it is shared. No corpus article has a group (`<collab>`), `<string-name>` or
given-names-only author, so the adapter's rule for a contributor without a surname is
tested by a synthetic document instead
(`testContributorsWithoutASurnameAreKeptWithBmlibsFullName`). The adapter keeps such a
contributor with bmlib's trimmed full name, as bmlib and Python do; Swift's parser drops
it. That is a class (b) divergence with Rust ahead.

**Swift behaviours bmlib lacks or exposes differently** (class (b), not visible in the
corpus diff):

- **The parse-warnings banner** (`JATSParseWarnings`, typed losses). bmlib returns its audit
  as strings (`JatsParseReport.warnings`/`diagnostics`). The banner needs a typed audit over
  the FFI, which is upstream work on bmlib.
- **The PMC ID mismatch warning** when the caller's PMC ID disagrees with the document's.
  bmlib's `parse_with_pmc_id` has the caller's ID win silently. Keep the warning in the
  adapter, or upstream it.
- **`citationIsDeposit`.** bmlib has no flag; the adapter derives it as described in row 5.
- **Grouped-citation sub-labels (#177).** bmlib's CHANGELOG argues #177 as settled. The
  corpus has no grouped citation, so this is unverified here.

### 3. Binary size — **measured; maintainer to accept**

Release `release-ffi` profile (fat LTO, `opt-level = "s"`, stripped). "mobile" is the
default for `bmlib-ffi` (bmlib without SQLite and ureq); "full" restores them.

| Architecture | Dynamic library, mobile | Dynamic library, full | Static archive, stripped, mobile / full |
|---|---|---|---|
| iOS arm64 | **0.67 MB** | 0.72 MB | 10.4 / 16.2 MB |
| iOS simulator arm64 | 0.67 MB | 0.72 MB | 10.4 / 16.2 MB |
| iOS simulator x86_64 | 0.64 MB | 0.76 MB | 10.1 / 15.9 MB |
| macOS arm64 | 0.67 MB | 0.72 MB | 10.4 / 16.2 MB |
| macOS x86_64 | 0.64 MB | — | — |
| Android arm64-v8a | **0.82 MB** (0.38 MB gzipped) | 0.89 MB | — |
| Android x86_64 | **0.87 MB** | 0.94 MB | — |

- **Linked statically into a stub app with `-dead_strip`:** +0.65 MB on iOS arm64, iOS
  simulator and macOS arm64 (+0.71 MB full).
- **What the mobile trim does to the shipped app:** about −50 KB. What it really saves is
  the C (bundled SQLite) and assembly (ring/rustls) cross-builds. A clean single-slice build
  takes **47 s instead of 89 s**, and the trim takes **6 MB off each static slice**.
- **App-size delta from real archives**, base `d3ae21db` against the spike, unsigned:
  - iOS (generic device, arm64): **4,564 KB → 5,356 KB, +792 KB**. `BmlibFFI.framework` is
    676 KB of that; the rest is the generated Swift.
  - macOS (universal): **9,008 KB → 10,588 KB, +1,580 KB**.
- **Android debug APK:** `libbmlib_ffi.so` adds 820 KB (arm64-v8a) and 874 KB (x86_64).
  JNA adds `libjnidispatch.so` at 118–168 KB per ABI, plus its classes. JNA's AAR also
  packs armeabi, mips and mips64 dispatch libraries that an `abiFilters` or `packaging`
  exclude would drop. Per delivered ABI (AAB split), the total is about **1.0 MB
  uncompressed**.

### 4. Xcode Cloud fresh-clone archive — **met with the committed xcframework (i)**

Reproduced as HANDOVER describes. The worktree was cloned into the scratch directory, and
`.github/scripts/xcode_project_guards.py` passed (no package reference outside the repo;
the scheme is still shared). Then:

```
xcodebuild -scheme MedicalFactChecker -destination 'platform=macOS' CODE_SIGNING_ALLOWED=NO archive
xcodebuild -scheme MedicalFactChecker -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO archive
```

Both report **ARCHIVE SUCCEEDED**. `BmlibFFI.framework` is embedded under
`Contents/Frameworks` / `Frameworks` and loaded through `@rpath`. The first macOS attempt
**failed**: the macOS archive builds arm64 **and** x86_64, and the slice was arm64 only.
The fix was a universal `macos-arm64_x86_64` slice (bmlib `2b969a5`). An Xcode Cloud run
would have found this too.

The project file is unchanged. The binary target lives inside `Packages/BioMedLit`, which
the app already references by relative path.

**Not run:** an iOS **Simulator** build. CoreSimulator on this host is older than Xcode 27.1
needs ("CoreSimulator is out of date"). The generic-device archive does compile the
`#if os(iOS)` files. The simulator slice is built and its architectures are checked
(`lipo`), but it has not been executed.

**Packaging trade-off:**

| | (i) committed `binaryTarget(path:)` xcframework | (ii) Rust built in `ci_scripts/ci_post_clone.sh` |
|---|---|---|
| Fresh-clone archive | **Tested: succeeds** | Untested |
| Repository cost | Dynamic: 3.3 MB on disk, **~0.8 MB packed** per bmlib bump (the slices delta-compress). Static: 39 MB on disk, 6.9 MB packed, which is LFS territory. **No LFS needed for dynamic** | None |
| Xcode Cloud time | 0 | rustup install, then 5 targets × ~47 s clean (~4 min of cargo before Xcode starts), every build. Up to ~2 min if only the archived platform's slices are built |
| Toolchain on the image | None | **Unverified.** No Apple statement found. One public project (waddle-social/waddle PR #1836, merged 2026-09-24) installs rustup from `sh.rustup.rs` in `ci_post_clone.sh`, but reports no Xcode Cloud build result |
| Reproducibility | The binary is pinned by bmlib commit plus a tree SHA-256 (`BmlibFFI.pin.json`). Nobody can rebuild it in CI without bmlib | Built from source at a pinned tag every time. Needs network (crates.io, static.rust-lang.org) during the build |
| Local `swift test` | Works offline, no Rust needed | Every developer needs Rust plus 5 targets |

**Recommendation: (i)**, with dynamic frameworks. Add a CI guard (GitHub Actions, macOS)
that rebuilds the xcframework from the pinned bmlib commit and compares tree hashes
(`.github/scripts/xcframework_tree_sha256.py <xcframework> --check BmlibFFI.pin.json`), so
the committed binary is checked rather than trusted. *Since #583 (2026-10-18)* it is
`.github/workflows/bmlib-xcframework.yml`, after bmlib #490 made the build
path-independent. Byte-identity also needs the same Apple linker, so **the committed
xcframework is that workflow's own build** (maintainer, 2026-10-18), and the pin records
the runner's Xcode; the workflow's header says how to re-pin.

Dynamic rather than static matters most for repository size: one order of magnitude.
Static would make the shipped app ~0.1 MB smaller, since the generated Swift and the
library dead-strip together.

### 5. Gradle builds the `.so` files — **prototyped and working**

`app/build.gradle.kts` adds three tasks. They are registered only when a bmlib checkout is
named by `bmlib.dir` (`local.properties`, relative to the Android project root) or by
`BMLIB_DIR`:

- `checkBmlibPin` reads `bmlibCommit` from `Packages/BioMedLit/Frameworks/BmlibFFI.pin.json`
  (the one pin both apps share; the committed Kotlin bindings came from that commit too)
  and fails when `git -C <bmlib> rev-parse HEAD` differs, or when `rust/` has uncommitted
  changes. Both cargo tasks depend on it. `-Pbmlib.allowUnpinnedCommit=true` builds anyway,
  with a warning, for working on bmlib itself.
- `cargoNdkBmlib` runs `cargo ndk -t arm64-v8a -t x86_64 -p 26 -o build/generated/bmlib/jniLibs build -p bmlib-ffi --profile release-ffi`.
  The ABIs and the API level are the same values as `abiFilters` and `minSdk` (one `val`
  each). It is wired as a `jniLibs.srcDir`, and `preBuild` depends on it. Inputs (Rust
  sources, manifests, the lockfile) and the output directory are declared, so Gradle skips
  it when nothing changed.
- `cargoHostBmlib` runs the host build into `CARGO_TARGET_DIR` (default `rust/target`,
  passed as `--target-dir`) and copies `libbmlib_ffi.{dylib,so}` to
  `build/generated/bmlib/host`, failing if no library was built. Every `Test` task depends
  on it and gets `jna.library.path`.

`checkBmlibForRelease` is always registered, and `assembleRelease`, `bundleRelease`,
`packageRelease` and `packageReleaseBundle` depend on it. It fails a release build that
has no bmlib checkout, or whose `jniLibs` lack the library for a shipped ABI. Once Phase 4
calls Rust, such an APK would throw `UnsatisfiedLinkError` (an `Error`, which a
`catch (e: Exception)` does not catch). Debug builds and unit tests are not hooked: without
a checkout they build as before, and the FFI test skips.

Measured: `./gradlew :app:testDebugUnitTest --tests '*BmlibJatsFfiTest*'` ran both cargo
tasks and the test, with no manual step. `assembleDebug` packed both `.so` files. Without
the property, the debug build is unchanged and the test skips with a message.

**What GitHub Actions (`android-tests.yml`) needs:**

1. `actions/checkout` of `hherb/bmlib` at the pinned tag into `./bmlib`, then
   `BMLIB_DIR=$GITHUB_WORKSPACE/bmlib`.
2. Rust: `rustup` is preinstalled on `ubuntu-latest`. Add `rustup target add
   aarch64-linux-android x86_64-linux-android`, then `cargo install cargo-ndk --locked`
   (~1 min) or `taiki-e/install-action`.
3. NDK: preinstalled on `ubuntu-latest` (`$ANDROID_NDK_LATEST_HOME`). Export it as
   `ANDROID_NDK_HOME`, or pin one with `sdkmanager`.
4. Cache `~/.cargo/registry` and `bmlib/rust/target` (`Swatinem/rust-cache`). A clean build
   is ~50 s per ABI.

The host library for unit tests is then `libbmlib_ffi.so` (linux-x86_64), which JNA's JAR
also covers.

**Two findings for Android:**

- **There was no `armeabi-v7a` build.** The app shipped `armeabi-v7a` and `x86` for its
  other native libraries (MediaPipe, TFLite), and on such a device the first call into
  Rust would throw `UnsatisfiedLinkError`. **Settled by decision 4:** `abiFilters` is now
  the 64-bit ABIs only. This takes effect when this branch merges, before any Rust call,
  so the 32-bit devices lose the app at that release (a release-notes item).
- **JNA's 16 KB page alignment.** JNA 5.14.0's x86_64 `libjnidispatch.so` has 4 KB-aligned
  `LOAD` segments, which Android 15+ devices with 16 KB pages refuse (targetSdk is 35). The
  app now uses JNA **5.19.1**, whose arm64-v8a and x86_64 dispatch libraries are both
  16 KB-aligned (checked from the program headers). `libbmlib_ffi.so`'s own alignment has
  not been checked.
- **`assembleRelease` already fails in R8** on the base commit (missing
  `javax.lang.model` / autovalue and markwon `Strikethrough` classes; verified on
  `d3ae21db`), and the release build is not in CI. So the JNA keep rules added here
  (`-keep class com.sun.jna.** / org.bmlib.ffi.**`) are untested by a minified build. JNA
  without keep rules is a known release-only crash.

## Other measurements

**Parse time.** Times are per article, the mean of 20 runs on the same machine. Rust
includes the FFI marshalling and the adapter. Run with `BMLIB_PARITY_TIMING=1` (the timing
loop is off by default, so CI does not pay for it).

| Build | Swift `JATSXMLParser` | Rust via UniFFI + adapter |
|---|---|---|
| debug Swift / release Rust | 145 ms for the 7 articles (8–34 ms each) | 17 ms (1.0–3.8 ms each) |
| **release / release** | **132 ms** (7–29 ms each) | **24 ms** (1.5–5.4 ms each) |

The Rust path is release-built in both rows, yet its corpus total was 17 ms in one run and
24 ms in the other, so the numbers are noisy. Against the release Swift parser that is
**roughly 5.5× to 8× faster** (132/24 to 132/17). Only the order of magnitude is claimed.

Android, through JNA, including first library load: 72 ms for all seven.

**Threading and Swift 6.** UniFFI generates records as `Sendable` value types
(`generate_immutable_records`). Objects are `@unchecked Sendable` classes guarded by Rust's
`Send + Sync`, and the free functions hold no state. The generated Swift typechecks
**cleanly under `-swift-version 6`** (strict concurrency, no warnings).
`testParseRunsConcurrentlyOffTheMainActor` parses all seven articles in a task group.

Calls are **synchronous and blocking**. Call them off the main actor, as the existing parser
should be.

The `ForeignHttpClient` sketch is synchronous too, because bmlib's `HttpClient` is. A Swift
implementation would block a background thread on a `URLSession` task (a semaphore), or
bmlib would grow an async transport, which UniFFI supports through `async` exports and a
runtime. The sketch is also **exported**: it ships in the committed xcframework, both
bindings and their checksum checks, although nothing calls it. bmlib #492 removes it (or
puts it behind a feature) before the pinned commit.

**Errors.** `BmlibJATS.parse(data:)` throws `JATSParseError.parsingFailed` with the XML
reader's own reason for malformed XML. A Rust **panic** is different: UniFFI raises it as
the bindings' file-private `UniffiInternalError.rustPanic`, which reaches the caller as a
plain `Error` that only a catch-all `catch` sees. Kotlin's bindings raise an
`InternalException` there, likewise outside `JatsException`.

**Release pinning.** Bindings and binary must come from **one** build. UniFFI checks
per-function checksums at the first call, and a mismatch fails loudly rather than
corrupting data.

lite pins three things in `Packages/BioMedLit/Frameworks/BmlibFFI.pin.json`:

- the bmlib commit, which becomes a tag when this lands (e.g. `bmlib-ffi-v0.2.0`);
- the UniFFI version;
- `treeSha256`, over every file in the xcframework. Its recipe is defined by
  `.github/scripts/xcframework_tree_sha256.py` (named in the pin as `treeSha256Script`):
  one line per file, `<path> <sha256>`, or `<path> -> <target>` per symlink, with paths
  starting `BmlibFFI.xcframework/`; sorted, `\n`-joined, with a trailing `\n`. A Python
  test checks that the committed xcframework reproduces the pinned hash.

Android reads the same commit from the same pin: Gradle's `checkBmlibPin` refuses a bmlib
checkout at any other commit (criterion 5). CI's checkout should use it too.

`bmlib_version()` does **not** identify the build. It returns bmlib-ffi's
`CARGO_PKG_VERSION`, `0.2.0` on every commit of that version, so both tests'
`== "0.2.0"` assertions cannot tell the pinned commit from another. UniFFI's checksums
catch an interface mismatch, but not a behaviour change behind the same interface. bmlib
#493 asks bmlib-ffi to expose the commit, so the apps can assert it against the pin. Until
then, the guards are the pin check (Android) and the CI rebuild-and-compare recommended
under criterion 4 (Swift).

## Risks

1. **Table rendering is the largest adapter gap.** The apps render markdown tables; bmlib
   carries HTML. Phase 4 either renders markdown from bmlib's HTML in the adapter (a
   per-platform port: the thing the spike was meant to avoid, though small), adds a
   markdown renderer to bmlib, or moves the apps' display to HTML. The `markdownDigest`
   corpus fields cannot be carried over until this is decided.
2. **Swift-ahead behaviours must go upstream or stay in the adapter:** the typed
   parse-warnings banner, the PMC ID mismatch warning, and the title-only abstract
   section. Each one left in the adapter is a small per-platform port again.
3. **Two new bmlib gaps surfaced:** Europe PMC's injected `associated-data` `<sec>`
   (duplicate legends) and the title-only abstract section. Both are lodged on bmlib:
   bmlib#497 and bmlib#498. *At #583's re-pin (bmlib `f767215`)* bmlib drops the
   associated-data block (bmlib#497) and links affiliations, so `BmlibJATSParityTests`
   compares author names only (BioMedLit has no affiliations, #154) and leaves Swift's
   `>Associated Data` paths out of its section check (#587).
4. **Digest churn.** Adopting Rust moves `JATSRealCorpusTests`' digests (body sections,
   citation counts). Read each change against the table above; it is a review, not a
   regeneration.
5. **Binary provenance.** Guarded since #583: CI rebuilds the xcframework from the pin and
   compares it, the committed xcframework, and the Swift and Kotlin bindings with the pin.
6. **The xcframework is rebuilt for every bmlib bump** (~4 min locally, 5 targets), and
   needs the `x86_64-apple-darwin` target for the universal macOS slice.
7. **Mobile caches** (WP-4.6) must carry a renderer stamp before a Rust switch ships, or
   cached articles keep the old parse. This holds whichever route Phase 4 takes.
8. **The `sqlite`/`http-ureq` defaults are a bmlib API change** (additive, default-on). They
   need a bmlib CHANGELOG entry and bmlib's CI matrix extended with `--no-default-features`.
9. **Fixed by bmlib #490 (2026-10-18):** builds go through bmlib's
   `with-remapped-paths.sh` (Gradle's cargo-ndk task too), Apple slices link with a fixed
   install name, and two checkouts build the same bytes (given the same rustc and Xcode). As found at the spike: **the
   binaries carried the builder's home path, so they were not reproducible.** `strings`
   on the committed `ios-arm64` slice finds 23 `/Users/<builder>/…` panic-location paths
   (`~/.rustup/…/rustlib/src`, `~/.cargo/registry/src/…`). That leaks the developer's home
   path into every shipped app, and it means the recommended CI rebuild-and-compare guard
   can never match a runner's build (`/Users/runner`). Fix on bmlib (#490): build with
   `RUSTFLAGS="--remap-path-prefix=$HOME/.cargo=/cargo --remap-path-prefix=$HOME/.rustup=/rustup --remap-path-prefix=<bmlib root>=/bmlib"`,
   and rebuild the committed xcframework (and Android's `.so` builds) with it.
10. **Only UTF-8 crosses the FFI.** `parse_jats` takes a string, so the adapter decodes the
    bytes as UTF-8 and refuses anything else (with a message saying so), where
    `XMLParser` honours a declared encoding (ISO-8859-1, UTF-16). Reading those needs a
    bytes entry point in bmlib (#491); the apps must not each guess the encoding.
11. **A Rust panic is not a parse error** (see *Errors* above). Phase 4 callers need a
    catch-all around the call, or the adapter must map the internal error.
12. **`bmlib_version()` cannot identify the build** (see *Release pinning*); bmlib #493.
13. **The evidence is seven articles, Swift vs Rust.** See the section below.

## Before any Phase 4 switch

The spike shows the route works. These would show that the Rust parse can replace both
apps' parsers:

1. **An allowlisted paragraph diff in the Swift test.** Encode the 11 classified
   differences (criterion 2's table) as an allowlist, and assert every other body
   paragraph equal. Today paragraphs are only reported, so a new difference passes
   unnoticed. Commit the diff the table was made from, too.
2. **A Kotlin-vs-Rust diff.** Android has only been compared with `corpus.json`'s
   hand-read fields, never with the Kotlin `JATSXMLParser`. Run the same agreed subset
   and paragraph diff in a JVM unit test, and wire that test into CI (a bmlib checkout at
   the pinned commit plus a Rust toolchain, as criterion 5 lists).
3. **A survey of a few hundred PMC articles.** Seven articles hid defects before: a
   synthetic or small corpus missed JATS defects that affected about half of real
   articles. Run both comparisons over a few hundred live PMC articles, and classify every
   new difference as the table does.
4. **The bmlib items:** bytes across the FFI (#491) and the build commit exposed (#493),
   each followed by a rebuild of the committed binaries and bindings. Reproducible builds
   (#490) and the HTTP sketch out of the exported surface (#492) are done; the pin names
   bmlib `main` since #583.

## Maintainer decisions (2026-10-17)

All four were decided as recommended:

1. **Size: accepted.** About +0.8 MB per iOS app (+1.6 MB universal macOS) and about
   +1.0 MB per Android ABI, JNA included.
2. **Packaging: the committed dynamic xcframework.** It is the route the fresh-clone
   archive tested. A `ci_post_clone.sh` Rust build is not pursued.
3. **Table bodies: a renderer in bmlib.** The desktop and both apps then share one table
   rendering, rather than each app's adapter writing its own. This is bmlib work (the bmlib
   plan's WP-B9 FFI surface).
4. **Android ABIs: 64-bit only.** `defaultConfig.ndk.abiFilters` is `arm64-v8a` and `x86_64`,
   the two ABIs cargo-ndk builds, so no APK ships an ABI without the library. Google Play
   requires 64-bit anyway. This applies from the release that includes this branch, ahead
   of any Rust call, so 32-bit-only devices stop receiving the app then (a release-notes
   item).

Separately, the release build's existing R8 failure should be fixed before any JNA code
ships.

## Reproducing

- bmlib: `cd rust && bmlib-ffi/scripts/build-xcframework.sh <dir>` (see
  `rust/bmlib-ffi/README.md`).
- Swift: `cd Packages/BioMedLit && swift test --filter BmlibJATSParityTests`. Add
  `BMLIB_PARITY_REPORT=<file>.json` to write the per-article report, which includes every
  paragraph from both parsers, and `BMLIB_PARITY_TIMING=1` to time both parsers.
- The xcframework's tree hash:
  `python3 .github/scripts/xcframework_tree_sha256.py Packages/BioMedLit/Frameworks/BmlibFFI.xcframework --check Packages/BioMedLit/Frameworks/BmlibFFI.pin.json`.
- Android: put `bmlib.dir=<bmlib checkout at the pinned commit>` in
  `android/MedicalFactChecker/local.properties`, then run
  `./gradlew :app:testDebugUnitTest --tests '*BmlibJatsFfiTest*'`.
