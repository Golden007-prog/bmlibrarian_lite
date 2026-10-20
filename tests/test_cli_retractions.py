"""``bmll retractions-update``: refresh the local Retraction Watch copy."""

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pytest

from bmlibrarian_lite import LiteConfig, retraction_export
from bmlibrarian_lite.cli import create_parser
from bmlibrarian_lite.constants import RETRACTION_WATCH_DB_NAME
from bmlibrarian_lite.retraction_export import ExportCopy, ExportState


def _config(tmp_path: Path) -> LiteConfig:
    """A configuration whose data directory is the test's temporary directory."""
    config = LiteConfig()
    config.storage.data_dir = tmp_path
    return config


def _run(*argv: str) -> int:
    """Parse and run one command."""
    args = create_parser().parse_args(list(argv))
    return int(args.func(args))


def _stub_refresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    copy: Callable[[date], ExportCopy],
    builds: bool = False,
) -> list[date]:
    """Stub the refresh (never the network) and the configuration; return the days it was asked for.

    The copy is built from the day the handler asked with, so a run across midnight cannot fail.
    With ``builds`` the stub swaps in a new copy file, as a refresh that succeeded does.
    """
    asked: list[date] = []

    def fake_refresh(data_dir: Path, session: object, *, mailto: str, today: date) -> ExportCopy:
        asked.append(today)
        if builds:
            new = tmp_path / "new.db"
            new.write_bytes(b"a new copy")
            new.replace(tmp_path / RETRACTION_WATCH_DB_NAME)
        return copy(today)

    monkeypatch.setattr(retraction_export, "refresh_export", fake_refresh)
    monkeypatch.setattr(LiteConfig, "load", classmethod(lambda cls, config_path=None: _config(tmp_path)))
    return asked


def test_update_refreshes_and_reports_the_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fresh current copy exits 0 and names its data_as_of."""
    asked = _stub_refresh(
        monkeypatch, tmp_path, lambda today: ExportCopy(ExportState.CURRENT, today), builds=True
    )

    assert _run("retractions-update") == 0

    assert len(asked) == 1
    assert asked[0].isoformat() in capsys.readouterr().out


def test_update_failure_exits_1_and_names_the_kept_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refresh that kept the old copy exits 1, names its date, and leaks no provider text."""
    asked = _stub_refresh(
        monkeypatch, tmp_path, lambda today: ExportCopy(ExportState.CURRENT, today - timedelta(days=1))
    )

    assert _run("retractions-update") == 1

    out = capsys.readouterr()
    assert (asked[0] - timedelta(days=1)).isoformat() in out.out
    assert out.err == ""


def test_update_failure_without_any_copy_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No copy to keep: exits 1 and says there is none."""
    _stub_refresh(monkeypatch, tmp_path, lambda _today: ExportCopy(ExportState.MISSING, None))

    assert _run("retractions-update") == 1

    out = capsys.readouterr().out
    assert "no copy" in out.lower()
    assert "missing" in out


@pytest.mark.parametrize(
    ("state", "word"),
    [(ExportState.STALE, "out of date"), (ExportState.UNREADABLE, "unreadable")],
)
def test_update_names_the_kept_copys_state_in_words(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    state: ExportState,
    word: str,
) -> None:
    """The reader reads "out of date", never the enum value "stale"."""
    stale = state is ExportState.STALE  # an unreadable copy has no date
    _stub_refresh(
        monkeypatch, tmp_path, lambda today: ExportCopy(state, today - timedelta(days=40) if stale else None)
    )

    assert _run("retractions-update") == 1

    out = capsys.readouterr().out
    assert word in out
    assert "stale" not in out


def test_a_failed_refresh_after_an_earlier_one_today_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Today's copy from an earlier run is kept, but this run built nothing."""
    (tmp_path / RETRACTION_WATCH_DB_NAME).write_bytes(b"this morning's copy")
    _stub_refresh(monkeypatch, tmp_path, lambda today: ExportCopy(ExportState.CURRENT, today))

    assert _run("retractions-update") == 1

    assert "did not produce a new copy" in capsys.readouterr().out
