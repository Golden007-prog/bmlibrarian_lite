"""Keep a thread alive after its owner lets go of it, until it finishes.

Dropping the last reference to a running ``QThread`` destroys it, and Qt
aborts the process ("QThread: Destroyed while thread is still running").
The application aborts the same way when a thread is still running as it
exits. An owner that must not wait for its thread (a dialog the reader has
closed, waiting on a provider that may take seconds to answer) hands the
thread to this keeper instead. The keeper holds it until ``finished`` arrives,
then waits for the thread to end and deletes it. At quit it waits a bounded
time for whatever it still holds.

Every function here runs on the GUI thread; the lock guards the set anyway.
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

    A thread that is not running (never started, or already done) is not
    kept: nothing waits on it.

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

    A thread still running when the deadline passes stays kept, and is
    logged: the application aborts if it exits with that thread running.

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
        logger.warning(
            "%d background thread(s) still running after %d ms: %s",
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
    # ``finished`` is emitted just before the thread ends; wait for the end,
    # which is immediate, so deleting it cannot catch it still running.
    thread.wait()
    thread.deleteLater()


def _hook_the_quit_wait() -> None:
    """Wait for the kept threads when the application quits (once)."""
    global _quit_hooked
    if _quit_hooked:
        return
    app = QCoreApplication.instance()
    if app is None:
        return
    app.aboutToQuit.connect(wait_for_kept_threads)
    _quit_hooked = True
