"""BrowserRescueService behaviour that needs no browser (#480, stage 2).

The service is driven by calling its handlers directly; the shared
QApplication is never pumped (#516, #517). Its retry timer is a child of the
service, and ``wake`` does nothing on a service that was never started, so a
timer left over from a test cannot act on later ones.
"""

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bmlibrarian_lite.browser_rescue import RescueOutcome, RescueState, RescueVerdict
from bmlibrarian_lite.constants import (
    RESCUE_RETRY_DELAY_SECONDS,
    RESCUE_VERDICT_SAVE_RETRY_SECONDS,
)
from bmlibrarian_lite.gui.browser_rescue_service import BrowserRescueService

PDF = RescueVerdict(RescueOutcome.PDF)


@pytest.fixture
def qapp():
    """A QApplication for constructing the service; never pumped."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Storage:
    """A queue whose verdict writes fail as often as asked, then succeed."""

    def __init__(self, entry: Any, failures: int) -> None:
        self.entry = entry
        self.failures = failures
        self.reads = 0
        self.writes: list[tuple[int, RescueVerdict]] = []

    def next_rescue_entry(self) -> Any:
        self.reads += 1
        return self.entry

    def record_rescue_verdict(self, entry_id: int, verdict: RescueVerdict) -> Any:
        self.writes.append((entry_id, verdict))
        if self.failures:
            self.failures -= 1
            raise sqlite3.OperationalError("database is locked")
        return SimpleNamespace(id=entry_id, state=RescueState.RESCUED, request=self.entry.request)


def _service(tmp_path: Path, storage: _Storage, now: list[float]) -> BrowserRescueService:
    config = SimpleNamespace(discovery=SimpleNamespace(browser_rescue_hidden=True))
    return BrowserRescueService(
        storage, config, tmp_path, clock=lambda: now[0]  # type: ignore[arg-type]
    )


def _entry() -> Any:
    return SimpleNamespace(id=1, request=SimpleNamespace(host="example.org"))


def _finish_visit(service: BrowserRescueService, entry: Any) -> None:
    """End a visit as the attempt's ``finished`` signal would."""
    service._busy = True
    service._on_finished(entry, PDF)


def test_an_unsaved_verdict_is_saved_again_not_the_publisher_visited_again(
    qapp, tmp_path: Path
) -> None:
    """A brief lock costs one short wait and no second visit."""
    entry = _entry()
    storage = _Storage(entry, failures=1)
    now = [1_000.0]
    service = _service(tmp_path, storage, now)
    rescued: list[Any] = []
    service.rescued.connect(rescued.append)

    _finish_visit(service, entry)
    assert not service.busy
    assert rescued == []
    # While the verdict is unsaved no entry is taken, so nothing is visited
    now[0] += RESCUE_VERDICT_SAVE_RETRY_SECONDS - 1
    assert service._next_entry() is None
    assert storage.reads == 0
    assert len(storage.writes) == 1

    now[0] += 1
    assert service._next_entry() is entry
    assert storage.writes == [(1, PDF), (1, PDF)]
    assert [e.id for e in rescued] == [1]


def test_a_lasting_failure_is_retried_ever_more_rarely(qapp, tmp_path: Path) -> None:
    """The wait doubles from the first retry delay up to the transit retry delay."""
    entry = _entry()
    storage = _Storage(entry, failures=100)
    now = [1_000.0]
    service = _service(tmp_path, storage, now)
    _finish_visit(service, entry)

    waits = []
    for _ in range(8):
        waited = 0.0
        while service._next_entry() is None and len(storage.writes) == len(waits) + 1:
            now[0] += 1
            waited += 1
        waits.append(waited)
    expected = [
        min(RESCUE_VERDICT_SAVE_RETRY_SECONDS * 2**n, RESCUE_RETRY_DELAY_SECONDS) for n in range(8)
    ]
    assert waits == expected
    assert storage.reads == 0


def test_a_saved_verdict_resets_the_wait(qapp, tmp_path: Path) -> None:
    """After a success, the next failure waits the first delay again."""
    entry = _entry()
    storage = _Storage(entry, failures=2)
    now = [1_000.0]
    service = _service(tmp_path, storage, now)
    _finish_visit(service, entry)
    now[0] += RESCUE_VERDICT_SAVE_RETRY_SECONDS
    assert service._next_entry() is None  # second failure: waits twice as long
    now[0] += 2 * RESCUE_VERDICT_SAVE_RETRY_SECONDS
    assert service._next_entry() is entry  # saved

    storage.failures = 1
    _finish_visit(service, entry)
    now[0] += RESCUE_VERDICT_SAVE_RETRY_SECONDS - 1
    assert service._next_entry() is None
    now[0] += 1
    assert service._next_entry() is entry


def test_an_unsaved_verdict_is_saved_even_with_the_hidden_pass_off(qapp, tmp_path: Path) -> None:
    """Saving is bookkeeping for a visit already made, not a new visit."""
    entry = _entry()
    storage = _Storage(entry, failures=1)
    now = [1_000.0]
    service = _service(tmp_path, storage, now)
    _finish_visit(service, entry)
    service._config.discovery.browser_rescue_hidden = False
    now[0] += RESCUE_VERDICT_SAVE_RETRY_SECONDS
    assert service._next_entry() is None
    assert len(storage.writes) == 2
    assert storage.reads == 0


def test_quit_tries_once_more_to_save(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verdict still unsaved at quit gets one last attempt.

    ``shutdown`` flushes deferred deletes, which in the suite's shared
    QApplication would deliver earlier tests' backlog (#516); the flush is
    stubbed here, and the harness checks the real quit in its own process.
    """
    from bmlibrarian_lite.gui import browser_rescue_service

    monkeypatch.setattr(
        browser_rescue_service,
        "QCoreApplication",
        SimpleNamespace(sendPostedEvents=lambda *args: None),
    )
    entry = _entry()
    storage = _Storage(entry, failures=1)
    now = [1_000.0]
    service = _service(tmp_path, storage, now)
    _finish_visit(service, entry)
    service.shutdown()
    assert len(storage.writes) == 2
