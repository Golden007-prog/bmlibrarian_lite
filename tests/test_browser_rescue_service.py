"""BrowserRescueService behaviour that needs no browser (#480, stage 2)."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from bmlibrarian_lite.browser_rescue import RescueOutcome, RescueVerdict
from bmlibrarian_lite.constants import RESCUE_RETRY_DELAY_SECONDS
from bmlibrarian_lite.gui.browser_rescue_service import BrowserRescueService


class _FailingStorage:
    """A queue whose reads work and whose writes fail."""

    def __init__(self, entry: Any) -> None:
        self.entry = entry
        self.writes = 0

    def next_rescue_entry(self) -> Any:
        return self.entry

    def record_rescue_verdict(self, entry_id: int, verdict: RescueVerdict) -> Any:
        self.writes += 1
        raise sqlite3.OperationalError("database is locked")


def test_a_failed_verdict_write_pauses_the_pass_for_the_retry_delay(tmp_path: Path) -> None:
    """After a failed write the entry is not offered again until the delay has passed."""
    entry = SimpleNamespace(id=1, request=SimpleNamespace(host="example.org"))
    now = [1_000.0]
    config = SimpleNamespace(discovery=SimpleNamespace(browser_rescue_hidden=True))
    service = BrowserRescueService(
        _FailingStorage(entry), config, tmp_path, clock=lambda: now[0]  # type: ignore[arg-type]
    )
    assert service._next_entry() is entry
    service._busy = True
    service._on_finished(entry, RescueVerdict(RescueOutcome.PDF))  # type: ignore[arg-type]
    assert not service.busy
    assert service._next_entry() is None
    now[0] += RESCUE_RETRY_DELAY_SECONDS - 1
    assert service._next_entry() is None
    now[0] += 1
    assert service._next_entry() is entry
