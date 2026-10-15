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
from PySide6.QtCore import QObject, QTimer, Signal

from bmlibrarian_lite.browser_rescue import (
    RescueOutcome,
    RescueState,
    RescueVerdict,
    WalkEnd,
    WalkEndReason,
)
from bmlibrarian_lite.constants import (
    RESCUE_RETRY_DELAY_SECONDS,
    RESCUE_VERDICT_SAVE_RETRY_SECONDS,
)
from bmlibrarian_lite.gui import browser_rescue_service as service_module
from bmlibrarian_lite.gui.browser_rescue_service import BrowserRescueService
from bmlibrarian_lite.rescue_queue_store import RecordedVerdict

PDF = RescueVerdict(RescueOutcome.PDF)


@pytest.fixture
def qapp():
    """A QApplication for constructing the service; never pumped."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Storage:
    """A queue whose verdict writes fail as often as asked, then succeed.

    Each entry is pending until a verdict or the test says otherwise
    (``states``); a visit reads its entry again before it starts.
    """

    def __init__(self, entry: Any, failures: int, applied: bool = True) -> None:
        self.entry = entry
        self.failures = failures
        self.applied = applied
        self.reads = 0
        self.writes: list[tuple[int, RescueVerdict]] = []
        self.states: dict[int, RescueState] = {}

    def next_rescue_entry(self) -> Any:
        self.reads += 1
        return self.entry

    def rescue_entry(self, entry_id: int) -> Any:
        """The entry as it now stands: pending unless settled."""
        known = {1: _entry().request, 2: _other().request}
        if self.entry is not None:
            known[self.entry.id] = self.entry.request
        request = known.get(entry_id)
        if request is None:
            return None
        state = self.states.get(entry_id, RescueState.PENDING)
        return SimpleNamespace(id=entry_id, state=state, request=request)

    def record_rescue_verdict(self, entry_id: int, verdict: RescueVerdict) -> RecordedVerdict:
        self.writes.append((entry_id, verdict))
        if self.failures:
            self.failures -= 1
            raise sqlite3.OperationalError("database is locked")
        state = RescueState.RESCUED if verdict.outcome is RescueOutcome.PDF else RescueState.PENDING
        if self.applied:
            self.states[entry_id] = state
        updated = SimpleNamespace(id=entry_id, state=state, request=self.entry.request)
        return RecordedVerdict(updated, applied=self.applied)  # type: ignore[arg-type]


def _service(tmp_path: Path, storage: _Storage, now: list[float]) -> BrowserRescueService:
    config = SimpleNamespace(discovery=SimpleNamespace(browser_rescue_hidden=True))
    return BrowserRescueService(
        storage, config, tmp_path, clock=lambda: now[0]  # type: ignore[arg-type]
    )


def _entry() -> Any:
    return SimpleNamespace(
        id=1,
        request=SimpleNamespace(host="example.org", address="https://example.org/a.pdf", doi="10.1/a"),
    )


@pytest.fixture
def no_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``shutdown``'s flush of deferred deletes.

    In the suite's shared QApplication it would deliver earlier tests'
    backlog (#516); the harness checks the real quit in its own process.
    """
    from bmlibrarian_lite.gui import browser_rescue_service

    monkeypatch.setattr(
        browser_rescue_service,
        "QCoreApplication",
        SimpleNamespace(sendPostedEvents=lambda *args: None),
    )


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


def test_quit_tries_once_more_to_save_and_announces_nothing(
    qapp, tmp_path: Path, no_flush: None
) -> None:
    """A verdict still unsaved at quit gets one last attempt, and starts no analysis.

    A rescue announced at quit would re-analyse its document while the app
    exits; the next session's analysis finds the PDF instead.
    """
    entry = _entry()
    storage = _Storage(entry, failures=1)
    now = [1_000.0]
    service = _service(tmp_path, storage, now)
    rescued: list[Any] = []
    service.rescued.connect(rescued.append)
    _finish_visit(service, entry)
    service.shutdown()
    assert len(storage.writes) == 2
    assert rescued == []


