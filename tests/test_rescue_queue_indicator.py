"""The status bar's "Walled PDFs: N" (#480, stage 3)."""

import sqlite3
from typing import Any

import pytest

from bmlibrarian_lite.browser_rescue import RescueState
from bmlibrarian_lite.gui.rescue_queue_indicator import RescueQueueIndicator, indicator_text


@pytest.fixture
def qapp():
    """The suite's QApplication; never pumped."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Storage:
    def __init__(self, count: int | Exception) -> None:
        """Answer every count with ``count``, or raise it."""
        self.count = count
        self.asked: list[frozenset[RescueState]] = []

    def count_rescue_entries(self, states: Any) -> int:
        """Record the states asked for."""
        self.asked.append(frozenset(states))
        if isinstance(self.count, Exception):
            raise self.count
        return self.count


def test_the_text_names_the_count() -> None:
    """The text names the count, or "?" for one that could not be read."""
    assert indicator_text(3) == "Walled PDFs: 3"
    assert indicator_text(None) == "Walled PDFs: ?"


def test_it_is_hidden_at_zero_and_shown_otherwise(qapp) -> None:
    """Hidden at 0 (spec)."""
    storage = _Storage(0)
    indicator = RescueQueueIndicator(storage, lambda: True)  # type: ignore[arg-type]
    assert indicator.refresh() == 0 and indicator.isHidden()
    storage.count = 2
    assert indicator.refresh() == 2 and not indicator.isHidden()
    assert indicator.text() == "Walled PDFs: 2"


@pytest.mark.parametrize(
    "hidden, states",
    [(True, {RescueState.NEEDS_PERSON}), (False, {RescueState.NEEDS_PERSON, RescueState.PENDING})],
)
def test_it_counts_what_waits_for_the_reader(qapp, hidden: bool, states: set[RescueState]) -> None:
    """D4: pending counts only when no hidden pass will visit it."""
    storage = _Storage(1)
    RescueQueueIndicator(storage, lambda: hidden).refresh()  # type: ignore[arg-type]
    assert storage.asked == [frozenset(states)]


def test_a_queue_that_cannot_be_read_is_shown_not_hidden(qapp) -> None:
    """Golden rule 8: a "?" and a tooltip, not a silently missing indicator."""
    indicator = RescueQueueIndicator(_Storage(sqlite3.OperationalError("locked")), lambda: True)  # type: ignore[arg-type]
    assert indicator.refresh() is None
    assert indicator.text() == "Walled PDFs: ?" and not indicator.isHidden()
    assert "could not be read" in indicator.toolTip()
