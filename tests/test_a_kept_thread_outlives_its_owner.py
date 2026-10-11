"""A thread its owner lets go of while it runs is kept until it finishes.

Dropping the last reference to a running ``QThread`` aborts the process
("QThread: Destroyed while thread is still running", exit 134), and so does
one still running when the application exits. The keeper in
``bmlibrarian_lite.gui.thread_keeper`` holds such a thread until its
``finished`` arrives, and waits a bounded time for the rest at quit (#514).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Generator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

# Each test runs QThreads and pumps the event loop (see the fixture in conftest).
pytestmark = pytest.mark.usefixtures("gc_on_the_gui_thread_only")

# How long a test lets the event loop run before it gives up on a thread.
_SETTLE_SECONDS = 5.0


@pytest.fixture
def qapp() -> QApplication:
    """A QApplication for the keeper tests."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def gate() -> Generator[threading.Event, None, None]:
    """An event a gated thread waits on; always set on teardown."""
    event = threading.Event()
    yield event
    event.set()


@pytest.fixture(autouse=True)
def _the_keeper_starts_empty() -> None:
    """A thread an earlier test left kept is named here, not by a count below."""
    from bmlibrarian_lite.gui import thread_keeper

    assert thread_keeper.kept_thread_count() == 0, "an earlier test left a thread kept"


class _StuckThread:
    """Stands in for a kept thread that never ends; records each wait."""

    def __init__(self, waits: list[int]) -> None:
        """Record waits into ``waits``."""
        self._waits = waits

    def wait(self, ms: int) -> bool:
        """Record the wait, as PySide6 does refusing a negative one."""
        if ms < 0:
            raise OverflowError(ms)
        self._waits.append(ms)
        return False

    def isRunning(self) -> bool:
        """Never ends."""
        return True


def _gated_thread(gate: threading.Event) -> QThread:
    """A thread that runs until the gate opens."""
    from PySide6.QtCore import QThread

    class GatedThread(QThread):
        """Runs until its gate is set."""

        def run(self) -> None:
            """Wait for the gate."""
            gate.wait()

    return GatedThread()


def _settle(qapp: QApplication) -> None:
    """Run the event loop until the keeper holds nothing, or time runs out."""
    from bmlibrarian_lite.gui import thread_keeper

    deadline = time.monotonic() + _SETTLE_SECONDS
    while thread_keeper.kept_thread_count() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def test_a_running_thread_is_kept_until_it_finishes(
    qapp: QApplication, gate: threading.Event
) -> None:
    """The caller's reference can go; the keeper holds the thread, then lets go."""
    from bmlibrarian_lite.gui import thread_keeper

    thread = _gated_thread(gate)
    thread.start()
    thread_keeper.keep_until_finished(thread)
    del thread  # without the keeper this aborts the interpreter

    assert thread_keeper.kept_thread_count() == 1
    gate.set()
    _settle(qapp)
    assert thread_keeper.kept_thread_count() == 0


def test_a_thread_that_never_started_is_not_kept(qapp: QApplication) -> None:
    """Nothing runs, so nothing needs keeping."""
    from bmlibrarian_lite.gui import thread_keeper

    thread = _gated_thread(threading.Event())
    thread_keeper.keep_until_finished(thread)
    assert thread_keeper.kept_thread_count() == 0


def test_a_thread_that_already_finished_is_not_kept(
    qapp: QApplication, gate: threading.Event
) -> None:
    """A thread whose ``finished`` came before the keeper saw it is let go at once."""
    from bmlibrarian_lite.gui import thread_keeper

    thread = _gated_thread(gate)
    thread.start()
    gate.set()
    assert thread.wait(int(_SETTLE_SECONDS * 1000))
    thread_keeper.keep_until_finished(thread)
    assert thread_keeper.kept_thread_count() == 0


def test_the_quit_wait_reports_a_thread_still_running(
    qapp: QApplication, gate: threading.Event, caplog: pytest.LogCaptureFixture
) -> None:
    """A bounded wait that a thread outlasts says so, and keeps the thread."""
    from bmlibrarian_lite.gui import thread_keeper

    thread = _gated_thread(gate)
    thread.start()
    thread_keeper.keep_until_finished(thread)

    with caplog.at_level("WARNING", logger=thread_keeper.__name__):
        still_running = thread_keeper.wait_for_kept_threads(timeout_ms=50)
    assert still_running == 1
    assert [r.levelname for r in caplog.records] == ["ERROR"]
    assert "will abort" in caplog.text
    assert thread_keeper.kept_thread_count() == 1, "a running thread is never let go"

    gate.set()
    _settle(qapp)


