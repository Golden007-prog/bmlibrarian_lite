"""The review queue window's embedded view (#480, stage 3).

The only module that imports QtWebEngineWidgets. It is loaded only once the
hidden browser's service started, so the window, and importing the app,
never load QtWebEngine.
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
