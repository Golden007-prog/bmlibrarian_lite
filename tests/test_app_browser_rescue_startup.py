"""The app starts when QtWebEngine cannot load (#480, stage 2)."""

from __future__ import annotations

import logging
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from bmlibrarian_lite.browser_rescue import (
    RescueEntry,
    RescueOutcome,
    RescueRequest,
    RescueState,
    RescueVerdict,
)
from bmlibrarian_lite.exceptions import SQLiteError
from bmlibrarian_lite.gui import app as app_module

DOI = "10.1000/xyz"


def test_importing_the_app_does_not_load_qtwebengine() -> None:
    """A fresh interpreter importing gui.app never loads the engine."""
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import sys, bmlibrarian_lite.gui.app; print('PySide6.QtWebEngineCore' in sys.modules)",
        ],
        capture_output=True, text=True, timeout=120, env={**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False"


@pytest.mark.parametrize("error", [ImportError("no engine"), OSError("no lib"), RuntimeError("too late")])
def test_a_web_engine_that_cannot_be_prepared_runs_the_app_without_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    """The failure is logged as a warning, not raised."""
    def fail() -> None:
        raise error

    monkeypatch.setattr(app_module, "prepare_web_engine", fail)
    with caplog.at_level(logging.WARNING, logger=app_module.logger.name):
        assert app_module.prepare_browser_rescue() is False
    assert "hidden browser is unavailable" in caplog.text
    assert type(error).__name__ in caplog.text


def test_a_web_engine_that_prepares_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: no failure, ready."""
    monkeypatch.setattr(app_module, "prepare_web_engine", lambda: None)
    assert app_module.prepare_browser_rescue() is True


def test_a_service_that_cannot_be_made_leaves_the_window_without_one(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Start-up returns None, attaches nothing, and warns."""
    import bmlibrarian_lite.gui.browser_rescue_service as service_module

    def fail(*_a: Any, **_k: Any) -> None:
        raise OSError("no profile folder")

    monkeypatch.setattr(service_module, "BrowserRescueService", fail)
    attached: list[object] = []
    window = SimpleNamespace(
        storage=object(),
        config=SimpleNamespace(storage=SimpleNamespace(browser_profile_dir="p")),
        attach_rescue_service=attached.append,
    )
    with caplog.at_level(logging.WARNING, logger=app_module.logger.name):
        assert app_module.start_browser_rescue(window, SimpleNamespace()) is None  # type: ignore[arg-type]
    assert attached == []
    assert "OSError" in caplog.text


def _entry() -> RescueEntry:
    request = RescueRequest("doc-1", DOI, "123", "A title", "https://x.org/a.pdf", "x.org", "unpaywall_pdf")
    return RescueEntry(1, request, RescueState.RESCUED, 1, RescueVerdict(RescueOutcome.PDF), 0, "t", "t")


def _window(get_document: Any, queued: bool) -> tuple[SimpleNamespace, list[str]]:
    messages: list[str] = []
    window = SimpleNamespace(
        storage=SimpleNamespace(get_document=get_document),
        systematic_review_tab=SimpleNamespace(reanalyse_transparency=lambda *a: queued),
        status_bar=SimpleNamespace(showMessage=lambda text, _ms: messages.append(text)),
    )
    return window, messages


def test_the_message_promises_a_recheck_only_when_one_was_queued() -> None:
    """True from the manager says it; False does not."""
    window, said = _window(lambda _id: object(), True)
    app_module.LiteMainWindow._on_pdf_rescued(window, _entry())  # type: ignore[arg-type]
    assert "re-checking its transparency" in said[0]
    window, said = _window(lambda _id: object(), False)
    app_module.LiteMainWindow._on_pdf_rescued(window, _entry())  # type: ignore[arg-type]
    assert "re-checking" not in said[0]
    assert said[0].endswith("hidden browser.")


@pytest.mark.parametrize("error", [__import__("sqlite3").Error("locked"), SQLiteError("locked")])
def test_a_storage_error_says_the_recheck_failed(
    caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    """The failure is logged at ERROR; the reader hears of the PDF and that no re-check runs."""
    def fail(_id: str) -> None:
        raise error

    window, said = _window(fail, True)
    with caplog.at_level(logging.ERROR, logger=app_module.logger.name):
        app_module.LiteMainWindow._on_pdf_rescued(window, _entry())  # type: ignore[arg-type]
    assert said and said[0].startswith("Fetched the PDF")
    assert said[0].endswith("its transparency could not be re-checked.")
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_an_unavailable_browser_is_said_while_the_setting_is_on() -> None:
    """The reader who expects walled PDFs fetched is told they will not be."""
    from bmlibrarian_lite.constants import BROWSER_RESCUE_UNAVAILABLE_NOTE

    for hidden, expected in ((True, [BROWSER_RESCUE_UNAVAILABLE_NOTE]), (False, [])):
        messages: list[str] = []
        window = SimpleNamespace(
            _browser_rescue_available=True,
            config=SimpleNamespace(discovery=SimpleNamespace(browser_rescue_hidden=hidden)),
            status_bar=SimpleNamespace(showMessage=lambda text, _ms, m=messages: m.append(text)),
        )
        app_module.LiteMainWindow.browser_rescue_unavailable(window)  # type: ignore[arg-type]
        assert window._browser_rescue_available is False
        assert messages == expected
