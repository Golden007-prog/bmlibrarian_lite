"""The review queue window's embedded view (#480, stage 3).

The only runtime import of QtWebEngineWidgets (others import it for type
checking only). ``show_review_queue`` imports this module lazily, and only
when the rescue service exists, so importing the app or the window's module
never loads it.
"""

from __future__ import annotations

from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget


def make_rescue_view(parent: QWidget) -> QWebEngineView:
    """A view for the window; the service puts each visit's page into it.

    Args:
        parent: The window.

    Returns:
        The view, showing nothing until a visit starts.
    """
    view = QWebEngineView(parent)
    view.setObjectName("rescueView")
    return view