def test_a_save_that_fails_at_quit_says_the_entry_waits_for_next_session(
    qapp, tmp_path: Path, no_flush: None, caplog: pytest.LogCaptureFixture
) -> None:
    """No retry is promised once the pass has stopped."""
    entry = _entry()
    storage = _Storage(entry, failures=2)
    service = _service(tmp_path, storage, [1_000.0])
    _finish_visit(service, entry)
    caplog.clear()
    service.shutdown()
    assert "visited again next session" in caplog.text
    assert "trying again" not in caplog.text
    assert not service._save_retry.isActive()


def test_a_rescue_another_process_recorded_first_is_not_announced_again(
    qapp, tmp_path: Path
) -> None:
    """Only a verdict that settled the entry announces its rescue."""
    entry = _entry()
    storage = _Storage(entry, failures=0, applied=False)
    service = _service(tmp_path, storage, [1_000.0])
    rescued: list[Any] = []
    changed: list[Any] = []
    service.rescued.connect(rescued.append)
    service.entry_changed.connect(changed.append)
    _finish_visit(service, entry)
    assert rescued == [] and changed == []
    assert not service.busy


def test_an_applied_verdict_says_the_entry_changed(qapp, tmp_path: Path) -> None:
    """Control: a verdict that applied emits entry_changed once, for the window and the tab."""
    entry = _entry()
    service = _service(tmp_path, _Storage(entry, failures=0), [1_000.0])
    changed: list[Any] = []
    service.entry_changed.connect(changed.append)
    _finish_visit(service, entry)
    assert len(changed) == 1 and changed[0].id == entry.id


