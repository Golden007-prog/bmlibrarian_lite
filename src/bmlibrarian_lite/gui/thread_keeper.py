"""Keep a thread alive after its owner lets go of it, until it finishes.

Dropping the last reference to a running, parentless ``QThread`` destroys it,
and Qt aborts the process ("QThread: Destroyed while thread is still running").
The application aborts the same way when a thread is still running as it
exits. An owner that must not wait for its thread (a dialog the reader has
closed, waiting on a provider that may take seconds to answer) hands the
thread to this keeper instead. The keeper holds it until ``finished`` arrives,
then waits for the thread to end and deletes it. At quit it waits a bounded
time for whatever it still holds.

Every function here runs on the GUI thread. The lock guards the set in case
a caller off it appears; ``_quit_hooked`` is touched on the GUI thread only.
"""

from __future__ import annotations

import logging
import threading
import time

from PySide6.QtCore import QCoreApplication, Qt, QThread

from bmlibrarian_lite.constants import KEPT_THREAD_EXIT_WAIT_MS

logger = logging.getLogger(__name__)

_kept: set[QThread] = set()
_lock = threading.Lock()
_quit_hooked = False


def keep_until_finished(thread: QThread) -> None:
    """Hold ``thread`` until it finishes, so its owner can drop it.

    A thread that is not running (never started, finishing, or done) is let
    go at once, after waiting for it to end.

    Args:
        thread: The thread its owner is letting go of.
    """
    _hook_the_quit_wait()
    with _lock:
        _kept.add(thread)
    thread.finished.connect(
        lambda: _release(thread), Qt.ConnectionType.QueuedConnection
    )
    # Checked after connecting: a thread that finished before the connection
    # never delivers its ``finished`` to us.
    if not thread.isRunning():
        _release(thread)


def kept_thread_count() -> int:
    """How many threads the keeper holds.

    Returns:
        The number of threads kept and not yet released.
    """
    with _lock:
        return len(_kept)


def wait_for_kept_threads(timeout_ms: int = KEPT_THREAD_EXIT_WAIT_MS) -> int:
    """Wait for every kept thread to finish, all within one deadline.

    Nothing is let go here: a thread that finished is released when its
    ``finished`` arrives. One still running at the deadline is logged as an
    error, because the application aborts if it exits with that thread running.

    Args:
        timeout_ms: How long to wait for all of them together, in milliseconds.

    Returns:
        How many kept threads are still running.
    """
    with _lock:
        threads = list(_kept)
    deadline = time.monotonic() + timeout_ms / 1000
    for thread in threads:
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        thread.wait(remaining_ms)
    running = [thread for thread in threads if thread.isRunning()]
    if running:
        logger.error(
            "%d background thread(s) still running after %d ms; the application"
            " will abort as it exits: %s",
            len(running),
            timeout_ms,
            ", ".join(type(thread).__name__ for thread in running),
        )
    return len(running)


def _release(thread: QThread) -> None:
    """Let go of a kept thread once it has finished.

    Releasing twice is harmless: the ``finished`` handler and the
    not-running check in ``keep_until_finished`` can both arrive.

    Args:
        thread: A thread whose ``finished`` has arrived, or that is not running.
    """
    with _lock:
        if thread not in _kept:
            return
        _kept.discard(thread)
    # ``finished`` is emitted, and isFinished() is already true, before the
    # thread has ended. Wait for the end, a moment away, so the delete cannot
    # catch it still running.
    thread.wait()
    thread.deleteLater()


def _hook_the_quit_wait() -> None:
    """Wait for the kept threads when the application quits (once).

    With no application yet, nothing is hooked; the next keep tries again.
    """
    global _quit_hooked
    if _quit_hooked:
        return
    app = QCoreApplication.instance()
    if app is None:
        return
    app.aboutToQuit.connect(wait_for_kept_threads)
    _quit_hooked = True
