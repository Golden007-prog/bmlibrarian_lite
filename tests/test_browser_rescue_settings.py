"""The browser rescue's setting and start-up (#480, stage 2)."""

import json
import logging
from pathlib import Path

import pytest

from bmlibrarian_lite.config import LiteConfig
from bmlibrarian_lite.constants import BROWSER_PROFILE_DIRNAME


def test_the_hidden_pass_is_on_by_default() -> None:
    """The hidden pass is on by default."""
    assert LiteConfig().discovery.browser_rescue_hidden is True


def test_the_setting_round_trips(tmp_path: Path) -> None:
    """The setting round trips."""
    path = tmp_path / "config.json"
    config = LiteConfig()
    config.discovery.browser_rescue_hidden = False
    config.save(path)
    assert LiteConfig.load(path).discovery.browser_rescue_hidden is False


@pytest.mark.parametrize("stored", ["false", 0, None, [1]])
def test_a_setting_that_is_not_a_boolean_is_the_default(
    tmp_path: Path, stored: object, caplog: pytest.LogCaptureFixture
) -> None:
    """A stored value that is not a boolean is the default, and the log says so.

    The default sends an unattended browser to publishers, so a reader who
    meant "off" is told why it is on.
    """
    path = tmp_path / "config.json"
    LiteConfig().save(path)
    data = json.loads(path.read_text())
    data["discovery"]["browser_rescue_hidden"] = stored
    path.write_text(json.dumps(data))
    with caplog.at_level(logging.WARNING, logger="bmlibrarian_lite.config"):
        assert LiteConfig.load(path).discovery.browser_rescue_hidden is True
    assert "browser_rescue_hidden" in caplog.text


def test_the_profile_lives_under_the_data_directory(tmp_path: Path) -> None:
    """The profile lives under the data directory."""
    config = LiteConfig()
    config.storage.data_dir = tmp_path
    assert config.storage.browser_profile_dir == tmp_path / BROWSER_PROFILE_DIRNAME


@pytest.fixture
def qapp():
    """The suite's QApplication, made if no test has made one yet."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_the_dialog_shows_and_saves_the_setting(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    """The dialog shows and saves the setting."""
    from bmlibrarian_lite.gui.settings_dialog import SettingsDialog

    config = LiteConfig()
    monkeypatch.setattr(LiteConfig, "save", lambda self, *a, **k: None)
    dialog = SettingsDialog(config)
    assert dialog.browser_rescue_hidden_check.isChecked()
    dialog.browser_rescue_hidden_check.setChecked(False)
    dialog._save_config()
    assert config.discovery.browser_rescue_hidden is False
    config.discovery.browser_rescue_hidden = True
    assert SettingsDialog(config).browser_rescue_hidden_check.isChecked()


def test_an_unavailable_browser_is_shown_in_the_dialog(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the hidden browser, its setting is disabled, kept, and explained."""
    from bmlibrarian_lite.constants import BROWSER_RESCUE_UNAVAILABLE_NOTE
    from bmlibrarian_lite.gui.settings_dialog import SettingsDialog

    config = LiteConfig()
    monkeypatch.setattr(LiteConfig, "save", lambda self, *a, **k: None)
    dialog = SettingsDialog(config, browser_rescue_available=False)
    assert not dialog.browser_rescue_hidden_check.isEnabled()
    assert dialog.browser_rescue_unavailable_note is not None
    assert BROWSER_RESCUE_UNAVAILABLE_NOTE in dialog.browser_rescue_unavailable_note.text()
    dialog._save_config()
    assert config.discovery.browser_rescue_hidden is True
    # Control: an available browser shows no such note
    available = SettingsDialog(config)
    assert available.browser_rescue_hidden_check.isEnabled()
    assert available.browser_rescue_unavailable_note is None


def test_the_web_engine_is_prepared_before_any_application() -> None:
    """The conftest prepared it, so a page can be made in this suite's QApplication."""
    from PySide6.QtCore import QCoreApplication, Qt

    assert QCoreApplication.testAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)


def test_preparing_too_late_says_so(qapp) -> None:
    """Preparing too late says so."""
    from bmlibrarian_lite.gui.web_engine import prepare_web_engine

    with pytest.raises(RuntimeError, match="before the QApplication"):
        prepare_web_engine()
