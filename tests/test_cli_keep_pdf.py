"""``bmll keep-pdf``: the reader's override of the library's title check (#541)."""

from pathlib import Path

import fitz
import pytest

from bmlibrarian_lite.cli import cmd_keep_pdf, create_parser
from bmlibrarian_lite.constants import PDF_MISMATCH_SUFFIX
from bmlibrarian_lite.pdf_library_check import is_kept


def _set_aside_pdf(tmp_path: Path) -> Path:
    """A PDF the title check set aside."""
    document = fitz.open()
    document.new_page().insert_text((50, 72), "A preprint retitled since")
    path = tmp_path / f"a.pdf{PDF_MISMATCH_SUFFIX}"
    document.save(str(path))
    document.close()
    return path


def _run(*argv: str) -> int:
    """Parse and run one command."""
    args = create_parser().parse_args(list(argv))
    return int(args.func(args))


def test_keep_pdf_puts_a_set_aside_pdf_back_and_marks_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Parsed as the reader types it; the PDF is back, and kept."""
    moved = _set_aside_pdf(tmp_path)

    assert _run("keep-pdf", str(moved)) == 0

    assert is_kept(tmp_path / "a.pdf")
    assert "Kept" in capsys.readouterr().out


def test_keep_pdf_refuses_a_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Nothing to keep: said, and a failing exit code."""
    assert _run("keep-pdf", str(tmp_path / "nothing.pdf.mismatch")) == 1
    assert "No such file" in capsys.readouterr().err


def test_keep_pdf_refuses_a_pdf_in_the_way(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Another PDF at the name to go back to is never replaced."""
    moved = _set_aside_pdf(tmp_path)
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.7\n")

    assert cmd_keep_pdf(create_parser().parse_args(["keep-pdf", str(moved)])) == 1

    assert (tmp_path / "a.pdf").read_bytes() == b"%PDF-1.7\n"
    assert moved.is_file()
    assert "already in the library" in capsys.readouterr().err
