"""The settings dialog lets go of its provider threads on every way out (#514).

``SettingsDialog`` asks each enabled provider for its models on a thread, and
a Test Connection click starts another. OK and Cancel leave through ``done()``
without a ``closeEvent``, so the dialog's wait used to run on the title-bar
close alone, and after it the dialog dropped threads that were still running,
which aborts the process. Every opening also left a dialog behind. Now every
way out disconnects the threads from the dialog and hands any still running
to the thread keeper, the main window deletes each dialog it opens, and the
probes are bounded so the quit wait can outlast them.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Generator
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

    from bmlibrarian_lite.gui.settings_dialog import SettingsDialog

# How long a test lets the event loop run before it gives up on a thread.
_SETTLE_SECONDS = 5.0

LATE_MODEL = "a-model-that-came-too-late"


@pytest.fixture
def qapp() -> QApplication:
    """A QApplication for the dialog tests."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def gate() -> Generator[threading.Event, None, None]:
    """An event the faked provider threads wait on; always set on teardown."""
    event = threading.Event()
    yield event
    event.set()


@pytest.fixture(autouse=True)
def _the_keeper_starts_empty() -> None:
    """A thread an earlier test left kept is named here, not by a count below."""
    from bmlibrarian_lite.gui import thread_keeper

    assert thread_keeper.kept_thread_count() == 0, "an earlier test left a thread kept"


@pytest.fixture
def gated_fetch(
    qapp: QApplication, gate: threading.Event, monkeypatch: pytest.MonkeyPatch
) -> threading.Event:
    """Model fetches really start, wait for the gate, then report a late model.

    No provider is asked: ``run`` is replaced. Use with ``real_model_fetch``.
    """
    from bmlibrarian_lite.gui import settings_dialog

    def run(worker: settings_dialog.ModelFetchWorker) -> None:
        """Wait for the gate, then report one model."""
        gate.wait()
        worker.models_fetched.emit(worker.provider, [LATE_MODEL])

    monkeypatch.setattr(settings_dialog.ModelFetchWorker, "run", run)
    return gate


def _anthropic_only_dialog() -> SettingsDialog:
    """A dialog that fetches Anthropic's models and nothing else."""
    from bmlibrarian_lite.config import LiteConfig
    from bmlibrarian_lite.gui.settings_dialog import SettingsDialog

    config = LiteConfig()
    config.models.providers["anthropic"].enabled = True
    config.models.providers["ollama"].enabled = False
    return SettingsDialog(config)


def _offered_models(dialog: SettingsDialog) -> list[str]:
    """The models Anthropic's combo offers."""
    combo = dialog.anthropic_default_model
    return [combo.itemText(i) for i in range(combo.count())]


def _leave(dialog: SettingsDialog, how: str) -> None:
    """Leave the dialog the way a reader does.

    Args:
        dialog: The dialog to leave.
        how: "accept" (OK), "reject" (Cancel), "close" (a dialog never shown),
            "escape" or "title_bar" (a shown dialog's close button).
    """
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    if how == "escape":
        dialog.show()
        # To the dialog's handler itself: a key event sent from code goes
        # through the application's shortcut map first, and in the full suite
        # something an earlier test leaked takes Escape there (see #516).
        dialog.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
            )
        )
    elif how == "title_bar":
        dialog.show()
        dialog.close()
    else:
        getattr(dialog, how)()


def _settle(qapp: QApplication, until: Callable[[], bool]) -> None:
    """Run the event loop until ``until`` holds, or time runs out."""
    deadline = time.monotonic() + _SETTLE_SECONDS
    while not until() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


@pytest.mark.real_model_fetch
def test_control_a_late_result_reaches_a_dialog_still_open(
    qapp: QApplication, gated_fetch: threading.Event
) -> None:
    """Control: the faked fetch does reach a dialog nobody has closed."""
    dialog = _anthropic_only_dialog()
    worker = dialog._model_fetch_workers[0]
    assert worker.isRunning()

    gated_fetch.set()
    _settle(qapp, lambda: LATE_MODEL in _offered_models(dialog))
    assert LATE_MODEL in _offered_models(dialog)
    dialog.reject()
    _settle(qapp, worker.isFinished)


