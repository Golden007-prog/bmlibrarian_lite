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


def test_a_server_that_never_answers_is_a_transport_failure(harness: dict[str, Any]) -> None:
    """No main-frame answer in time: the visit learnt nothing, not no_pdf."""
    result = _attempt(harness, "slow")
    assert result["verdict"]["outcome"] == "transport"
    assert result["verdict"]["http_status"] is None


def test_a_page_replaced_during_a_download_does_not_end_it(harness: dict[str, Any]) -> None:
    """A 404 navigated to mid-download is ignored; the PDF arrives whole."""
    result = _attempt(harness, "replacing")
    assert result["verdict"]["outcome"] == "pdf"
    assert result["saved"] and result["left"] == []
    assert "/gone" in harness["out"]["attempts"]["visits"]


def test_response_headers_join_a_multimap() -> None:
    """Repeated headers are joined as HTTP joins them."""
    from PySide6.QtCore import QByteArray

    from bmlibrarian_lite.gui.rescue_attempt import response_headers

    raw = {QByteArray(b"Server"): [QByteArray(b"cloudflare")], QByteArray(b"Vary"): [QByteArray(b"a"), QByteArray(b"b")]}
    assert response_headers(raw) == {"Server": "cloudflare", "Vary": "a, b"}


PROFILE_RELEASE_WARNING = "Release of profile requested but WebEnginePage still not deleted"


def _service(harness: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = harness["out"]["service"]
    assert result["drained"], "the queue was never drained"
    return result


def test_the_waiting_entry_goes_first_then_the_oldest(harness: dict[str, Any]) -> None:
    """A waiting entry is visited first, then the oldest."""
    visits = _service(harness)["visits"]
    assert visits[:3] == ["/gone?c", "/pdf?a", "/abstract?b"]


def test_a_rescue_supersedes_its_documents_other_address(harness: dict[str, Any]) -> None:
    """A rescued document's other address is superseded and never visited."""
    service = _service(harness)
    assert service["states"]["/pdf?a"] == "rescued"
    assert service["states"]["/walled?a"] == "superseded"
    assert "/walled?a" not in service["visits"]
    assert service["doc_a_saved"]


def test_each_entry_settles_by_its_verdict(harness: dict[str, Any]) -> None:
    """Each entry's state follows its verdict."""
    states = _service(harness)["states"]
    assert (states["/abstract?b"], states["/gone?c"]) == ("no_pdf", "gone")


def test_a_rescue_is_announced_once(harness: dict[str, Any]) -> None:
    """A rescue is announced exactly once."""
    assert _service(harness)["rescued"][:1] == ["doc-a"]
    assert _service(harness)["rescued"].count("doc-a") == 1


def test_an_entry_another_process_wrote_is_found_by_the_poll(harness: dict[str, Any]) -> None:
    """The poll finds an entry another process wrote."""
    assert _service(harness)["polled"]


def test_with_the_hidden_pass_off_entries_collect_unvisited(harness: dict[str, Any]) -> None:
    """With the pass off, entries stay pending and unvisited."""
    service = _service(harness)
    assert service["off"] == {"visited": False, "state": "pending"}
    assert service["back_on"]


def test_a_quit_mid_visit_leaves_the_entry_pending(harness: dict[str, Any]) -> None:
    """A quit mid-visit leaves the entry pending with no attempt counted."""
    service = _service(harness)
    assert service["visiting"]
    assert service["quit"] == {"state": "pending", "attempts": 0}


def test_quit_releases_the_page_before_the_profile(harness: dict[str, Any]) -> None:
    """No QtWebEngine release warning at quit."""
    assert PROFILE_RELEASE_WARNING not in harness["stderr"]
