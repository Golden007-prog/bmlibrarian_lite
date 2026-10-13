"""The browser rescue engine against a loopback publisher (#480, stage 2).

Everything that pumps QtWebEngine runs in ``browser_rescue_harness.py``, in
its own process (#516, #517). A QtWebEngine that cannot start fails these
tests; it never skips them.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("browser_rescue_harness.py")
HARNESS_TIMEOUT_SECONDS = 300
TIMEOUT_SECONDS = 3


@pytest.fixture(scope="module")
def harness() -> dict[str, Any]:
    """Run the harness once and return its JSON and stderr."""
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.run(
        [sys.executable, str(HARNESS)],
        capture_output=True, text=True, timeout=HARNESS_TIMEOUT_SECONDS, env=env, check=False,
    )
    assert proc.returncode == 0, f"harness failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    return {"out": json.loads(proc.stdout.strip().splitlines()[-1]), "stderr": proc.stderr}


def _attempt(harness: dict[str, Any], name: str) -> dict[str, Any]:
    result: dict[str, Any] = harness["out"]["attempts"][name]
    assert result["finished"], f"{name} never finished"
    return result


def test_a_direct_pdf_is_saved_whole(harness: dict[str, Any]) -> None:
    """A direct PDF is saved and nothing else is left."""
    result = _attempt(harness, "pdf")
    assert result["verdict"]["outcome"] == "pdf"
    assert result["saved"] and result["left"] == []


def test_a_javascript_challenge_is_passed_to_the_pdf(harness: dict[str, Any]) -> None:
    """A cookie-and-reload challenge passes in the hidden page."""
    result = _attempt(harness, "challenge")
    assert result["verdict"]["outcome"] == "pdf"
    assert result["saved"]
    visits = harness["out"]["attempts"]["visits"]
    assert visits.count("/challenge") == 2


def test_a_wall_that_stays_needs_a_person(harness: dict[str, Any]) -> None:
    """A challenge that still stands is reported as one."""
    result = _attempt(harness, "walled")
    assert result["verdict"] == {"outcome": "challenge", "http_status": 403, "markers": ["cloudflare"]}
    assert not result["saved"]


def test_a_404_is_gone_at_once(harness: dict[str, Any]) -> None:
    """A 404 ends the visit without waiting for the timeout."""
    result = _attempt(harness, "gone")
    assert result["verdict"] == {"outcome": "gone", "http_status": 404, "markers": []}
    assert result["seconds"] < TIMEOUT_SECONDS


def test_an_abstract_page_has_no_pdf(harness: dict[str, Any]) -> None:
    """A plain HTML page is no PDF."""
    assert _attempt(harness, "abstract")["verdict"] == {"outcome": "no_pdf", "http_status": 200, "markers": []}


def test_a_pdf_that_is_not_one_is_discarded(harness: dict[str, Any]) -> None:
    """A download without the PDF magic is discarded."""
    result = _attempt(harness, "fakepdf")
    assert result["verdict"]["outcome"] == "no_pdf"
    assert not result["saved"] and result["left"] == []


@pytest.mark.parametrize("name", ["oversize", "oversize_unannounced"])
def test_an_oversize_pdf_is_refused_and_nothing_kept(harness: dict[str, Any], name: str) -> None:
    """A PDF over the limit is refused, announced or not."""
    result = _attempt(harness, name)
    assert result["verdict"]["outcome"] == "too_large"
    assert not result["saved"] and result["left"] == []


def test_a_stalled_download_is_cancelled_as_a_transport_failure(harness: dict[str, Any]) -> None:
    """A download that receives nothing is cancelled."""
    result = _attempt(harness, "stall")
    assert result["verdict"]["outcome"] == "transport"
    assert not result["saved"] and result["left"] == []


def test_a_refused_connection_is_a_transport_failure(harness: dict[str, Any]) -> None:
    """A refused connection is the network's failure."""
    assert _attempt(harness, "refused")["verdict"]["outcome"] == "transport"


def test_response_headers_join_a_multimap() -> None:
    """Repeated headers are joined as HTTP joins them."""
    from PySide6.QtCore import QByteArray

    from bmlibrarian_lite.gui.rescue_attempt import response_headers

    raw = {QByteArray(b"Server"): [QByteArray(b"cloudflare")], QByteArray(b"Vary"): [QByteArray(b"a"), QByteArray(b"b")]}
    assert response_headers(raw) == {"Server": "cloudflare", "Vary": "a, b"}
