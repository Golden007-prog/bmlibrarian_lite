"""Fail loudly when the GUI stack, QtWebEngine included, cannot start.

The widget suites skip without PySide6, and the engine harness would fail
with a less direct message, so CI checks the stack first: a widget, then a
QtWebEngine page that must load ``about:blank`` (#480, stage 2).
"""

import sys

from bmlibrarian_lite.gui.web_engine import prepare_web_engine

prepare_web_engine()

from PySide6.QtCore import QTimer, QUrl  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402
from shiboken6 import Shiboken  # noqa: E402

#: How long the blank page may take to load on a cold runner.
PREFLIGHT_TIMEOUT_MS = 60_000


def main() -> int:
    """Start the stack and load one page.

    Returns:
        0 when a page loaded; 1 otherwise.
    """
    app = QApplication([])
    QLabel("ci preflight")
    profile = QWebEngineProfile()
    page = QWebEnginePage(profile)
    loaded: list[bool] = []
    page.loadFinished.connect(lambda ok: (loaded.append(ok), app.quit()))
    QTimer.singleShot(PREFLIGHT_TIMEOUT_MS, app.quit)
    page.load(QUrl("about:blank"))
    app.exec()
    Shiboken.delete(page)  # before the profile, as QtWebEngine requires
    Shiboken.delete(profile)
    if loaded != [True]:
        print("QtWebEngine could not load a page", file=sys.stderr)
        return 1
    print("Qt offscreen platform and QtWebEngine are usable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
