"""Tests for the bmlib sync ledger and its CI checker.

The ledger (``doc/cross_platform/bmlib_sync/ledger.json``) records, for every
change in bmlib's CHANGELOG, what became of it on each platform here.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "doc" / "cross_platform" / "bmlib_sync" / "ledger.json"
SCRIPT_PATH = ROOT / "scripts" / "check_bmlib_ledger.py"


def _load_script() -> ModuleType:
    """Import the checker script by path (``scripts/`` is not a package)."""
    spec = importlib.util.spec_from_file_location("check_bmlib_ledger", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # A dataclass resolves its string annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_script = _load_script()
changelog_issue_ids = _script.changelog_issue_ids
missing_entries = _script.missing_entries
missing_titles = _script.missing_titles
ledger_problems = _script.ledger_problems
owed_counts = _script.owed_counts
main = _script.main
UnreadableInput = _script.UnreadableInput

OLDEST = "## [0.10.0] — 2026-08-15\n- **Old entry** (#79).\n"

CHANGELOG = """## [Unreleased]
### Fixed
- **The PubMed record read is the one asked about** (`transparency`, #474).
  Body mentioning #999 in prose only.
""" + OLDEST

# A title whose parenthesised group runs over several lines, and a pull
# request number inside the group, which is not an issue.
WRAPPED = """## [Unreleased]
- **An article's funding is stored** (issue
  #284, found by PR #256's review).
  Prose naming #1000.
- **A title (with parens) of its own** (#12, #13).
- **No group follows this title.** Prose naming #2000.
""" + OLDEST

IDLESS = """## [Unreleased]
- **An identifier is read from the type the document declares, not from its
  shape.** `JATSParser` took any `<article-id>` beginning `10.` as the DOI.
- **A titled change** (#5).
""" + OLDEST


def _entry(entry_id: str, title: str = "t", status: str = "n/a") -> dict[str, Any]:
    """A well-formed ``from_bmlib`` entry."""
    return {"id": entry_id, "title": title, "python": status, "swift": status,
            "android": status, "issue": None, "note": ""}


def _ledger(*entries: dict[str, Any]) -> dict[str, Any]:
    """A well-formed ledger holding ``entries``."""
    return {"schema": 1, "bmlib_commit": "0" * 40, "from_bmlib": list(entries),
            "to_bmlib": [{"id": "lite:x", "title": "x", "bmlib": "owed",
                          "issue": None, "note": ""}]}


# --- Reading the changelog -------------------------------------------------

def test_ids_come_from_entry_titles_newer_than_0_10_0() -> None:
    """Only title groups newer than 0.10.0 give ids."""
    assert changelog_issue_ids(CHANGELOG) == {"bmlib#474"}


def test_an_unledgered_id_is_missing() -> None:
    """An id with no ledger entry is reported."""
    assert missing_entries(CHANGELOG, {"from_bmlib": []}) == ["bmlib#474"]


def test_a_ledgered_id_is_not_missing() -> None:
    """An id with a ledger entry is not reported."""
    ledger = {"from_bmlib": [{"id": "bmlib#474"}]}
    assert missing_entries(CHANGELOG, ledger) == []


def test_a_group_wrapped_over_lines_is_read_and_prose_is_not() -> None:
    """A wrapped group is read, a PR number and body prose are not."""
    assert changelog_issue_ids(WRAPPED) == {"bmlib#284", "bmlib#12", "bmlib#13"}


def test_pull_requests_in_a_title_group_are_not_issues() -> None:
    """A PR list inside the title group is not read as issues."""
    text = (
        "## [Unreleased]\n"
        "- **Metadata is not prose** (issues #241 and #248, filed by the reviews\n"
        "  of PRs #239 and #246).\n" + OLDEST
    )
    assert changelog_issue_ids(text) == {"bmlib#241", "bmlib#248"}


def test_an_unclosed_group_runs_to_the_end_of_the_paragraph() -> None:
    """A group never closed is still read, up to the paragraph's end."""
    text = (
        "## [Unreleased]\n"
        "- **Never closed** (#5, and\n"
        "  #6 on the next line\n"
        "\n"
        "  A later paragraph naming #7.\n" + OLDEST
    )
    assert changelog_issue_ids(text) == {"bmlib#5", "bmlib#6"}


@pytest.mark.parametrize("group", ["#165-#170", "#165–#170", "#165-170", "#165 – #170"])
def test_a_range_names_every_issue_in_it(group: str) -> None:
    """``#a-#b`` and its en-dash form name every issue from a to b."""
    text = f"## [Unreleased]\n- **Six ways** ({group}, from the review of PR #163).\n" + OLDEST
    assert changelog_issue_ids(text) == {f"bmlib#{n}" for n in range(165, 171)}


@pytest.mark.parametrize("group", ["#170-#165", "#100-#200"])
def test_a_backwards_or_overwide_range_is_unreadable(group: str) -> None:
    """A range that runs backwards or past the cap is refused, not guessed."""
    text = f"## [Unreleased]\n- **Bad range** ({group}).\n" + OLDEST
    with pytest.raises(UnreadableInput):
        changelog_issue_ids(text)


@pytest.mark.parametrize("text", [
    "",
    "## [Unreleased]\n- **A fix** (#9999).\n",
    "## [Unreleased]\n### Fixed\n" + OLDEST,
    "## [Unreleased]\n* **A fix** (#9999).\n" + OLDEST,
    "## [Unreleased]\n- **A fix** (#1).\n+ **Another** (#2).\n" + OLDEST,
    "## [Unreleased]\n- **A title never closed (#1).\n" + OLDEST,
], ids=["empty", "no-oldest-heading", "no-entries", "star-bullets",
        "one-plus-bullet", "unclosed-bold"])
def test_a_changelog_the_script_cannot_parse_is_unreadable(text: str) -> None:
    """A layout the script can't read raises rather than passing as empty."""
    with pytest.raises(UnreadableInput):
        changelog_issue_ids(text)


# --- Entries that name no issue --------------------------------------------

def test_an_idless_entry_without_a_titled_ledger_entry_is_missing() -> None:
    """An entry naming no issue is reported by its title."""
    assert missing_titles(IDLESS, _ledger()) == [
        "An identifier is read from the type the document declares, not from its shape"]


def test_an_idless_entry_is_matched_by_a_bmlib_slug_entry_of_its_title() -> None:
    """A ``bmlib:<slug>`` entry carrying the title accounts for the entry."""
    ledger = _ledger(_entry(
        "bmlib:unreleased-identifier-from-declared-type",
        "An identifier is read from the type the document declares,\n not from its shape."))
    assert missing_titles(IDLESS, ledger) == []


def test_a_lite_entry_does_not_account_for_an_idless_bmlib_entry() -> None:
    """Only a ``bmlib:`` entry stands for a bmlib change."""
    ledger = _ledger(_entry(
        "lite:x", "An identifier is read from the type the document declares, "
        "not from its shape"))
    assert len(missing_titles(IDLESS, ledger)) == 1


# --- The ledger's shape -----------------------------------------------------

def test_the_committed_ledger_is_well_formed() -> None:
    """The committed ledger passes the script's own shape check."""
    assert ledger_problems(json.loads(LEDGER_PATH.read_text())) == []


def test_a_well_formed_ledger_has_no_problems() -> None:
    """The control for the malformed cases below."""
    assert ledger_problems(_ledger(_entry("bmlib#1"))) == []


def _broken(change: str) -> object:
    """A ledger broken in one named way."""
    ledger = _ledger(_entry("bmlib#1"), _entry("bmlib:x-y", status="declined"))
    ledger["from_bmlib"][1]["note"] = "a reason"
    if change == "top-level list":
        return []
    broken = copy.deepcopy(ledger)
    if change == "no from_bmlib":
        del broken["from_bmlib"]
    elif change == "no platform key":
        del broken["from_bmlib"][0]["android"]
    elif change == "bad status":
        broken["from_bmlib"][0]["python"] = "owd"
    elif change == "declined without note":
        broken["from_bmlib"][1]["note"] = ""
    elif change == "duplicate id":
        broken["from_bmlib"].append(_entry("bmlib#1"))
    elif change == "short commit":
        broken["bmlib_commit"] = "a368a67"
    elif change == "bad id":
        broken["from_bmlib"][0]["id"] = "bmlib#1 "
    elif change == "no title":
        broken["from_bmlib"][0]["title"] = " "
    elif change == "to_bmlib without bmlib":
        del broken["to_bmlib"][0]["bmlib"]
    elif change == "schema":
        broken["schema"] = 2
    return broken


@pytest.mark.parametrize("change", [
    "top-level list", "no from_bmlib", "no platform key", "bad status",
    "declined without note", "duplicate id", "short commit", "bad id",
    "no title", "to_bmlib without bmlib", "schema",
])
def test_a_malformed_ledger_has_problems(change: str) -> None:
    """Each way of breaking the ledger is named, rather than crashing later."""
    assert ledger_problems(_broken(change))


def test_owed_counts_count_each_platform_and_bmlib() -> None:
    """Owed rows are counted per platform, and ``to_bmlib`` as ``bmlib``."""
    ledger = _ledger(_entry("bmlib#1", status="owed"), _entry("bmlib#2"))
    ledger["from_bmlib"][1]["swift"] = "owed"
    assert owed_counts(ledger) == {"python": 1, "swift": 2, "android": 1, "bmlib": 1}


# --- The command line -------------------------------------------------------

def _write(tmp_path: Path, changelog: str, ledger: object) -> tuple[Path, Path]:
    """Write a bmlib checkout holding ``changelog`` and a ledger file."""
    bmlib = tmp_path / "bmlib"
    bmlib.mkdir()
    (bmlib / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(
        ledger if isinstance(ledger, str) else json.dumps(ledger), encoding="utf-8")
    return bmlib, ledger_path


def _run(tmp_path: Path, changelog: str, ledger: object) -> int:
    """Run ``main`` against a written checkout and ledger."""
    bmlib, ledger_path = _write(tmp_path, changelog, ledger)
    status: int = main(["--bmlib", str(bmlib), "--ledger", str(ledger_path)])
    return status


def test_main_exits_0_when_everything_is_ledgered(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Every id and every id-less title ledgered: exit 0, owed counts printed."""
    ledger = _ledger(
        _entry("bmlib#5", status="owed"),
        _entry("bmlib:unreleased-identifier-from-declared-type",
               "An identifier is read from the type the document declares, "
               "not from its shape"))
    assert _run(tmp_path, IDLESS, ledger) == 0
    assert "owed: python=1, swift=1, android=1, bmlib=1" in capsys.readouterr().out


def test_main_exits_1_and_names_a_missing_id(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An unledgered id: exit 1, the id printed."""
    assert _run(tmp_path, CHANGELOG, _ledger()) == 1
    assert "bmlib#474" in capsys.readouterr().out


def test_main_exits_1_and_names_a_missing_idless_title(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An unledgered id-less entry: exit 1, its title printed."""
    assert _run(tmp_path, IDLESS, _ledger(_entry("bmlib#5"))) == 1
    assert "An identifier is read from the type" in capsys.readouterr().out


@pytest.mark.parametrize("changelog", [
    "",
    "## [Unreleased]\n* **New JATS fix** (#9999).\n" + OLDEST,
], ids=["empty", "star-bullets"])
def test_main_exits_2_on_an_unparseable_changelog(
        tmp_path: Path, changelog: str, capsys: pytest.CaptureFixture[str]) -> None:
    """A changelog read as no entries is an error, not "nothing missing"."""
    assert _run(tmp_path, changelog, _ledger()) == 2
    assert "cannot check the ledger" in capsys.readouterr().err


@pytest.mark.parametrize("ledger", [
    "{not json", [], _broken("no from_bmlib"), _broken("no platform key"),
    _broken("bad status"),
], ids=["invalid-json", "list", "no-from-bmlib", "no-platform-key", "bad-status"])
def test_main_exits_2_on_a_malformed_ledger(tmp_path: Path, ledger: object) -> None:
    """A malformed ledger is unreadable (2), never a crash or a miss (1)."""
    assert _run(tmp_path, CHANGELOG, ledger) == 2


def test_main_exits_2_without_a_changelog(tmp_path: Path) -> None:
    """A checkout with no CHANGELOG.md is unreadable."""
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(_ledger()), encoding="utf-8")
    assert main(["--bmlib", str(tmp_path), "--ledger", str(ledger_path)]) == 2


def test_print_commit_prints_the_ledger_commit(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--print-commit`` prints the validated SHA alone."""
    _, ledger_path = _write(tmp_path, "", _ledger())
    assert main(["--print-commit", "--ledger", str(ledger_path)]) == 0
    assert capsys.readouterr().out == "0" * 40 + "\n"


def test_print_commit_exits_2_on_a_malformed_ledger(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CI never checks bmlib out at a ref read from a malformed ledger."""
    _, ledger_path = _write(tmp_path, "", _broken("short commit"))
    assert main(["--print-commit", "--ledger", str(ledger_path)]) == 2
    assert capsys.readouterr().out == ""
