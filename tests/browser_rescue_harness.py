"""Drives the browser rescue engine against a loopback server, in its own process.

Run by ``tests/test_browser_rescue_engine.py`` and never collected by pytest:
QtWebEngine needs its own ``QApplication``, and pumping the suite's shared
one inherits earlier tests' backlog (#516, #517). Prints one JSON object as
the last line of standard output.
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bmlibrarian_lite.gui.web_engine import prepare_web_engine

prepare_web_engine()

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from shiboken6 import Shiboken  # noqa: E402

from bmlibrarian_lite.browser_rescue import RescueRequest, RescueState, RescueVerdict  # noqa: E402
from bmlibrarian_lite.config import LiteConfig  # noqa: E402
from bmlibrarian_lite.constants import RESCUE_PRIORITY_WAITING, SERVICE_UNPAYWALL_PDF  # noqa: E402
from bmlibrarian_lite.gui.browser_rescue_service import BrowserRescueService  # noqa: E402
from bmlibrarian_lite.gui.rescue_attempt import RescueAttempt, rescue_profile  # noqa: E402
from bmlibrarian_lite.storage import LiteStorage  # noqa: E402

TIMEOUT_MS = 3_000
STALL_MS = 1_500
MAX_PDF_BYTES = 1_024
POLL_MS = 500
SCENARIO_LIMIT_SECONDS = 30
STALL_HOLD_SECONDS = 8
SLOW_ANSWER_SECONDS = 5
TRICKLE_CHUNKS = 6
TRICKLE_CHUNK_BYTES = 100
TRICKLE_PAUSE_SECONDS = 0.4
# Starts a slow download, then replaces the page with a 404 while it arrives
REPLACING_PAGE = (
    b"<html><title>Replacing</title><script>"
    b"setTimeout(function(){location='/trickle'},100);"
    b"setTimeout(function(){location='/gone'},800);"
    b"</script></html>"
)
PDF_BODY = b"%PDF-1.4\n" + b"0" * 500
OVERSIZE_BODY = b"%PDF-1.4\n" + b"0" * 4_096
CHALLENGE_PAGE = (
    b"<html><head><title>Just a moment...</title></head><body><script>"
    b"window._cf_chl_opt={};setTimeout(function(){document.cookie='cleared=1; path=/';"
    b"location.reload()},300)</script></body></html>"
)
WALL_PAGE = (
    b"<html><head><title>Just a moment...</title></head><body>"
    b"<script>window._cf_chl_opt={};</script></body></html>"
)


class Handler(BaseHTTPRequestHandler):
    """The loopback publisher: one route per scenario."""

    visits: list[str] = []

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Keep the harness's output to its JSON line."""

    def _send(self, status: int, content_type: str, body: bytes, headers: dict[str, str] | None = None,
              announce: bool = True) -> None:
        """Send one complete answer."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if announce:
            self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        """Answer one route."""
        Handler.visits.append(self.path)
        route = self.path.split("?")[0]
        if route == "/pdf":
            self._send(200, "application/pdf", PDF_BODY)
        elif route == "/challenge":
            if "cleared=1" in (self.headers.get("Cookie") or ""):
                self.send_response(302)
                self.send_header("Location", "/pdf")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._send(403, "text/html", CHALLENGE_PAGE, {"Server": "cloudflare"})
        elif route == "/walled":
            self._send(403, "text/html", WALL_PAGE, {"Server": "cloudflare"})
        elif route == "/gone":
            self._send(404, "text/html", b"<html><title>Not found</title>gone</html>")
        elif route == "/abstract":
            self._send(200, "text/html", b"<html><title>Abstract</title><p>An abstract.</p></html>")
        elif route == "/fakepdf":
            self._send(200, "application/pdf", b"<html>not a pdf</html>")
        elif route == "/oversize":
            self._send(200, "application/pdf", OVERSIZE_BODY)
        elif route == "/oversize-unannounced":
            self._send(200, "application/pdf", OVERSIZE_BODY, announce=False)
        elif route == "/stall":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(MAX_PDF_BYTES - 1))
            self.end_headers()
            self.wfile.write(b"%PDF-1.4\n")
            self.wfile.flush()
            time.sleep(STALL_HOLD_SECONDS)
        elif route == "/slow":
            time.sleep(SLOW_ANSWER_SECONDS)
            self._send(200, "text/html", b"<html><title>Late</title><p>Late.</p></html>")
        elif route == "/replacing":
            self._send(200, "text/html", REPLACING_PAGE)
        elif route == "/trickle":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(b"%PDF-1.4\n") + TRICKLE_CHUNKS * TRICKLE_CHUNK_BYTES))
            self.end_headers()
            self.wfile.write(b"%PDF-1.4\n")
            for _ in range(TRICKLE_CHUNKS):
                self.wfile.flush()
                time.sleep(TRICKLE_PAUSE_SECONDS)
                self.wfile.write(b"0" * TRICKLE_CHUNK_BYTES)
        else:
            self._send(404, "text/html", b"no such route")


def run_until(app: QApplication, done: Callable[[], bool], limit_seconds: float) -> bool:
    """Pump the event loop until ``done`` or the limit."""
    deadline = time.monotonic() + limit_seconds
    while not done():
        if time.monotonic() > deadline:
            return False
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)
    return True


def closed_port() -> int:
    """A loopback port nothing listens on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


ATTEMPTS = {
    "pdf": "/pdf",
    "challenge": "/challenge",
    "walled": "/walled",
    "gone": "/gone",
    "abstract": "/abstract",
    "fakepdf": "/fakepdf",
    "oversize": "/oversize",
    "oversize_unannounced": "/oversize-unannounced",
    "stall": "/stall",
    "slow": "/slow",
    "replacing": "/replacing",
}


