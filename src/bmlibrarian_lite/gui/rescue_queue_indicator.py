"""The status bar's count of walled PDFs waiting for the reader (#480, stage 3)."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton, QWidget

from ..browser_rescue import awaiting_reader_states
from ..browser_rescue_text import REVIEW_QUEUE_UNREADABLE
from ..constants import RESCUE_POLL_INTERVAL_MS
from ..exceptions import SQLiteError

if TYPE_CHECKING:
    from ..storage import LiteStorage

logger = logging.getLogger(__name__)

TOOLTIP = "Open the review queue: PDFs behind a robot check that wait for you"
_UNKNOWN = "?"


def indicator_text(count: int | None) -> str:
    """The indicator's text.

    Args:
        count: The count; ``None`` when it could not be read.

    Returns:
        "Walled PDFs: N", or "Walled PDFs: ?" for an unread count.
    """
    return f"Walled PDFs: {_UNKNOWN if count is None else count}"


class RescueQueueIndicator(QPushButton):
    """The "Walled PDFs: N" button: the entries waiting for the reader; hidden at 0 (D4).

    Read on :meth:`refresh`, which the main window calls on every change it
    hears of, and every ``poll_ms`` for entries other processes wrote.
    """

    def __init__(
        self,
        storage: LiteStorage,
        hidden_pass_runs: Callable[[], bool],
        poll_ms: int = RESCUE_POLL_INTERVAL_MS,
        parent: QWidget | None = None,
    ) -> None:
        """Make the indicator, hidden until :meth:`refresh` finds entries.

        Args:
            storage: The queue.
            hidden_pass_runs: Whether a hidden pass will visit pending entries.
            poll_ms: How often the count is read again.
            parent: The status bar's window.
        """
        super().__init__(parent)
        self.setObjectName("rescueQueueIndicator")
        self.setFlat(True)
        self.setToolTip(TOOLTIP)
        self.hide()
        self._storage = storage
        self._hidden_pass_runs = hidden_pass_runs
        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self.refresh)

    def start(self) -> None:
        """Read the count now, then every poll interval."""
        self._timer.start()
        self.refresh()

    def refresh(self, *_args: object) -> int | None:
        """Read the count again.

        Connected to signals with arguments (an entry), which are ignored.

        Args:
            *_args: Ignored.

        Returns:
            The count; ``None`` when the queue could not be read (logged, and
            shown as "?").
        """
        try:
            count = self._storage.count_rescue_entries(awaiting_reader_states(self._hidden_pass_runs()))
        except (sqlite3.Error, SQLiteError) as e:
            logger.error("The browser rescue queue could not be counted: %s", e)
            self.setText(indicator_text(None))
            self.setToolTip(REVIEW_QUEUE_UNREADABLE)
            self.show()
            return None
        self.setText(indicator_text(count))
        self.setToolTip(TOOLTIP)
        self.setVisible(count > 0)
        return count
