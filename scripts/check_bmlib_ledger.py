#!/usr/bin/env python3
"""Check that every bmlib change since 0.10.0 has an entry in the sync ledger.

bmlib's ``CHANGELOG.md`` names each change with a bold title. Most titles are
followed by a parenthesised group that carries the bmlib issue numbers, for
example ``- **The PubMed record read is the one asked about** (`transparency`,
#474).``. Every such number must appear as ``bmlib#N`` among the
``from_bmlib`` entries of ``doc/cross_platform/bmlib_sync/ledger.json``. An
entry whose group names no issue must instead have a ``bmlib:<slug>`` entry
whose ``title`` is the changelog title. Either way, a bmlib fix this
repository has not ported can't go unnoticed.

Usage:
    python scripts/check_bmlib_ledger.py --bmlib <bmlib checkout>
    python scripts/check_bmlib_ledger.py --print-commit

Exit status is 0 when every change is ledgered, 1 when some are missing, and
2 when an input can't be read: a missing file, a malformed ledger, or a
changelog this script can't parse.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LEDGER_PATH = (
    Path(__file__).resolve().parent.parent
    / "doc" / "cross_platform" / "bmlib_sync" / "ledger.json"
)

# Entries older than this release are out of scope; the ledger starts after it.
OLDEST_RELEASE_HEADING = "## [0.10.0]"
ENTRY_START = "- **"
# Where the bold title's opening "**" sits in an entry: after the "- ".
BOLD_OFFSET = len(ENTRY_START) - len("**")
PLATFORMS = ("python", "swift", "android")
OWED = "owed"
STATUSES = frozenset({"owed", "ported", "equivalent", "n/a", "declined"})
LEDGER_SCHEMA = 1
# The widest "#a-#b" range a title group may name. bmlib's widest is six
# (#165-#170); a wider one is far likelier a typo than a real span.
MAX_ISSUE_RANGE = 50

EXIT_OK = 0
EXIT_MISSING = 1
EXIT_UNREADABLE = 2

_ISSUE_NUMBER = re.compile(r"#(\d+)")
# "#165-#170" or "#165–170": every issue from the first to the last.
_ISSUE_RANGE = re.compile(r"#(\d+)\s*[-–]\s*#?(\d+)")
# "PR #195", "PRs #213 and #219": pull requests, not issues of the entry.
_PULL_REQUESTS = re.compile(
    r"\bPRs?\s*#\d+(?:(?:,\s*(?:and\s+)?|\s+and\s+)#\d+)*")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
# A list item opened with "*" or "+" instead of "-": an entry this script
# would otherwise skip without a word.
_FOREIGN_BULLET = re.compile(r"^[*+] \*\*")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_LEDGER_ID = re.compile(r"bmlib#\d+|(?:lite|bmlib):[a-z0-9][a-z0-9.-]*")


class UnreadableInput(Exception):
    """An input the check can't read, so it can't say what is missing."""


@dataclass(frozen=True)
class ChangelogEntry:
    """One changelog entry newer than the oldest release.

    Attributes:
        title: The bold title, whitespace collapsed, trailing period dropped.
        ids: The ``bmlib#N`` ids its title group names; empty when none.
    """

    title: str
    ids: frozenset[str]


def _entry_texts(changelog_text: str) -> list[str]:
    """Return the first paragraph of each entry newer than the oldest release.

    Args:
        changelog_text: The whole ``CHANGELOG.md``.

    Returns:
        One string per ``- **`` entry, its lines joined by single spaces,
        ending at the first blank line or the next list item or heading.

    Raises:
        UnreadableInput: The oldest release's heading is missing, or an entry
            is bulleted with ``*`` or ``+``.
    """
    entries: list[str] = []
    current: list[str] | None = None
    found_oldest = False
    for line in changelog_text.splitlines():
        if line.startswith(OLDEST_RELEASE_HEADING):
            found_oldest = True
            break
        if _FOREIGN_BULLET.match(line):
            raise UnreadableInput(
                f"an entry is not bulleted {ENTRY_START!r}: {line[:80]!r}")
        if line.startswith(ENTRY_START):
            if current is not None:
                entries.append(" ".join(current))
            current = [line.strip()]
        elif current is not None:
            if not line.strip() or line.startswith(("- ", "#")):
                entries.append(" ".join(current))
                current = None
            else:
                current.append(line.strip())
    if current is not None:
        entries.append(" ".join(current))
    if not found_oldest:
        raise UnreadableInput(
            f"no {OLDEST_RELEASE_HEADING!r} heading: the changelog's layout "
            "has changed, or it is not bmlib's")
    return entries


def _title_group(entry: str, title_end: int) -> str:
    """Return the parenthesised group that follows an entry's bold title.

    Args:
        entry: An entry as joined by ``_entry_texts``.
        title_end: Where the bold title ends in ``entry``.

    Returns:
        The text inside the balanced parentheses directly after the bold
        title, or an empty string when none follows it. A group left unclosed
        runs to the end of the entry's first paragraph.
    """
    rest = entry[title_end:].lstrip()
    if not rest.startswith("("):
        return ""
    depth = 0
    for index, char in enumerate(rest):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return rest[1:index]
    return rest[1:]


def _normalise_title(title: str) -> str:
    """Collapse a title's whitespace and drop a trailing period.

    Args:
        title: A changelog or ledger title.

    Returns:
        The title as the two are compared.
    """
    return " ".join(title.split()).rstrip(".")


def _group_issue_ids(group: str) -> set[str]:
    """Return the issue ids a title group names, ranges expanded.

    Args:
        group: The text inside a title group.

    Returns:
        Ids of the form ``bmlib#N``; pull requests are left out.

    Raises:
        UnreadableInput: A range runs backwards or spans more than
            ``MAX_ISSUE_RANGE`` issues.
    """
    group = _PULL_REQUESTS.sub("", group)
    ids: set[str] = set()
    for match in _ISSUE_RANGE.finditer(group):
        first, last = int(match.group(1)), int(match.group(2))
        if not first < last <= first + MAX_ISSUE_RANGE:
            raise UnreadableInput(
                f"issue range {match.group(0)!r} is not ascending within "
                f"{MAX_ISSUE_RANGE} issues")
        ids.update(f"bmlib#{n}" for n in range(first, last + 1))
    for number in _ISSUE_NUMBER.findall(_ISSUE_RANGE.sub("", group)):
        ids.add(f"bmlib#{number}")
    return ids


def parse_changelog(changelog_text: str) -> list[ChangelogEntry]:
    """Return every entry newer than 0.10.0, with its title and issue ids.

    Only the parenthesised group after the bold title is read for ids;
    numbers in the body prose, and pull requests (``PR #N``), are not issues
    of the entry.

    Args:
        changelog_text: The whole ``CHANGELOG.md``.

    Returns:
        The entries in changelog order.

    Raises:
        UnreadableInput: The changelog's layout isn't the one this script
            reads, so an empty answer would be a false "nothing missing".
    """
    entries: list[ChangelogEntry] = []
    for text in _entry_texts(changelog_text):
        bold = _BOLD.match(text, BOLD_OFFSET)
        if bold is None:
            raise UnreadableInput(f"an entry has no closed bold title: {text[:80]!r}")
        title = _normalise_title(bold.group(1))
        ids = _group_issue_ids(_title_group(text, bold.end()))
        entries.append(ChangelogEntry(title=title, ids=frozenset(ids)))
    if not entries:
        raise UnreadableInput(
            f"no {ENTRY_START!r} entry above {OLDEST_RELEASE_HEADING!r}")
    return entries


def changelog_issue_ids(changelog_text: str) -> set[str]:
    """Return the bmlib issue ids named in entry titles newer than 0.10.0.

    Args:
        changelog_text: The whole ``CHANGELOG.md``.

    Returns:
        Ids of the form ``bmlib#N``.
    """
    return {i for entry in parse_changelog(changelog_text) for i in entry.ids}


def _sort_key(entry_id: str) -> tuple[int, str]:
    """Order ``bmlib#N`` ids numerically."""
    return (int(entry_id.split("#", 1)[1]), entry_id)


def missing_entries(changelog_text: str, ledger: dict[str, Any]) -> list[str]:
    """Return changelog issue ids that have no ``from_bmlib`` entry.

    Args:
        changelog_text: The whole ``CHANGELOG.md``.
        ledger: The parsed ledger.

    Returns:
        The missing ids, in ascending issue order.
    """
    known = {entry["id"] for entry in ledger.get("from_bmlib", [])}
    return sorted(changelog_issue_ids(changelog_text) - known, key=_sort_key)


def missing_titles(changelog_text: str, ledger: dict[str, Any]) -> list[str]:
    """Return the titles of id-less changelog entries the ledger doesn't hold.

    An entry whose title group names no issue is matched by title against
    the ``from_bmlib`` entries whose id is ``bmlib:<slug>``.

    Args:
        changelog_text: The whole ``CHANGELOG.md``.
        ledger: The parsed ledger.

    Returns:
        The missing titles, in changelog order.
    """
    known = {
        _normalise_title(entry["title"])
        for entry in ledger.get("from_bmlib", [])
        if entry["id"].startswith("bmlib:")
    }
    return [
        entry.title for entry in parse_changelog(changelog_text)
        if not entry.ids and entry.title not in known
    ]


def _entry_problems(entry: object, where: str, platforms: tuple[str, ...]) -> list[str]:
    """Return what is wrong with one ledger entry.

    Args:
        entry: The entry as parsed.
        where: Its position, for the messages.
        platforms: The status keys it must carry.

    Returns:
        One message per problem; empty when the entry is well formed.
    """
    if not isinstance(entry, dict):
        return [f"{where} is not an object"]
    problems: list[str] = []
    entry_id = entry.get("id")
    if not isinstance(entry_id, str) or not _LEDGER_ID.fullmatch(entry_id):
        problems.append(f"{where} has id {entry_id!r}, not bmlib#N, "
                        "lite:<slug> or bmlib:<slug>")
    title = entry.get("title")
    if not isinstance(title, str) or not title.strip():
        problems.append(f"{where} ({entry_id}) has no title")
    for platform in platforms:
        status = entry.get(platform)
        if status not in STATUSES:
            problems.append(f"{where} ({entry_id}) has {platform} status "
                            f"{status!r}, not one of {sorted(STATUSES)}")
    note = entry.get("note")
    if "declined" in (entry.get(p) for p in platforms) and not (
            isinstance(note, str) and note.strip()):
        problems.append(f"{where} ({entry_id}) is declined without a note")
    return problems


def ledger_problems(ledger: object) -> list[str]:
    """Return what is wrong with the ledger's shape.

    Args:
        ledger: The ledger as parsed from JSON.

    Returns:
        One message per problem; empty when the ledger is well formed.
    """
    if not isinstance(ledger, dict):
        return ["the ledger is not a JSON object"]
    problems: list[str] = []
    if ledger.get("schema") != LEDGER_SCHEMA:
        problems.append(f"schema is {ledger.get('schema')!r}, not {LEDGER_SCHEMA}")
    commit = ledger.get("bmlib_commit")
    # actions/checkout takes the value as its ref, and a commit ref must be
    # the full SHA there: an abbreviated one fails the checkout.
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        problems.append(f"bmlib_commit {commit!r} is not a full 40-character SHA")
    for key, platforms in (("from_bmlib", PLATFORMS), ("to_bmlib", ("bmlib",))):
        entries = ledger.get(key)
        if not isinstance(entries, list):
            problems.append(f"{key} is not a list")
            continue
        for index, entry in enumerate(entries):
            problems.extend(_entry_problems(entry, f"{key}[{index}]", platforms))
        ids = [e.get("id") for e in entries if isinstance(e, dict)]
        duplicates = sorted({str(i) for i in ids if ids.count(i) > 1})
        if duplicates:
            problems.append(f"{key} repeats ids: {', '.join(duplicates)}")
    return problems


def owed_counts(ledger: dict[str, Any]) -> dict[str, int]:
    """Count the entries still owed on each platform.

    Args:
        ledger: The parsed ledger, already found well formed.

    Returns:
        ``from_bmlib`` owed counts per platform, plus ``bmlib`` for the
        ``to_bmlib`` entries owed to bmlib.
    """
    counts = {
        platform: sum(1 for e in ledger["from_bmlib"] if e[platform] == OWED)
        for platform in PLATFORMS
    }
    counts["bmlib"] = sum(1 for e in ledger["to_bmlib"] if e["bmlib"] == OWED)
    return counts


def _read_ledger(path: Path) -> dict[str, Any]:
    """Read and validate the ledger.

    Args:
        path: The ledger's path.

    Returns:
        The parsed ledger.

    Raises:
        UnreadableInput: The file can't be read or the ledger is malformed.
    """
    try:
        ledger: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UnreadableInput(f"cannot read the ledger: {exc}") from exc
    problems = ledger_problems(ledger)
    if problems:
        raise UnreadableInput(
            f"{path.name} is malformed:\n  " + "\n  ".join(problems))
    return ledger


def _check(bmlib: Path, ledger_path: Path) -> int:
    """Compare a bmlib checkout's changelog with the ledger and report.

    Args:
        bmlib: The bmlib checkout.
        ledger_path: The ledger's path.

    Returns:
        ``EXIT_OK`` or ``EXIT_MISSING``.

    Raises:
        UnreadableInput: An input can't be read.
    """
    ledger = _read_ledger(ledger_path)
    try:
        changelog = (bmlib.expanduser() / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise UnreadableInput(f"cannot read the changelog: {exc}") from exc

    missing = missing_entries(changelog, ledger)
    titles = missing_titles(changelog, ledger)
    if missing:
        print(f"{len(missing)} bmlib issue(s) in CHANGELOG.md have no "
              f"from_bmlib entry in {ledger_path.name}:")
        for entry_id in missing:
            print(f"  {entry_id}")
    if titles:
        print(f"CHANGELOG.md entries naming no issue that have no bmlib:<slug> "
              f"entry with their title in {ledger_path.name} ({len(titles)}):")
        for title in titles:
            print(f"  {title}")
    counts = owed_counts(ledger)
    print("owed: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return EXIT_MISSING if missing or titles else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Run the check and print what is missing and what is owed.

    Args:
        argv: Command-line arguments; ``sys.argv[1:]`` when omitted.

    Returns:
        0 when every change is ledgered, 1 when some are missing, 2 when an
        input can't be read.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bmlib", type=Path, help="path to a bmlib checkout")
    mode.add_argument("--print-commit", action="store_true",
                      help="validate the ledger and print its bmlib_commit")
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH,
                        help="path to ledger.json")
    args = parser.parse_args(argv)
    try:
        if args.print_commit:
            print(_read_ledger(args.ledger)["bmlib_commit"])
            return EXIT_OK
        return _check(args.bmlib, args.ledger)
    except UnreadableInput as exc:
        print(f"cannot check the ledger: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE


if __name__ == "__main__":
    sys.exit(main())
