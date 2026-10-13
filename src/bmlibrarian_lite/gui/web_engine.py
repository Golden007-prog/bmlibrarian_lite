"""QtWebEngine's start-up requirement, in one place (#480, stage 2).

QtWebEngine must be imported, with OpenGL contexts shared, before the
``QApplication`` exists. Otherwise its first page fails, and Qt only warns.
The application, the test suite's preflight, the engine harness and CI's
preflight all call :func:`prepare_web_engine`.
"""

from PySide6.QtCore import QCoreApplication, Qt


def prepare_web_engine() -> None:
    """Make QtWebEngine usable by the ``QApplication`` about to be created.

    Raises:
        RuntimeError: If a ``QApplication`` already exists: it is too late.
    """
    if QCoreApplication.instance() is not None:
        raise RuntimeError("QtWebEngine must be prepared before the QApplication exists")
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    import PySide6.QtWebEngineCore  # noqa: F401  (registers the engine)
