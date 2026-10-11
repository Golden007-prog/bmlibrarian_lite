# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2025 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Python's cyclic garbage collection, run on the main thread only.

The suite turns automatic collection off and calls ``collect_if_due`` on the
main thread after each test (``tests/conftest.py``). Automatic collection runs
on whichever thread crosses the allocation threshold, and a worker thread that
collects an earlier test's leaked Qt objects destroys them off their thread:
their timers stay registered, and the next ``processEvents`` fires one into
freed memory. That segfault appears only in the full suite, and moves with
every change to what the suite allocates (#517).
"""

from __future__ import annotations

import gc

# The generations CPython collects, youngest first.
_GENERATIONS = (0, 1, 2)


def due_generation(
    counts: tuple[int, int, int], thresholds: tuple[int, int, int]
) -> int | None:
    """The generation CPython's automatic collection would collect now.

    Nothing is due until the youngest generation's count passes its
    threshold. Then the oldest generation whose count passes its own is
    collected, with every younger one.

    Args:
        counts: ``gc.get_count()``.
        thresholds: ``gc.get_threshold()``.

    Returns:
        The generation to collect, or None when no collection is due.
    """
    if counts[0] <= thresholds[0]:
        return None
    return max(gen for gen in _GENERATIONS if gen == 0 or counts[gen] > thresholds[gen])


def collect_if_due() -> None:
    """Collect on the calling thread, if automatic collection would have."""
    generation = due_generation(gc.get_count(), gc.get_threshold())
    if generation is not None:
        gc.collect(generation)
