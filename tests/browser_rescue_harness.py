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

from bmlibrarian_lite.gui.web_engine import prepare_web_engine

prepare_web_engine()

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from shiboken6 import Shiboken  # noqa: E402

from bmlibrarian_lite.browser_rescue import RescueVerdict  # noqa: E402
from bmlibrarian_lite.gui.rescue_attempt import RescueAttempt, rescue_profile  # noqa: E402

TIMEOUT_MS = 3_000
STALL_MS = 1_500
MAX_PDF_BYTES = 1_024
SCENARIO_LIMIT_SECONDS = 30
STALL_HOLD_SECONDS = 8
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


def main() -> int:
    """Run the scenarios and print the results."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    app = QApplication(sys.argv)
    # The engine's helper processes may still be flushing the profile as it goes
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        output = {"attempts": attempt_scenarios(app, base, root)}
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