def attempt_scenarios(app: QApplication, base: str, root: Path) -> dict[str, Any]:
    """Run each visit once, in one profile, and report what came of it."""
    profile = rescue_profile(root / "attempt-profile")
    current: list[RescueAttempt] = []
    profile.downloadRequested.connect(lambda request: current[0].accept_download(request))
    results: dict[str, Any] = {}
    addresses = {name: base + path for name, path in ATTEMPTS.items()}
    addresses["refused"] = f"http://127.0.0.1:{closed_port()}/x.pdf"
    for name, address in addresses.items():
        out = root / "attempts" / name
        out.mkdir(parents=True)
        pdf_path = out / "article.pdf"
        verdicts: list[RescueVerdict] = []
        attempt = RescueAttempt(profile, address, pdf_path, TIMEOUT_MS, STALL_MS, MAX_PDF_BYTES)
        attempt.finished.connect(verdicts.append)
        current[:] = [attempt]
        started = time.monotonic()
        attempt.start()
        finished = run_until(app, lambda v=verdicts: bool(v), SCENARIO_LIMIT_SECONDS)
        results[name] = {
            "finished": finished,
            "verdict": verdicts[0].to_dict() if verdicts else None,
            "saved": pdf_path.exists() and pdf_path.read_bytes().startswith(b"%PDF"),
            "left": sorted(p.name for p in out.iterdir() if p.name != "article.pdf"),
            "seconds": round(time.monotonic() - started, 2),
        }
        attempt.deleteLater()
    results["visits"] = list(Handler.visits)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    Shiboken.delete(profile)
    return results


def pump(app: QApplication, seconds: float) -> None:
    """Run the event loop for a while."""
    run_until(app, lambda: False, seconds)


def service_scenarios(app: QApplication, base: str, root: Path) -> dict[str, Any]:
    """Work a queue through the real service, then quit mid-visit."""
    config = LiteConfig()
    config.storage.data_dir = root / "data"
    storage = LiteStorage(config)
    out = root / "service-pdfs"

    def request(doc: str, path: str) -> RescueRequest:
        return RescueRequest(doc, f"10.1/{doc}", None, f"Title {doc}", base + path, "127.0.0.1",
                             SERVICE_UNPAYWALL_PDF)

    def states() -> dict[str, str]:
        rows = storage.rescue_entries(list(RescueState))
        return {f"{urlparse(e.request.address).path}?{urlparse(e.request.address).query}": e.state.value
                for e in rows}

    def entry_of(tag: str) -> Any:
        return next(e for e in storage.rescue_entries(list(RescueState)) if e.request.address.endswith(tag))

    storage.enqueue_rescues([request("doc-a", "/pdf?a"), request("doc-a", "/walled?a")])
    storage.enqueue_rescues([request("doc-b", "/abstract?b")])
    storage.enqueue_rescues([request("doc-c", "/gone?c")], priority=RESCUE_PRIORITY_WAITING)
    service = BrowserRescueService(
        storage, config, root / "service-profile",
        pdf_path_for=lambda e: out / f"{e.request.document_id}.pdf",
        timeout_ms=TIMEOUT_MS, stall_ms=STALL_MS, poll_ms=POLL_MS, max_pdf_bytes=MAX_PDF_BYTES,
    )
    rescued: list[str] = []
    service.rescued.connect(lambda e: rescued.append(e.request.document_id))
    Handler.visits.clear()
    service.start()
    drained = run_until(
        app, lambda: storage.next_rescue_entry() is None and not service.busy, SCENARIO_LIMIT_SECONDS
    )
    result: dict[str, Any] = {
        "drained": drained,
        # The browser asks for a site icon after a page; that is not a visit
        "visits": [v for v in Handler.visits if v != "/favicon.ico"],
        "states": states(),
        "rescued": list(rescued),
        "doc_a_saved": (out / "doc-a.pdf").exists(),
    }

    # Written by another process: the poll finds it
    LiteStorage(config).enqueue_rescues([request("doc-d", "/pdf?d")])
    result["polled"] = run_until(app, lambda: "doc-d" in rescued, SCENARIO_LIMIT_SECONDS)

    # The hidden pass switched off: entries collect, unvisited
    config.discovery.browser_rescue_hidden = False
    storage.enqueue_rescues([request("doc-e", "/pdf?e")])
    pump(app, 3 * POLL_MS / 1000)
    result["off"] = {"visited": any(v.endswith("?e") for v in Handler.visits),
                     "state": entry_of("?e").state.value}
    config.discovery.browser_rescue_hidden = True
    result["back_on"] = run_until(app, lambda: "doc-e" in rescued, SCENARIO_LIMIT_SECONDS)

    # Quit mid-visit: the entry stays pending, its attempts unchanged
    storage.enqueue_rescues([request("doc-f", "/abstract?f")])
    result["visiting"] = run_until(app, lambda: "/abstract?f" in Handler.visits, SCENARIO_LIMIT_SECONDS)
    service.shutdown()
    pump(app, 0.5)
    quit_entry = entry_of("?f")
    result["quit"] = {"state": quit_entry.state.value, "attempts": quit_entry.attempts}
    return result


def main() -> int:
    """Run the scenarios and print the results."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    app = QApplication(sys.argv)
    # The engine's helper processes may still be flushing the profile as it goes
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        output = {
            "attempts": attempt_scenarios(app, base, root),
            "service": service_scenarios(app, base, root),
        }
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