@pytest.mark.real_model_fetch
@pytest.mark.parametrize("leave", ["accept", "reject", "close", "escape", "title_bar"])
def test_every_way_out_hands_running_threads_to_the_keeper(
    qapp: QApplication, gated_fetch: threading.Event, leave: str
) -> None:
    """Every way out returns at once, and the dialog holds no thread after."""
    from bmlibrarian_lite.gui import thread_keeper

    dialog = _anthropic_only_dialog()
    worker = dialog._model_fetch_workers[0]
    assert worker.isRunning()

    _leave(dialog, leave)
    assert not dialog.isVisible()

    assert worker.isRunning(), "leaving does not wait for the provider"
    assert dialog._model_fetch_workers == []
    assert dialog._connection_test_workers == []
    assert thread_keeper.kept_thread_count() == 1

    gated_fetch.set()
    _settle(qapp, lambda: thread_keeper.kept_thread_count() == 0)
    assert thread_keeper.kept_thread_count() == 0
    assert LATE_MODEL not in _offered_models(dialog), (
        "a result that comes after the dialog finished never reaches it"
    )


@pytest.mark.real_model_fetch
def test_a_connection_test_still_running_is_kept_and_disconnected(
    qapp: QApplication, gate: threading.Event, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Test Connection thread is handed over too, and its verdict is dropped."""
    from bmlibrarian_lite.gui import settings_dialog, thread_keeper

    def run(worker: settings_dialog.ProviderConnectionTestWorker) -> None:
        """Wait for the gate, then report a success."""
        gate.wait()
        worker.test_completed.emit(worker.provider, True, "late verdict")

    monkeypatch.setattr(settings_dialog.ProviderConnectionTestWorker, "run", run)
    monkeypatch.setattr(settings_dialog.ModelFetchWorker, "start", lambda self: None)

    dialog = _anthropic_only_dialog()
    dialog.anthropic_test_btn.click()
    assert dialog._connection_test_workers[0].isRunning()

    dialog.reject()
    assert dialog._connection_test_workers == []
    assert thread_keeper.kept_thread_count() == 1

    gate.set()
    _settle(qapp, lambda: thread_keeper.kept_thread_count() == 0)
    assert dialog.anthropic_status.text() != "late verdict"


@pytest.mark.real_model_fetch
@pytest.mark.parametrize("leave", [None, "reject"])
def test_a_verdict_already_sent_when_the_dialog_finishes_starts_no_thread(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, leave: str | None
) -> None:
    """A successful Test Connection queued before the dialog finished starts no fetch.

    Disconnecting a thread does not recall a result it already sent: that one
    is still delivered. A fetch it started then would belong to nobody, and
    the dialog's deletion would drop it while it ran, which aborts the
    process. The control (``None``) keeps the dialog open: the same verdict
    starts the fetch there.
    """
    from bmlibrarian_lite.gui import settings_dialog

    def run(worker: settings_dialog.ProviderConnectionTestWorker) -> None:
        """Report a success at once."""
        worker.test_completed.emit(worker.provider, True, "verdict")

    started: list[settings_dialog.ModelFetchWorker] = []
    monkeypatch.setattr(settings_dialog.ProviderConnectionTestWorker, "run", run)
    monkeypatch.setattr(
        settings_dialog.ModelFetchWorker, "start", lambda self: started.append(self)
    )

    dialog = _anthropic_only_dialog()
    started.clear()  # the fetch the dialog asks for as it opens
    dialog.anthropic_test_btn.click()
    test_worker = dialog._connection_test_workers[0]
    assert test_worker.wait(int(_SETTLE_SECONDS * 1000))  # the verdict is queued
    if leave:
        getattr(dialog, leave)()

    qapp.processEvents()

    if leave:
        assert started == [], "a released dialog starts no thread"
        assert dialog._model_fetch_workers == []
        assert dialog.anthropic_status.text() != "verdict"
    else:
        assert len(started) == 1, "control: the verdict starts a fetch"
        dialog.reject()


def test_closing_a_dialog_twice_is_harmless(qapp: QApplication) -> None:
    """A second way out finds nothing left to release."""
    dialog = _anthropic_only_dialog()
    dialog.accept()
    dialog.close()
    dialog.reject()
    assert dialog._model_fetch_workers == []


def test_each_opening_of_the_settings_dialog_is_deleted(qapp: QApplication) -> None:
    """The main window keeps no dialog from an earlier opening."""
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QWidget

    from bmlibrarian_lite.config import LiteConfig
    from bmlibrarian_lite.gui import app
    from bmlibrarian_lite.gui.settings_dialog import SettingsDialog

    window = QWidget()
    window.config = LiteConfig()  # type: ignore[attr-defined]
    window.status_bar = MagicMock()  # type: ignore[attr-defined]
    window._browser_rescue_available = True  # type: ignore[attr-defined]
    opened: list[SettingsDialog] = []

    def record_and_cancel(dialog: SettingsDialog) -> int:
        """Stand in for the user pressing Cancel."""
        opened.append(dialog)
        dialog.reject()
        return 0

    with patch.object(SettingsDialog, "exec", record_and_cancel):
        app.LiteMainWindow._show_settings(window)  # type: ignore[arg-type]
        app.LiteMainWindow._show_settings(window)  # type: ignore[arg-type]
    # Only these dialogs' deletes: a global flush can reach other tests' leaked widgets (#516).
    for dialog in opened:
        QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)

    assert len(opened) == 2
    assert window.findChildren(SettingsDialog) == []
    assert not any(shiboken6.isValid(dialog) for dialog in opened)


def test_the_probes_ask_with_a_bounded_timeout_and_no_retries() -> None:
    """A provider that never answers cannot hold a probe past the quit wait."""
    from bmlibrarian_lite.constants import (
        KEPT_THREAD_EXIT_WAIT_MS,
        PROVIDER_PROBE_TIMEOUT_SECONDS,
        PROVIDER_PROBE_WAITS_PER_REQUEST,
    )
    from bmlibrarian_lite.gui.settings_dialog import probe_provider_kwargs

    assert probe_provider_kwargs("anthropic", "") == {
        "request_timeout_seconds": PROVIDER_PROBE_TIMEOUT_SECONDS,
        "max_retries": 0,
    }
    assert probe_provider_kwargs("ollama", "http://h:1") == {
        "request_timeout_seconds": PROVIDER_PROBE_TIMEOUT_SECONDS,
        "base_url": "http://h:1",
    }
    assert probe_provider_kwargs("ollama", "") == {
        "request_timeout_seconds": PROVIDER_PROBE_TIMEOUT_SECONDS,
    }
    probe_wait_ms = PROVIDER_PROBE_WAITS_PER_REQUEST * PROVIDER_PROBE_TIMEOUT_SECONDS * 1000
    assert probe_wait_ms < KEPT_THREAD_EXIT_WAIT_MS


def test_the_providers_build_their_clients_with_the_probe_bounds() -> None:
    """The bound reaches each client; a provider built without one keeps its defaults."""
    from bmlibrarian_lite.llm.providers import get_provider

    with patch("anthropic.Anthropic") as anthropic_client:
        get_provider(
            "anthropic", api_key="k", request_timeout_seconds=7.0, max_retries=0
        )._get_client()
        get_provider("anthropic", api_key="k")._get_client()
    assert anthropic_client.call_args_list[0].kwargs == {
        "api_key": "k",
        "timeout": 7.0,
        "max_retries": 0,
    }
    assert anthropic_client.call_args_list[1].kwargs == {"api_key": "k"}

    with patch("ollama.Client") as ollama_client:
        get_provider("ollama", base_url="http://h:1", request_timeout_seconds=7.0)._get_client()
        get_provider("ollama", base_url="http://h:1")._get_client()
    assert ollama_client.call_args_list[0].kwargs == {"host": "http://h:1", "timeout": 7.0}
    assert ollama_client.call_args_list[1].kwargs == {"host": "http://h:1"}


@pytest.mark.parametrize(("provider", "host"), [("anthropic", ""), ("ollama", "http://h:1")])
def test_the_model_fetch_asks_once_with_the_probe_bounds(
    monkeypatch: pytest.MonkeyPatch, provider: str, host: str
) -> None:
    """The fetch builds its provider with the bounds and asks only for the IDs."""
    from bmlibrarian_lite.gui import settings_dialog
    from bmlibrarian_lite.llm import providers

    get_provider = MagicMock()
    get_provider.return_value.list_model_ids.return_value = ["m"]
    monkeypatch.setattr(providers, "get_provider", get_provider)
    worker = settings_dialog.ModelFetchWorker(provider, host)
    fetched: list[tuple[str, list[str]]] = []
    worker.models_fetched.connect(lambda p, ids: fetched.append((p, ids)))

    worker.run()

    get_provider.assert_called_once_with(
        provider, **settings_dialog.probe_provider_kwargs(provider, host)
    )
    get_provider.return_value.list_models.assert_not_called()
    assert fetched == [(provider, ["m"])]


@pytest.mark.parametrize(("provider", "host"), [("anthropic", ""), ("ollama", "http://h:1")])
def test_the_connection_test_asks_with_the_probe_bounds(
    monkeypatch: pytest.MonkeyPatch, provider: str, host: str
) -> None:
    """Test Connection builds its provider with the same bounds."""
    from bmlibrarian_lite.gui import settings_dialog
    from bmlibrarian_lite.llm import providers

    get_provider = MagicMock()
    get_provider.return_value.test_connection.return_value = (True, "ok")
    monkeypatch.setattr(providers, "get_provider", get_provider)

    settings_dialog.ProviderConnectionTestWorker(provider, host).run()

    get_provider.assert_called_once_with(
        provider, **settings_dialog.probe_provider_kwargs(provider, host)
    )


def test_a_failed_fetch_says_why_in_plain_words(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A timed-out fetch reports a timeout, and logs the traceback."""
    import httpx

    from bmlibrarian_lite.gui import settings_dialog
    from bmlibrarian_lite.llm import providers

    get_provider = MagicMock()
    get_provider.return_value.list_model_ids.side_effect = httpx.ReadTimeout("read")
    monkeypatch.setattr(providers, "get_provider", get_provider)
    worker = settings_dialog.ModelFetchWorker("ollama", "http://h:1")
    failures: list[str] = []
    worker.fetch_failed.connect(lambda _p, error: failures.append(error))

    with caplog.at_level("WARNING", logger=settings_dialog.__name__):
        worker.run()

    assert failures == [providers.base.describe_request_failure(httpx.ReadTimeout("read"))]
    assert "No answer in time" in failures[0]
    assert caplog.records[0].exc_info is not None


@pytest.mark.parametrize("provider", ["anthropic", "ollama"])
def test_a_failed_fetch_is_shown_against_its_provider(
    qapp: QApplication, provider: str
) -> None:
    """The built-in models are not passed off as the live list."""
    from bmlibrarian_lite.gui.settings_dialog import models_fetch_failed_text

    dialog = _anthropic_only_dialog()
    dialog._on_models_fetch_failed(provider, "No answer in time.")
    status = dialog.anthropic_status if provider == "anthropic" else dialog.ollama_status
    assert status.text() == models_fetch_failed_text("No answer in time.")
    dialog.reject()


def test_a_released_dialog_shows_no_late_failure(qapp: QApplication) -> None:
    """A failure queued before the dialog finished changes nothing on it."""
    dialog = _anthropic_only_dialog()
    before = dialog.anthropic_status.text()
    dialog.reject()
    dialog._on_models_fetch_failed("anthropic", "late")
    assert dialog.anthropic_status.text() == before


@pytest.mark.parametrize(
    ("error", "shown"),
    [
        ("Connection refused", "Could not load the model list: Connection refused. "),
        ("No answer in time.", "Could not load the model list: No answer in time. "),
        ("busy! ", "Could not load the model list: busy! "),
    ],
)
def test_the_failed_fetch_text_ends_its_reason_with_a_stop(error: str, shown: str) -> None:
    """The reason reads as its own sentence before what the dialog does about it."""
    from bmlibrarian_lite.gui.settings_dialog import models_fetch_failed_text

    assert models_fetch_failed_text(error) == (
        shown + "Showing the built-in models; Test Connection asks again."
    )


def test_the_dialog_closes_even_if_letting_go_fails(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure while letting go of the threads never leaves the dialog stuck open."""
    import contextlib

    from PySide6.QtWidgets import QDialog

    dialog = _anthropic_only_dialog()
    dialog.show()

    def fail() -> None:
        """Stand in for a release that raises."""
        raise RuntimeError("release failed")

    monkeypatch.setattr(dialog, "_release_workers", fail)
    with contextlib.suppress(RuntimeError):
        dialog.reject()

    assert not dialog.isVisible()
    assert dialog.result() == QDialog.DialogCode.Rejected