def test_a_visit_switched_off_while_it_waited_for_its_slot_is_not_made(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The setting is read again when the visit starts, after the pacing wait."""
    from bmlibrarian_lite.gui import browser_rescue_service

    made: list[Any] = []
    monkeypatch.setattr(browser_rescue_service, "RescueAttempt", lambda *a, **k: made.append(a))
    entry = _entry()
    storage = _Storage(entry, failures=0)
    service = _service(tmp_path, storage, [1_000.0])
    service._busy = True
    service._config.discovery.browser_rescue_hidden = False
    service._visit(entry)
    assert made == []
    assert storage.writes == []
    assert not service.busy


def test_a_visit_that_cannot_start_counts_as_a_transport_failure(
    qapp, tmp_path: Path
) -> None:
    """A local failure to start is recorded, so the entry is held back, not retried each poll."""
    entry = _entry()
    storage = _Storage(entry, failures=0)
    service = BrowserRescueService(
        storage, SimpleNamespace(discovery=SimpleNamespace(browser_rescue_hidden=True)),  # type: ignore[arg-type]
        tmp_path, pdf_path_for=_raise(OSError("read-only file system")),
    )
    service._profile = SimpleNamespace()  # type: ignore[assignment]
    service._busy = True
    service._visit(entry)
    assert storage.writes == [(1, RescueVerdict(RescueOutcome.TRANSPORT))]
    assert not service.busy


def test_an_unexpected_error_starting_a_visit_does_not_stop_the_pass(
    qapp, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Whatever stops a visit from starting is logged, and the pass is not left busy (#525)."""
    entry = _entry()
    storage = _Storage(entry, failures=0)
    service = BrowserRescueService(
        storage, SimpleNamespace(discovery=SimpleNamespace(browser_rescue_hidden=True)),  # type: ignore[arg-type]
        tmp_path, pdf_path_for=_raise(RuntimeError("engine gone")),
    )
    service._profile = SimpleNamespace()  # type: ignore[assignment]
    service._busy = True
    service._visit(entry)
    assert not service.busy
    assert service._attempt is None
    assert "engine gone" in caplog.text


def test_an_unexpected_error_looking_for_work_does_not_stop_the_pass(
    qapp, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An error escaping the queue read is logged, and the next poll looks again (#525)."""
    entry = _entry()
    storage = _Storage(entry, failures=0)
    storage.next_rescue_entry = _raise(RuntimeError("no extension support"))  # type: ignore[method-assign]
    service = _service(tmp_path, storage, [1_000.0])
    service._busy = True
    service._tick()
    assert not service.busy
    assert "no extension support" in caplog.text


def test_an_unexpected_error_saving_a_verdict_holds_it(qapp, tmp_path: Path) -> None:
    """Whatever the write raised, the verdict is held and saved again, never the visit."""
    entry = _entry()
    storage = _Storage(entry, failures=0)
    write = storage.record_rescue_verdict
    storage.record_rescue_verdict = _raise(RuntimeError("no extension support"))  # type: ignore[method-assign]
    now = [1_000.0]
    service = _service(tmp_path, storage, now)
    _finish_visit(service, entry)
    assert not service.busy
    assert service._unsaved == (entry, PDF)
    storage.record_rescue_verdict = write  # type: ignore[method-assign]
    now[0] += RESCUE_VERDICT_SAVE_RETRY_SECONDS
    assert service._next_entry() is entry
    assert storage.writes == [(1, PDF)]


def _raise(error: Exception) -> Any:
    """A callable that raises ``error`` whatever it is given."""

    def raiser(*_args: object, **_kwargs: object) -> Any:
        raise error

    return raiser


# -- shown visits (#480, stage 3) -----------------------------------------------


class _FakeAttempt(QObject):
    """Stands in for RescueAttempt: records how it was made and driven."""

    finished = Signal(object)
    download_ignored = Signal()
    made: list["_FakeAttempt"] = []

    def __init__(self, profile: Any, address: str, pdf_path: Path, timeout_ms: int, stall_ms: int,
                 max_pdf_bytes: int, parent: Any = None, *, view: Any = None, judge_pages: bool = True) -> None:
        """Record the arguments."""
        super().__init__()
        self.address, self.timeout_ms, self.view, self.judge_pages = address, timeout_ms, view, judge_pages
        self.started = False
        self.aborted = False
        _FakeAttempt.made.append(self)

    def start(self) -> None:
        """Record the start."""
        self.started = True

    def abort(self) -> None:
        """Record the abort."""
        self.aborted = True


@pytest.fixture
def shown(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, Any]]:
    """Fake attempts and profile; scheduled starts are recorded, not run."""
    _FakeAttempt.made = []
    monkeypatch.setattr(service_module, "RescueAttempt", _FakeAttempt)
    monkeypatch.setattr(BrowserRescueService, "_ensure_profile", lambda self: object())
    scheduled: list[tuple[int, Any]] = []

    class _RecordingTimer(QTimer):
        """A real QTimer for the service's own timers; one-shot starts are recorded."""

        @staticmethod
        def singleShot(delay: int, _context: Any, fn: Any) -> None:  # noqa: N802
            scheduled.append((delay, fn))

    monkeypatch.setattr(service_module, "QTimer", _RecordingTimer)
    return scheduled


def _started_service(tmp_path: Path, storage: Any) -> BrowserRescueService:
    """A service as start() leaves it, without its timers running."""
    service = _service(tmp_path, storage, [0.0])
    service._started = True
    return service


def _run_scheduled(scheduled: list[tuple[int, Any]]) -> None:
    """Run what was scheduled, as the event loop would."""
    while scheduled:
        _delay, fn = scheduled.pop(0)
        fn()


def _other() -> Any:
    return SimpleNamespace(
        id=2, request=SimpleNamespace(host="b.example", address="https://b.example/2.pdf", doi="10.1/b")
    )


def test_a_person_s_visit_is_made_in_their_view_unjudged(qapp, tmp_path: Path, shown: list) -> None:
    """visit_shown makes one attempt, in the view, with no page judgement (D2)."""
    service = _started_service(tmp_path, _Storage(_entry(), failures=0))
    view = object()
    started: list[Any] = []
    service.shown_visit_started.connect(started.append)
    service.visit_shown(_entry(), view)
    _run_scheduled(shown)
    (attempt,) = _FakeAttempt.made
    assert attempt.started and attempt.view is view and attempt.judge_pages is False
    assert service.shown_entry_id == 1
    assert len(started) == 1


def test_the_hidden_pass_takes_no_entry_while_a_shown_visit_is_asked_for(qapp, tmp_path: Path, shown: list) -> None:
    """While the window drives its view, the hidden pass pauses."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    service.visit_shown(_entry(), object())
    service._tick()
    assert storage.reads == 0


def test_a_shown_visit_waits_for_the_hidden_visit_under_way(qapp, tmp_path: Path, shown: list) -> None:
    """One visit at a time: the shown one starts when the hidden one ends (D1)."""
    service = _started_service(tmp_path, _Storage(_entry(), failures=0))
    service._attempt = _FakeAttempt(None, "h", tmp_path, 0, 0, 0)  # type: ignore[assignment]
    _FakeAttempt.made = []
    service.visit_shown(_other(), object())
    _run_scheduled(shown)
    assert _FakeAttempt.made == []
    service._busy = True
    service._on_finished(_entry(), PDF)
    _run_scheduled(shown)
    assert len(_FakeAttempt.made) == 1 and _FakeAttempt.made[0].started


def test_a_shown_visit_for_an_entry_the_hidden_visit_rescued_is_not_made(
    qapp, tmp_path: Path, shown: list
) -> None:
    """Asked for behind the hidden visit of the same entry, which then rescued it: no page asks for a tick."""
    service = _started_service(tmp_path, _Storage(_entry(), failures=0))
    service._config.discovery.browser_rescue_hidden = False  # type: ignore[attr-defined]
    service._attempt = _FakeAttempt(None, "h", tmp_path, 0, 0, 0)  # type: ignore[assignment]
    _FakeAttempt.made = []
    ended: list[tuple[Any, Any]] = []
    service.shown_visit_ended.connect(lambda e, v: ended.append((e, v)))
    service.visit_shown(_entry(), object())
    service._busy = True
    service._on_finished(_entry(), PDF)
    _run_scheduled(shown)
    assert _FakeAttempt.made == []
    assert service.shown_entry_id is None
    (settled, verdict), = ended
    assert verdict is None and settled.state is RescueState.RESCUED


def test_a_shown_visit_whose_entry_cannot_be_read_is_made_as_last_read(
    qapp, tmp_path: Path, shown: list, caplog: pytest.LogCaptureFixture
) -> None:
    """Unreadable is not settled: the reader asked for it, and a verdict never overwrites a settled entry."""
    storage = _Storage(_entry(), failures=0)
    storage.rescue_entry = _raise(sqlite3.OperationalError("database is locked"))  # type: ignore[method-assign]
    service = _started_service(tmp_path, storage)
    as_last_read = SimpleNamespace(id=1, state=RescueState.NEEDS_PERSON, request=_entry().request)
    service.visit_shown(as_last_read, object())
    _run_scheduled(shown)
    assert len(_FakeAttempt.made) == 1 and _FakeAttempt.made[0].started
    assert "could not be read before its page opened" in caplog.text


def test_a_hidden_visit_to_an_entry_settled_while_it_waited_is_not_made(
    qapp, tmp_path: Path, shown: list
) -> None:
    """Dismissed, or superseded, during its pacing wait: its visit could replace a PDF already rescued."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    storage.states[1] = RescueState.DISMISSED
    service._busy = True
    service._visit(_entry())
    assert _FakeAttempt.made == [] and not service.busy


def test_a_hidden_visit_reads_its_entry_again_and_visits_it_still_pending(
    qapp, tmp_path: Path, shown: list
) -> None:
    """Control: an entry still pending after its wait is visited."""
    service = _started_service(tmp_path, _Storage(_entry(), failures=0))
    service._busy = True
    service._visit(_entry())
    assert len(_FakeAttempt.made) == 1 and _FakeAttempt.made[0].started


def test_a_shown_visit_s_verdict_is_recorded_announced_and_reported(qapp, tmp_path: Path, shown: list) -> None:
    """A person's PDF is saved as the hidden pass's is: recorded, rescued, entry_changed."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    rescued: list[Any] = []
    changed: list[Any] = []
    ended: list[tuple[Any, Any]] = []
    service.rescued.connect(rescued.append)
    service.entry_changed.connect(changed.append)
    service.shown_visit_ended.connect(lambda e, v: ended.append((e, v)))
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    _FakeAttempt.made[0].finished.emit(PDF)
    assert storage.writes == [(1, PDF)]
    assert len(rescued) == 1 and len(changed) == 1
    assert ended[0][1] == PDF
    assert service.shown_entry_id is None


def test_leaving_a_shown_visit_records_nothing(qapp, tmp_path: Path, shown: list) -> None:
    """The entry stays as it was; no attempt is counted (D2)."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    ended: list[tuple[Any, Any]] = []
    service.shown_visit_ended.connect(lambda e, v: ended.append((e, v)))
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    service.end_shown_visit()
    assert _FakeAttempt.made[0].aborted
    assert storage.writes == []
    assert ended[0][1] is None


def test_choosing_another_entry_leaves_the_first_without_a_verdict(qapp, tmp_path: Path, shown: list) -> None:
    """One visit at a time; the one left is aborted, not judged."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    service.visit_shown(_other(), object())
    _run_scheduled(shown)
    first, second = _FakeAttempt.made
    assert first.aborted and second.started and service.shown_entry_id == 2
    assert storage.writes == []


def test_a_start_scheduled_for_an_entry_the_reader_left_does_nothing(qapp, tmp_path: Path, shown: list) -> None:
    """A quick click-through starts only the last entry's visit."""
    service = _started_service(tmp_path, _Storage(_entry(), failures=0))
    service.visit_shown(_entry(), object())
    service.visit_shown(_other(), object())
    _run_scheduled(shown)
    assert [a.address for a in _FakeAttempt.made] == ["https://b.example/2.pdf"]


def test_a_shown_visit_that_cannot_start_ends_without_a_verdict(
    qapp, tmp_path: Path, shown: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No profile folder: logged, reported as failed and ended, nothing recorded for a person's visit."""
    def no_profile(self: Any) -> Any:
        raise OSError("read-only")

    monkeypatch.setattr(BrowserRescueService, "_ensure_profile", no_profile)
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    # Off, so the hidden pass, woken once the shown visit ends, does not visit
    # the same entry (its own failure to start is a stage 2 test)
    service._config.discovery.browser_rescue_hidden = False  # type: ignore[attr-defined]
    ended: list[tuple[Any, Any]] = []
    failed: list[Any] = []
    service.shown_visit_ended.connect(lambda e, v: ended.append((e, v)))
    service.shown_visit_failed.connect(failed.append)
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    assert ended and ended[0][1] is None and len(failed) == 1
    assert storage.writes == [] and service.shown_entry_id is None


def test_a_shown_visit_waits_while_a_verdict_is_unsaved(qapp, tmp_path: Path, shown: list) -> None:
    """The next write would most likely fail too: no visit starts until the save succeeds."""
    service = _started_service(tmp_path, _Storage(_entry(), failures=0))
    service._unsaved = (_entry(), PDF)
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    assert _FakeAttempt.made == []


def test_shutdown_aborts_a_shown_visit(qapp, tmp_path: Path, shown: list, no_flush: None) -> None:
    """Quit: the shown visit ends with no verdict, its entry as it was."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    service.shutdown()
    assert _FakeAttempt.made[0].aborted and storage.writes == []


# -- walk the queue (#480, stage 3) ------------------------------------------------


class _WalkStorage(_Storage):
    """A queue whose entries are read by ID; ``states`` decides which are still open."""

    def __init__(self, states: dict[int, RescueState]) -> None:
        """Hold each entry's state."""
        super().__init__(None, failures=0)
        self.states = states

    def rescue_entry(self, entry_id: int) -> Any:
        """The entry as it now stands, or None when gone."""
        state = self.states.get(entry_id)
        if state is None:
            return None
        address = f"https://h{entry_id}.example/{entry_id}.pdf"
        return SimpleNamespace(id=entry_id, state=state, request=SimpleNamespace(
            host=f"h{entry_id}.example", address=address, doi=f"10.1/{entry_id}"))

    def record_rescue_verdict(self, entry_id: int, verdict: RescueVerdict) -> RecordedVerdict:
        """Apply a verdict: a PDF rescues, anything else waits for a person."""
        self.writes.append((entry_id, verdict))
        state = RescueState.RESCUED if verdict.outcome is RescueOutcome.PDF else RescueState.NEEDS_PERSON
        self.states[entry_id] = state
        return RecordedVerdict(self.rescue_entry(entry_id), applied=True)


CHALLENGE = RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare",))


def _walking_service(tmp_path: Path, storage: _WalkStorage) -> BrowserRescueService:
    """A started service whose hidden pass is off, so only the walk visits."""
    service = _started_service(tmp_path, storage)
    service._config.discovery.browser_rescue_hidden = False  # type: ignore[attr-defined]
    return service


def test_a_walk_visits_each_entry_in_turn_judging_its_page(qapp, tmp_path: Path, shown: list) -> None:
    """Each entry in the order given, with the visible timeout and page judgement."""
    storage = _WalkStorage({1: RescueState.NEEDS_PERSON, 2: RescueState.NEEDS_PERSON})
    service = _walking_service(tmp_path, storage)
    service.walk([1, 2], object())
    _run_scheduled(shown)
    first = _FakeAttempt.made[0]
    assert first.judge_pages is True and first.timeout_ms == service._visible_timeout_ms
    first.finished.emit(CHALLENGE)
    _run_scheduled(shown)
    assert [a.address for a in _FakeAttempt.made] == [
        "https://h1.example/1.pdf", "https://h2.example/2.pdf"
    ]
    assert service.walking


def test_a_walk_skips_an_entry_settled_meanwhile(qapp, tmp_path: Path, shown: list) -> None:
    """Re-read before each visit; one no longer open, or gone, is skipped."""
    storage = _WalkStorage({1: RescueState.DISMISSED, 3: RescueState.NEEDS_PERSON})
    service = _walking_service(tmp_path, storage)
    service.walk([1, 2, 3], object())
    _run_scheduled(shown)
    assert [a.address for a in _FakeAttempt.made] == ["https://h3.example/3.pdf"]


def test_a_walk_ends_by_itself_after_the_last_entry(qapp, tmp_path: Path, shown: list) -> None:
    """walk_ended is emitted once the list is done."""
    storage = _WalkStorage({1: RescueState.NEEDS_PERSON})
    service = _walking_service(tmp_path, storage)
    ended: list[WalkEnd] = []
    service.walk_ended.connect(ended.append)
    service.walk([1], object())
    _run_scheduled(shown)
    _FakeAttempt.made[0].finished.emit(RescueVerdict(RescueOutcome.NO_PDF, 200))
    assert ended == [WalkEnd(WalkEndReason.FINISHED)] and not service.walking


def test_stopping_a_walk_aborts_its_visit(qapp, tmp_path: Path, shown: list) -> None:
    """Stop: the visit under way ends with no verdict, and no further entry is visited."""
    storage = _WalkStorage({1: RescueState.NEEDS_PERSON, 2: RescueState.NEEDS_PERSON})
    service = _walking_service(tmp_path, storage)
    ended: list[WalkEnd] = []
    service.walk_ended.connect(ended.append)
    service.walk([1, 2], object())
    _run_scheduled(shown)
    service.stop_walk()
    _run_scheduled(shown)
    assert _FakeAttempt.made[0].aborted and len(_FakeAttempt.made) == 1
    assert storage.writes == [] and ended == [WalkEnd(WalkEndReason.STOPPED)]


def test_choosing_an_entry_stops_the_walk(qapp, tmp_path: Path, shown: list) -> None:
    """The reader picking an entry takes the view over: the walk stops."""
    storage = _WalkStorage({1: RescueState.NEEDS_PERSON, 2: RescueState.NEEDS_PERSON})
    service = _walking_service(tmp_path, storage)
    service.walk([1, 2], object())
    _run_scheduled(shown)
    service.visit_shown(storage.rescue_entry(2), object())
    _run_scheduled(shown)
    assert not service.walking
    assert _FakeAttempt.made[-1].judge_pages is False


def test_a_queue_that_cannot_be_read_stops_the_walk_and_says_so(
    qapp, tmp_path: Path, shown: list, caplog: pytest.LogCaptureFixture
) -> None:
    """Unreadable is not settled: the walk stops, logged, and its end says why."""
    storage = _WalkStorage({2: RescueState.NEEDS_PERSON})
    original = storage.rescue_entry

    def flaky(entry_id: int) -> Any:
        if entry_id == 1:
            raise sqlite3.OperationalError("database is locked")
        return original(entry_id)

    storage.rescue_entry = flaky  # type: ignore[method-assign]
    service = _walking_service(tmp_path, storage)
    ended: list[WalkEnd] = []
    service.walk_ended.connect(ended.append)
    service.walk([1, 2], object())
    _run_scheduled(shown)
    assert _FakeAttempt.made == [] and not service.walking
    assert ended == [WalkEnd(WalkEndReason.UNREADABLE)]
    assert "could not be read" in caplog.text


def test_the_hidden_pass_looks_again_once_a_walk_ends(qapp, tmp_path: Path, shown: list) -> None:
    """The pause is over when the walk is: the hidden pass looks at once, not at the next poll."""
    storage = _WalkStorage({1: RescueState.NEEDS_PERSON})
    service = _started_service(tmp_path, storage)
    service.walk([1], object())
    _run_scheduled(shown)
    reads = storage.reads
    _FakeAttempt.made[0].finished.emit(RescueVerdict(RescueOutcome.NO_PDF, 200))
    _run_scheduled(shown)
    assert not service.walking and storage.reads > reads


# -- the final review's findings (#480, stage 3) ---------------------------------


def test_a_hidden_visit_late_for_its_slot_leaves_the_reader_s_visit_alone(
    qapp, tmp_path: Path, shown: list
) -> None:
    """A hidden visit whose slot arrives after a shown visit started frees only itself."""
    service = _started_service(tmp_path, _Storage(_entry(), failures=0))
    service.wake()
    _delay, tick = shown.pop(0)
    tick()  # takes the entry and schedules its hidden visit for the host's slot
    _delay, late_visit = shown.pop(0)
    service.visit_shown(_other(), object())
    _run_scheduled(shown)
    (reader_attempt,) = _FakeAttempt.made
    late_visit()
    assert not reader_attempt.aborted
    assert service._attempt is reader_attempt and service.shown_entry_id == 2
    ended: list[Any] = []
    service.shown_visit_ended.connect(lambda e, v: ended.append(v))
    reader_attempt.finished.emit(PDF)
    assert ended == [PDF]


def test_ending_the_walk_s_visit_from_outside_ends_the_walk(qapp, tmp_path: Path, shown: list) -> None:
    """Dismissing the walk's entry, or selecting a settled one, stops the walk rather than hanging it."""
    storage = _WalkStorage({1: RescueState.NEEDS_PERSON, 2: RescueState.NEEDS_PERSON})
    service = _walking_service(tmp_path, storage)
    ended: list[WalkEnd] = []
    service.walk_ended.connect(ended.append)
    service.walk([1, 2], object())
    _run_scheduled(shown)
    service.end_shown_visit()
    _run_scheduled(shown)
    assert not service.walking and ended == [WalkEnd(WalkEndReason.STOPPED)]
    assert _FakeAttempt.made[0].aborted and len(_FakeAttempt.made) == 1


# -- the PR review's findings (#480, stage 3) --------------------------------------


def test_a_walk_goes_on_past_a_page_that_cannot_open_and_counts_it(
    qapp, tmp_path: Path, shown: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The next entry is visited; the walk's end says how many pages failed."""
    storage = _WalkStorage({1: RescueState.NEEDS_PERSON, 2: RescueState.NEEDS_PERSON})
    service = _walking_service(tmp_path, storage)
    made = 0

    def first_fails(*args: Any, **kwargs: Any) -> Any:
        nonlocal made
        made += 1
        if made == 1:
            raise RuntimeError("engine gone")
        return _FakeAttempt(*args, **kwargs)

    monkeypatch.setattr(service_module, "RescueAttempt", first_fails)
    failed: list[Any] = []
    ended: list[WalkEnd] = []
    service.shown_visit_failed.connect(failed.append)
    service.walk_ended.connect(ended.append)
    service.walk([1, 2], object())
    _run_scheduled(shown)
    assert [e.id for e in failed] == [1]
    (second,) = _FakeAttempt.made
    assert second.started and second.address == "https://h2.example/2.pdf"
    second.finished.emit(CHALLENGE)
    assert ended == [WalkEnd(WalkEndReason.FINISHED, failed=1)]


def test_a_shown_visit_held_by_an_unsaved_verdict_starts_once_it_is_saved(
    qapp, tmp_path: Path, shown: list
) -> None:
    """The hold ends with the save: the reader's page is not left "opening" for good."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    service._config.discovery.browser_rescue_hidden = False  # type: ignore[attr-defined]
    service._unsaved = (_entry(), PDF)
    service.visit_shown(_other(), object())
    _run_scheduled(shown)
    assert _FakeAttempt.made == []
    service._tick()
    _run_scheduled(shown)
    assert storage.writes == [(1, PDF)] and not service.holds_unsaved_verdict
    assert len(_FakeAttempt.made) == 1 and _FakeAttempt.made[0].started


def test_a_shown_verdict_that_cannot_be_saved_is_reported_unsaved(
    qapp, tmp_path: Path, shown: list
) -> None:
    """The visit still ends with its verdict, and the window can tell it was not saved."""
    storage = _Storage(_entry(), failures=1)
    service = _started_service(tmp_path, storage)
    service._config.discovery.browser_rescue_hidden = False  # type: ignore[attr-defined]
    seen: list[tuple[Any, bool]] = []
    service.shown_visit_ended.connect(lambda _e, v: seen.append((v, service.holds_unsaved_verdict)))
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    _FakeAttempt.made[0].finished.emit(PDF)
    assert seen == [(PDF, True)]


def test_a_walk_waits_for_an_unsaved_verdict_then_goes_on(qapp, tmp_path: Path, shown: list) -> None:
    """No page opens while a verdict waits to be saved; the walk resumes with the save."""
    storage = _WalkStorage({1: RescueState.NEEDS_PERSON, 2: RescueState.NEEDS_PERSON})
    write = storage.record_rescue_verdict
    storage.record_rescue_verdict = _raise(sqlite3.OperationalError("locked"))  # type: ignore[method-assign]
    service = _walking_service(tmp_path, storage)
    service.walk([1, 2], object())
    _run_scheduled(shown)
    _FakeAttempt.made[0].finished.emit(CHALLENGE)
    _run_scheduled(shown)
    assert len(_FakeAttempt.made) == 1 and service.walking
    storage.record_rescue_verdict = write  # type: ignore[method-assign]
    service._save_retry_at = 0.0
    service._tick()
    _run_scheduled(shown)
    assert [a.address for a in _FakeAttempt.made][-1] == "https://h2.example/2.pdf"


def test_a_shown_visit_without_a_pacing_slot_is_not_made_unpaced(
    qapp, tmp_path: Path, shown: list, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A limiter that fails is a page that cannot open, never a visit at once."""
    monkeypatch.setattr(service_module, "rescue_delay_ms", _raise(RuntimeError("limiter broken")))
    service = _started_service(tmp_path, _Storage(_entry(), failures=0))
    service._config.discovery.browser_rescue_hidden = False  # type: ignore[attr-defined]
    failed: list[Any] = []
    service.shown_visit_failed.connect(failed.append)
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    assert _FakeAttempt.made == [] and len(failed) == 1
    assert service.shown_entry_id is None
    assert "limiter broken" in caplog.text


def test_a_person_s_download_that_was_no_pdf_is_said_for_their_visit_only(
    qapp, tmp_path: Path, shown: list
) -> None:
    """Relayed while the visit is the window's; one the reader left says nothing."""
    service = _started_service(tmp_path, _Storage(_entry(), failures=0))
    ignored: list[Any] = []
    service.shown_download_ignored.connect(ignored.append)
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    first = _FakeAttempt.made[0]
    first.download_ignored.emit()
    assert [e.id for e in ignored] == [1]
    service.visit_shown(_other(), object())
    _run_scheduled(shown)
    first.download_ignored.emit()
    assert [e.id for e in ignored] == [1]


def test_the_hidden_pass_looks_again_once_a_person_s_visit_is_left(qapp, tmp_path: Path, shown: list) -> None:
    """Leaving the window's visit ends the pause at once, not at the next poll."""
    storage = _Storage(_entry(), failures=0)
    service = _started_service(tmp_path, storage)
    service.visit_shown(_entry(), object())
    _run_scheduled(shown)
    service.end_shown_visit()
    _run_scheduled(shown)
    assert storage.reads >= 1