def test_the_quit_wait_returns_once_every_thread_has_finished(
    qapp: QApplication, gate: threading.Event, caplog: pytest.LogCaptureFixture
) -> None:
    """A thread that finishes within the wait leaves nothing running, and no warning."""
    from bmlibrarian_lite.gui import thread_keeper

    thread = _gated_thread(gate)
    thread.start()
    thread_keeper.keep_until_finished(thread)

    opener = threading.Timer(0.05, gate.set)
    opener.start()
    with caplog.at_level("WARNING", logger=thread_keeper.__name__):
        still_running = thread_keeper.wait_for_kept_threads(
            timeout_ms=int(_SETTLE_SECONDS * 1000)
        )
    opener.join()
    assert still_running == 0
    assert "still running" not in caplog.text
    _settle(qapp)
    assert thread_keeper.kept_thread_count() == 0


def test_the_application_waits_for_kept_threads_as_it_quits(
    qapp: QApplication, gate: threading.Event
) -> None:
    """Keeping a thread hooks the quit wait: ``aboutToQuit`` lets the thread end."""
    from bmlibrarian_lite.gui import thread_keeper

    thread = _gated_thread(gate)
    thread.start()
    thread_keeper.keep_until_finished(thread)

    opener = threading.Timer(0.05, gate.set)
    opener.start()
    # Emitted on the suite's shared application: every slot connected to it
    # runs here, and only the keeper's is.
    qapp.aboutToQuit.emit()
    finished_when_quit_returned = thread.isFinished()
    opener.join()
    assert finished_when_quit_returned, "the quit hook waited for the kept thread"
    _settle(qapp)


def test_a_second_release_of_a_deleted_thread_does_nothing(
    qapp: QApplication, gate: threading.Event
) -> None:
    """A release after the thread was let go and deleted does nothing.

    A thread caught while finishing is released twice: at once, because it no
    longer runs, and when its queued ``finished`` arrives. On PySide6 6.10
    deleting the thread drops that ``finished``, but the release must not
    depend on it.
    """
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent

    from bmlibrarian_lite.gui import thread_keeper

    thread = _gated_thread(gate)
    thread.start()
    gate.set()
    assert thread.wait(int(_SETTLE_SECONDS * 1000))
    thread_keeper.keep_until_finished(thread)
    # Only this thread's delete: a global flush can reach other tests' leaked widgets (#516).
    QCoreApplication.sendPostedEvents(thread, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(thread), "the keeper deleted the thread it let go"

    thread_keeper._release(thread)  # the second release; must not raise
    assert thread_keeper.kept_thread_count() == 0


def test_the_quit_wait_shares_one_deadline_and_never_waits_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each thread waits what is left of one deadline, and none past it.

    Three stuck threads, a 1 s wait: the second is waited on 0.6 s in, the
    third 1.5 s in, after the deadline. PySide6 refuses a negative wait with
    ``OverflowError``, which would end the quit wait early.
    """
    from bmlibrarian_lite.gui import thread_keeper

    clock = iter([0.0, 0.0, 0.6, 1.5])
    monkeypatch.setattr(thread_keeper.time, "monotonic", lambda: next(clock))
    waits: list[int] = []
    monkeypatch.setattr(thread_keeper, "_kept", {_StuckThread(waits) for _ in range(3)})

    assert thread_keeper.wait_for_kept_threads(timeout_ms=1000) == 3
    assert sorted(waits, reverse=True) == [1000, 400, 0]


def test_the_quit_wait_is_hooked_once(qapp: QApplication) -> None:
    """Every keep after the first adds no second wait at quit."""
    from PySide6.QtCore import SIGNAL, QThread

    from bmlibrarian_lite.gui import thread_keeper

    thread_keeper.keep_until_finished(QThread())
    hooked = qapp.receivers(SIGNAL("aboutToQuit()"))
    thread_keeper.keep_until_finished(QThread())
    assert qapp.receivers(SIGNAL("aboutToQuit()")) == hooked


def test_a_kept_thread_is_released_on_the_gui_thread(
    qapp: QApplication, gate: threading.Event, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The release waits for and deletes the thread from the GUI thread, never its own."""
    from PySide6.QtCore import QThread

    from bmlibrarian_lite.gui import thread_keeper

    released_on: list[QThread] = []
    release = thread_keeper._release

    def recording_release(thread: QThread) -> None:
        """Note which thread releases, then release."""
        released_on.append(QThread.currentThread())
        release(thread)

    monkeypatch.setattr(thread_keeper, "_release", recording_release)
    thread = _gated_thread(gate)
    thread.start()
    thread_keeper.keep_until_finished(thread)
    del thread
    gate.set()
    _settle(qapp)

    assert released_on, "the kept thread was released"
    assert all(t is qapp.thread() for t in released_on)
