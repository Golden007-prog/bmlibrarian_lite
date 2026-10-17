"""Tests for the xcframework tree hash (.github/scripts/xcframework_tree_sha256.py)."""

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
SCRIPT_PATH = PROJECT_ROOT / ".github" / "scripts" / "xcframework_tree_sha256.py"
FRAMEWORKS = PROJECT_ROOT / "Packages" / "BioMedLit" / "Frameworks"
COMMITTED_XCFRAMEWORK = FRAMEWORKS / "BmlibFFI.xcframework"
COMMITTED_PIN = FRAMEWORKS / "BmlibFFI.pin.json"


def _load_script() -> ModuleType:
    """Load xcframework_tree_sha256.py as a module (.github/scripts is not a package).

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location("xcframework_tree_sha256", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["xcframework_tree_sha256"] = module
    spec.loader.exec_module(module)
    return module


tree = _load_script()


def _make_xcframework(tmp_path: Path) -> Path:
    """Build a tiny xcframework: two files, a file symlink and a directory symlink.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        The xcframework directory.
    """
    root = tmp_path / "X.xcframework"
    versions = root / "macos" / "X.framework" / "Versions" / "A"
    versions.mkdir(parents=True)
    (versions / "X").write_bytes(b"binary")
    (root / "Info.plist").write_bytes(b"plist")
    framework = root / "macos" / "X.framework"
    os.symlink("A", framework / "Versions" / "Current")
    os.symlink("Versions/Current/X", framework / "X")
    return root


def test_lines_are_parent_relative_sorted_and_name_symlink_targets(tmp_path: Path) -> None:
    """Every line starts with the xcframework's name; links are written, not followed."""
    lines = tree.tree_lines(_make_xcframework(tmp_path))
    sha = hashlib.sha256
    assert lines == [
        f"X.xcframework/Info.plist {sha(b'plist').hexdigest()}",
        f"X.xcframework/macos/X.framework/Versions/A/X {sha(b'binary').hexdigest()}",
        "X.xcframework/macos/X.framework/Versions/Current -> A",
        "X.xcframework/macos/X.framework/X -> Versions/Current/X",
    ]


def test_hash_is_over_newline_joined_lines_with_a_trailing_newline(tmp_path: Path) -> None:
    """The hashed text ends in exactly one newline (the pin's recipe)."""
    root = _make_xcframework(tmp_path)
    expected = hashlib.sha256(("\n".join(tree.tree_lines(root)) + "\n").encode()).hexdigest()
    assert tree.tree_sha256(root) == expected


def test_changing_one_byte_or_one_link_changes_the_hash(tmp_path: Path) -> None:
    """A modified binary, or a retargeted symlink, no longer matches."""
    root = _make_xcframework(tmp_path)
    before = tree.tree_sha256(root)
    binary = root / "macos" / "X.framework" / "Versions" / "A" / "X"
    binary.write_bytes(b"binarY")
    after_byte = tree.tree_sha256(root)
    assert after_byte != before
    link = root / "macos" / "X.framework" / "Versions" / "Current"
    link.unlink()
    os.symlink("B", link)
    assert tree.tree_sha256(root) != after_byte


def test_not_a_directory_is_refused(tmp_path: Path) -> None:
    """A missing xcframework is an error, not the hash of an empty tree."""
    with pytest.raises(NotADirectoryError):
        tree.tree_lines(tmp_path / "missing.xcframework")


def test_committed_xcframework_matches_its_pin() -> None:
    """The committed BmlibFFI.xcframework reproduces the pin's treeSha256."""
    pinned = json.loads(COMMITTED_PIN.read_text(encoding="utf-8"))["treeSha256"]
    assert tree.tree_sha256(COMMITTED_XCFRAMEWORK) == pinned
    assert tree.main([str(COMMITTED_XCFRAMEWORK), "--check", str(COMMITTED_PIN)]) == 0


def test_check_reports_a_mismatch_and_an_unreadable_pin(tmp_path: Path) -> None:
    """--check exits 1 on a different hash and 2 when the pin has no hash."""
    root = _make_xcframework(tmp_path)
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"treeSha256": "0" * 64}), encoding="utf-8")
    assert tree.main([str(root), "--check", str(wrong)]) == tree.EXIT_MISMATCH
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    assert tree.main([str(root), "--check", str(empty)]) == tree.EXIT_BAD_PIN
    right = tmp_path / "right.json"
    right.write_text(json.dumps({"treeSha256": tree.tree_sha256(root)}), encoding="utf-8")
    assert tree.main([str(root), "--check", str(right)]) == 0
