"""The suite collects Python's garbage on the main thread only.

Earlier tests leave Qt objects in collectable cycles. Python's collector runs on
whichever thread crosses its allocation threshold, and a test's HTTP server or
executor thread that collects them destroys GUI objects off their thread; the
next ``processEvents`` then fires a stale timer into freed memory (a segfault
only in the full suite, on CI). So automatic collection is off for the whole
session, and a collection that is due runs on the main thread after each test.
"""

from __future__ import annotations

import gc
import threading

import pytest

from tests.main_thread_gc import collect_if_due, due_generation

THRESHOLDS = (700, 10, 10)


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ((0, 0, 0), None),
        ((700, 9, 9), None),
        ((701, 0, 0), 0),
        ((701, 11, 0), 1),
        ((701, 11, 11), 2),
        ((701, 0, 11), 2),
    ],
    ids=["nothing", "at-threshold", "young", "middle", "oldest", "oldest-alone"],
)
def test_the_due_generation_follows_cpythons_rule(
    counts: tuple[int, int, int], expected: int | None
) -> None:
    """Nothing is due until the youngest count passes its threshold; then the oldest due one."""
    assert due_generation(counts, THRESHOLDS) == expected


def test_automatic_collection_is_off_while_a_test_runs() -> None:
    """No thread can trigger a collection during a test."""
    assert not gc.isenabled()


def test_a_due_collection_runs_on_the_calling_thread_and_frees_a_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cycle a test leaves is freed by the main thread's collection, not a worker's."""
    collected_on: list[threading.Thread] = []

    def note_thread(phase: str, info: dict) -> None:
        """Record which thread each collection starts on."""
        if phase == "start":
            collected_on.append(threading.current_thread())

    monkeypatch.setattr(gc, "get_threshold", lambda: (0, 10, 10))
    freed = threading.Event()

    class Cycle:
        """Frees itself only through the cyclic collector."""

        def __init__(self) -> None:
            self.me = self

        def __del__(self) -> None:
            freed.set()

    Cycle()
    # The collector calls the list it holds, so the callback goes on that list.
    gc.callbacks.append(note_thread)
    try:
        collect_if_due()
    finally:
        gc.callbacks.remove(note_thread)

    assert freed.is_set()
    assert collected_on == [threading.main_thread()]
