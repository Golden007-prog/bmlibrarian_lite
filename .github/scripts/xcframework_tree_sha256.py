#!/usr/bin/env python3
"""Compute (or check) the tree hash of a committed xcframework.

``Packages/BioMedLit/Frameworks/BmlibFFI.pin.json`` records ``treeSha256``,
one SHA-256 over every file in ``BmlibFFI.xcframework``. A rebuild from the
pinned bmlib commit should reproduce it; this script is the one definition of
how it is computed, so a CI rebuild-and-compare guard and a person checking by
hand cannot disagree over the recipe.

The recipe:

1. Walk the xcframework without following symlinks.
2. For each regular file, one line ``<path> <sha256 of its bytes>``; for each
   symlink (file or directory), one line ``<path> -> <link target>``.
   ``<path>`` is relative to the xcframework's *parent* directory and uses
   ``/``, so every line starts with ``BmlibFFI.xcframework/``.
3. Sort the lines (code-point order), join them with a line feed (LF), append
   one trailing LF, and hash the UTF-8 bytes.

Usage::

    xcframework_tree_sha256.py <path/to/X.xcframework>
    xcframework_tree_sha256.py <path/to/X.xcframework> --check <pin.json>

With ``--check`` it exits 1 when the computed hash differs from the pin's
``treeSha256`` (and 2 when the pin cannot be read).
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

PIN_HASH_KEY = "treeSha256"
EXIT_MISMATCH = 1
EXIT_BAD_PIN = 2


def tree_lines(xcframework: Path) -> list[str]:
    """List the hashed lines for every file and symlink under an xcframework.

    Args:
        xcframework: The ``.xcframework`` directory.

    Returns:
        The lines, sorted, one per regular file or symlink.

    Raises:
        NotADirectoryError: When ``xcframework`` is not a directory.
    """
    if not xcframework.is_dir():
        raise NotADirectoryError(f"not an xcframework directory: {xcframework}")
    base = xcframework.parent
    lines: list[str] = []
    for directory, subdirectories, files in os.walk(xcframework, followlinks=False):
        # os.walk lists a symlink to a directory among the subdirectories and
        # does not descend into it, so both lists are checked for links.
        for name in subdirectories + files:
            path = Path(directory) / name
            relative = path.relative_to(base).as_posix()
            if path.is_symlink():
                lines.append(f"{relative} -> {os.readlink(path)}")
            elif path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                lines.append(f"{relative} {digest}")
    return sorted(lines)


def tree_sha256(xcframework: Path) -> str:
    """Compute an xcframework's tree hash, as ``BmlibFFI.pin.json`` records it.

    Args:
        xcframework: The ``.xcframework`` directory.

    Returns:
        The hex SHA-256 of the sorted, newline-terminated lines.
    """
    text = "\n".join(tree_lines(xcframework)) + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pinned_tree_sha256(pin: Path) -> str:
    """Read ``treeSha256`` from a pin file.

    Args:
        pin: The pin JSON file.

    Returns:
        The pinned hash.

    Raises:
        ValueError: When the pin is not a JSON object with a string ``treeSha256``.
    """
    data = json.loads(pin.read_text(encoding="utf-8"))
    value = data.get(PIN_HASH_KEY) if isinstance(data, dict) else None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{pin} has no string {PIN_HASH_KEY!r}")
    return value


def main(argv: list[str]) -> int:
    """Print the tree hash, or check it against a pin.

    Args:
        argv: Command-line arguments, without the program name.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("xcframework", type=Path)
    parser.add_argument("--check", type=Path, metavar="PIN_JSON")
    args = parser.parse_args(argv)

    actual = tree_sha256(args.xcframework)
    if args.check is None:
        print(actual)
        return 0
    try:
        expected = pinned_tree_sha256(args.check)
    except (OSError, ValueError) as error:
        print(f"::error::cannot read the pin: {error}", file=sys.stderr)
        return EXIT_BAD_PIN
    if actual != expected:
        print(
            f"::error::{args.xcframework} tree hash {actual} differs from "
            f"{args.check}'s {PIN_HASH_KEY} {expected}",
            file=sys.stderr,
        )
        return EXIT_MISMATCH
    print(f"{args.xcframework}: {actual} matches {args.check}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
