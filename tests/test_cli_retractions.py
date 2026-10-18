"""``bmll retractions-update``: refresh the local Retraction Watch copy."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from bmlibrarian_lite import LiteConfig, retraction_export
from bmlibrarian_lite.cli import create_parser
from bmlibrarian_lite.retraction_export import ExportCopy, ExportState

TODAY = datetime.now(UTC).date()
YESTERDAY = date.fromordinal(TODAY.toordinal() - 1)


def _config(tmp_path: Path) -> LiteConfig:
    """A configuration whose data directory is the test's temporary directory."""
    config = LiteConfig()
    config.storage.data_dir = tmp_path
    return config


def _run(*argv: str) -> int:
    """Parse and run one command."""
    args = create_parser().parse_args(list(argv))
    return int(args.func(args))


def _stub_refresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, copy: ExportCopy) -> list[date]:
    """Stub the refresh (never the network) and the configuration; return the days it was asked for."""
    asked: list[date] = []

    def fake_refresh(data_dir: Path, session: object, *, mailto: str, today: date) -> ExportCopy:
        asked.append(today)
        return copy

    monkeypatch.setattr(retraction_export, "refresh_export", fake_refresh)
    monkeypatch.setattr(LiteConfig, "load", classmethod(lambda cls, config_path=None: _config(tmp_path)))
    return asked


def test_update_refreshes_and_reports_the_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fresh current copy exits 0 and names its data_as_of."""
    asked = _stub_refresh(monkeypatch, tmp_path, ExportCopy(ExportState.CURRENT, TODAY))

    assert _run("retractions-update") == 0

    assert asked == [TODAY]
    assert TODAY.isoformat() in capsys.readouterr().out


def test_update_failure_exits_1_and_names_the_kept_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refresh that kept the old copy exits 1, names its date, and leaks no provider text."""
    _stub_refresh(monkeypatch, tmp_path, ExportCopy(ExportState.CURRENT, YESTERDAY))

    assert _run("retractions-update") == 1

    out = capsys.readouterr()
    assert YESTERDAY.isoformat() in out.out
    assert out.err == ""


def test_update_failure_without_any_copy_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No copy to keep: exits 1 and says there is none."""
    _stub_refresh(monkeypatch, tmp_path, ExportCopy(ExportState.MISSING, None))

    assert _run("retractions-update") == 1

    assert "no copy" in capsys.readouterr().out.lower()
