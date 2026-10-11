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

# Each test runs QThreads and pumps the event loop (see the fixture in conftest).
pytestmark = pytest.mark.usefixtures("gc_on_the_gui_thread_only")

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
@pytest.mark.parametrize("leave", ["accept", "reject", "close"])
def test_every_way_out_hands_running_threads_to_the_keeper(
    qapp: QApplication, gated_fetch: threading.Event, leave: str
) -> None:
    """OK, Cancel and close return at once; the dialog holds no thread after."""
    from bmlibrarian_lite.gui import thread_keeper

    dialog = _anthropic_only_dialog()
    worker = dialog._model_fetch_workers[0]
    assert worker.isRunning()

    getattr(dialog, leave)()

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
    opened: list[SettingsDialog] = []

    def record_and_cancel(dialog: SettingsDialog) -> int:
        """Stand in for the user pressing Cancel."""
        opened.append(dialog)
        dialog.reject()
        return 0

    with patch.object(SettingsDialog, "exec", record_and_cancel):
        app.LiteMainWindow._show_settings(window)  # type: ignore[arg-type]
        app.LiteMainWindow._show_settings(window)  # type: ignore[arg-type]
    # Only these dialogs' deletes: earlier tests leave deletes that crash (#516).
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
    assert PROVIDER_PROBE_TIMEOUT_SECONDS * 1000 < KEPT_THREAD_EXIT_WAIT_MS


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
