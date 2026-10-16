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

import fitz

from bmlibrarian_lite.gui.web_engine import prepare_web_engine

prepare_web_engine()

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QUrl  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from shiboken6 import Shiboken  # noqa: E402

from bmlibrarian_lite.browser_rescue import (  # noqa: E402
    RescueOutcome,
    RescueRequest,
    RescueState,
    RescueVerdict,
)
from bmlibrarian_lite.config import LiteConfig  # noqa: E402
from bmlibrarian_lite.constants import RESCUE_PRIORITY_WAITING, SERVICE_UNPAYWALL_PDF  # noqa: E402
from bmlibrarian_lite.gui import rescue_attempt as rescue_attempt_module  # noqa: E402
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
TRICKLE_CHUNKS = 10
# How long a person's visit is left open before it is left: past the hidden timeout
PERSON_WAIT_SECONDS = 2 * TIMEOUT_MS / 1000
TRICKLE_PAUSE_SECONDS = 0.4
# How long after /trickle sent its headers the server calls the download begun,
# which gives QtWebEngine time to turn the response into a download
DOWNLOAD_SETTLE_SECONDS = 0.5
DOWNLOAD_POLL_MS = 100
# Starts a slow download, then replaces the page with a 404 while it arrives.
# The page waits for the server to say the download has begun rather than for a
# fixed time: on a slow runner a wall-clock timer fired before the download had
# started, so the second navigation aborted it and "gone" was the right verdict.
REPLACING_PAGE = (
    b"<html><title>Replacing</title><script>"
    b"setTimeout(function(){location='/trickle'},100);"
    b"function poll(){fetch('/trickle-started').then(function(r){return r.text()})"
    b".then(function(t){if(t==='yes'){location='/gone'}else{setTimeout(poll,"
    + str(DOWNLOAD_POLL_MS).encode()
    + b")}})}"
    b"setTimeout(poll,200);"
    b"</script></html>"
)
#: The title every visit checks a PDF against (#532), which PDF_BODY carries.
ARTICLE_TITLE = "Coffee and blood pressure"


def _pdf(text: str) -> bytes:
    """A real one-page PDF carrying ``text``, small enough for MAX_PDF_BYTES."""
    document = fitz.open()
    document.new_page().insert_text((50, 72), text)
    body = document.tobytes(garbage=4, deflate=True)
    document.close()
    return body


PDF_BODY = _pdf(ARTICLE_TITLE)
#: Another article's PDF: served whole, and not this article's (#532).
OTHER_PDF_BODY = _pdf("Why the P value is not enough")
#: Begins as a PDF, and no reader can open it (#532).
BROKEN_PDF_BODY = b"%PDF-1.4\n" + b"0" * 100
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
# An interstitial whose script moves on to a PDF that answers only after the
# visit's timeout: the page in view at the timeout is the one being left.
INTERSTITIAL_PAGE = (
    b"<html><title>Redirecting</title><p>Fetching the PDF...</p><script>"
    b"setTimeout(function(){location='/slowpdf'},100);</script></html>"
)


class Handler(BaseHTTPRequestHandler):
    """The loopback publisher: one route per scenario."""

    visits: list[str] = []
    # When /trickle's headers went out (time.monotonic()); None before any
    trickle_headers_sent: float | None = None

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
        elif route == "/otherpdf":
            self._send(200, "application/pdf", OTHER_PDF_BODY)
        elif route == "/brokenpdf":
            self._send(200, "application/pdf", BROKEN_PDF_BODY)
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
        elif route == "/unavailable":
            self._send(
                503, "text/html",
                b"<html><title>Unavailable</title><p>This page is temporarily unavailable.</p></html>",
                {"Server": "nginx"},
            )
        elif route == "/abstract":
            self._send(200, "text/html", b"<html><title>Abstract</title><p>An abstract.</p></html>")
        elif route == "/fakepdf":
            self._send(200, "application/pdf", b"<html>not a pdf</html>")
        elif route == "/empty":
            self._send(200, "application/pdf", b"", {"Content-Disposition": "attachment; filename=a.pdf"})
        elif route == "/walldownload":
            self._send(200, "application/pdf", WALL_PAGE)
        elif route == "/interstitial":
            self._send(200, "text/html", INTERSTITIAL_PAGE)
        elif route == "/slowpdf":
            time.sleep(SLOW_ANSWER_SECONDS)
            self._send(200, "application/pdf", PDF_BODY)
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
        elif route == "/trickle-started":
            sent = Handler.trickle_headers_sent
            begun = sent is not None and time.monotonic() - sent >= DOWNLOAD_SETTLE_SECONDS
            self._send(200, "text/plain", b"yes" if begun else b"no")
        elif route == "/trickle":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(PDF_BODY)))
            self.end_headers()
            Handler.trickle_headers_sent = time.monotonic()
            # The article's real PDF (#532), its head at once, the rest in
            # TRICKLE_CHUNKS pieces
            size = -(-len(PDF_BODY) // (TRICKLE_CHUNKS + 1))
            self.wfile.write(PDF_BODY[:size])
            for i in range(1, TRICKLE_CHUNKS + 1):
                self.wfile.flush()
                time.sleep(TRICKLE_PAUSE_SECONDS)
                self.wfile.write(PDF_BODY[i * size:(i + 1) * size])
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
    "otherpdf": "/otherpdf",
    "brokenpdf": "/brokenpdf",
    "challenge": "/challenge",
    "walled": "/walled",
    "gone": "/gone",
    "abstract": "/abstract",
    "unavailable": "/unavailable",
    "fakepdf": "/fakepdf",
    "oversize": "/oversize",
    "oversize_unannounced": "/oversize-unannounced",
    "stall": "/stall",
    "slow": "/slow",
    "replacing": "/replacing",
    "empty": "/empty",
    "walldownload": "/walldownload",
    "interstitial": "/interstitial",
    "unkept": "/pdf?unkept",
    "nofolder": "/pdf?nofolder",
}


def _pdf_path_where_the_rename_fails(out: Path) -> Path:
    """A folder already stands where the PDF goes, so it cannot be renamed into place."""
    pdf_path = out / "article.pdf"
    pdf_path.mkdir()
    return pdf_path


def _pdf_path_with_no_folder(out: Path) -> Path:
    """A file stands where the PDF's folder should be made."""
    (out / "blocker").write_text("a file where the folder should be")
    return out / "blocker" / "article.pdf"


#: Scenarios whose PDF path is prepared to fail; the rest save to out/article.pdf
PDF_PATHS: dict[str, Callable[[Path], Path]] = {
    "unkept": _pdf_path_where_the_rename_fails,
    "nofolder": _pdf_path_with_no_folder,
}


def run_attempt(
    app: QApplication, profile: QWebEngineProfile, current: list[RescueAttempt], address: str,
    out: Path, pdf_path: Path, during: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Make one visit and report what came of it.

    Args:
        app: The application, pumped until the verdict.
        profile: The shared profile.
        current: Holds the visit downloads are handed to.
        address: The address visited.
        out: The scenario's folder.
        pdf_path: Where a PDF that arrives is saved.
        during: Called once the visit has started.

    Returns:
        Whether it finished, its verdict, whether a PDF was saved, and what else
        was left in the folder.
    """
    verdicts: list[RescueVerdict] = []
    attempt = RescueAttempt(profile, address, pdf_path, TIMEOUT_MS, STALL_MS, MAX_PDF_BYTES, title=ARTICLE_TITLE)
    attempt.finished.connect(verdicts.append)
    current[:] = [attempt]
    started = time.monotonic()
    attempt.start()
    if during is not None:
        during()
    finished = run_until(app, lambda: bool(verdicts), SCENARIO_LIMIT_SECONDS)
    result = {
        "finished": finished,
        "verdict": verdicts[0].to_dict() if verdicts else None,
        "saved": pdf_path.is_file() and pdf_path.read_bytes().startswith(b"%PDF"),
        "left": sorted(p.name for p in out.iterdir() if p.name != "article.pdf"),
        "seconds": round(time.monotonic() - started, 2),
    }
    attempt.deleteLater()
    return result


def attempt_scenarios(app: QApplication, base: str, root: Path) -> dict[str, Any]:
    """Run each visit once, in one profile, and report what came of it."""
    profile = rescue_profile(root / "attempt-profile")
    current: list[RescueAttempt] = []
    profile.downloadRequested.connect(lambda request: current[0].accept_download(request))
    results: dict[str, Any] = {}
    addresses = {name: base + path for name, path in ATTEMPTS.items()}
    addresses["refused"] = f"http://127.0.0.1:{closed_port()}/x.pdf"
    for name, address in addresses.items():
        Handler.trickle_headers_sent = None
        out = root / "attempts" / name
        out.mkdir(parents=True)
        pdf_path = PDF_PATHS.get(name, lambda o: o / "article.pdf")(out)
        results[name] = run_attempt(app, profile, current, address, out, pdf_path)

    # A page an earlier visit let go of starts a download while the next
    # visit is under way: another article's PDF, which that visit refuses
    out = root / "attempts" / "foreign"
    out.mkdir(parents=True)
    stray = QWebEnginePage(profile)
    results["foreign"] = run_attempt(
        app, profile, current, base + "/abstract?foreign", out, out / "article.pdf",
        during=lambda: stray.load(QUrl(base + "/pdf?stray")),
    )
    Shiboken.delete(stray)

    results["visits"] = list(Handler.visits)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    Shiboken.delete(profile)
    return results


def pump(app: QApplication, seconds: float) -> None:
    """Run the event loop for a while."""
    run_until(app, lambda: False, seconds)


def shown_scenarios(app: QApplication, base: str, root: Path) -> dict[str, Any]:
    """Visits shown in a view: a person's, and one judged as a walk judges it."""
    profile = rescue_profile(root / "shown-profile")
    current: list[RescueAttempt] = []
    profile.downloadRequested.connect(lambda request: current[0].accept_download(request))
    view = QWebEngineView()
    view.show()
    results: dict[str, Any] = {}

    def person(name: str, path: str, wait: float) -> dict[str, Any]:
        """One person's visit, left after ``wait`` seconds if nothing ended it."""
        out = root / "shown" / name
        out.mkdir(parents=True)
        verdicts: list[RescueVerdict] = []
        attempt = RescueAttempt(profile, base + path, out / "article.pdf", TIMEOUT_MS, STALL_MS,
                                MAX_PDF_BYTES, title=ARTICLE_TITLE, view=view, judge_pages=False)
        attempt.finished.connect(verdicts.append)
        current[:] = [attempt]
        attempt.start()
        shown = view.page() is not None and view.page().profile() is profile
        finished = run_until(app, lambda: bool(verdicts), wait)
        if not finished:
            attempt.abort()
        pump(app, 0.2)
        # A finished visit lets its page go with deleteLater; deliver that first
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        result = {
            "shown": shown,
            "finished": finished,
            "verdict": verdicts[0].to_dict() if verdicts else None,
            "saved": (out / "article.pdf").is_file(),
            "view_alive": view.page() is not None,
            "view_released": view.page().profile() is not profile,
        }
        attempt.deleteLater()
        return result

    results["pdf"] = person("pdf", "/pdf?shown", SCENARIO_LIMIT_SECONDS)

    # Another article's PDF is held for the person, who keeps it (#532)
    out = root / "shown" / "held"
    out.mkdir(parents=True)
    held_verdicts: list[RescueVerdict] = []
    mismatched: list[bool] = []
    attempt = RescueAttempt(profile, base + "/otherpdf?shown", out / "article.pdf", TIMEOUT_MS,
                            STALL_MS, MAX_PDF_BYTES, title=ARTICLE_TITLE, view=view, judge_pages=False)
    attempt.finished.connect(held_verdicts.append)
    attempt.download_mismatched.connect(lambda: mismatched.append(True))
    current[:] = [attempt]
    attempt.start()
    held = run_until(app, lambda: bool(mismatched), SCENARIO_LIMIT_SECONDS)
    held_before_keep = sorted(p.name for p in out.iterdir())
    kept = attempt.keep_held_pdf().value
    results["held"] = {
        "held": held,
        "held_before_keep": held_before_keep,
        "kept": kept,
        "verdict": held_verdicts[0].to_dict() if held_verdicts else None,
        "saved_other": (out / "article.pdf").is_file()
        and (out / "article.pdf").read_bytes() == OTHER_PDF_BODY,
        "left": sorted(p.name for p in out.iterdir() if p.name != "article.pdf"),
    }
    pump(app, 0.2)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    attempt.deleteLater()
    # Neither a gone page nor an abstract ends a person's visit; leaving it does
    results["gone"] = person("gone", "/gone?shown", PERSON_WAIT_SECONDS)
    results["abstract"] = person("abstract", "/abstract?shown", PERSON_WAIT_SECONDS)

    # A new window opens in the same page (D3)
    page_probe = rescue_attempt_module._ShownPage(profile)
    results["new_window_here"] = page_probe.createWindow(
        QWebEnginePage.WebWindowType.WebBrowserTab) is page_probe
    Shiboken.delete(page_probe)

    # A walk's visit is judged as the hidden pass judges, in the view
    out = root / "shown" / "walk"
    out.mkdir(parents=True)
    verdicts: list[RescueVerdict] = []
    attempt = RescueAttempt(profile, base + "/walled?shown", out / "article.pdf", TIMEOUT_MS,
                            STALL_MS, MAX_PDF_BYTES, title=ARTICLE_TITLE, view=view, judge_pages=True)
    attempt.finished.connect(verdicts.append)
    current[:] = [attempt]
    attempt.start()
    results["walk_walled"] = {
        "finished": run_until(app, lambda: bool(verdicts), SCENARIO_LIMIT_SECONDS),
        "verdict": verdicts[0].to_dict() if verdicts else None,
    }
    attempt.deleteLater()

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    Shiboken.delete(view)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    Shiboken.delete(profile)
    return results


def service_scenarios(app: QApplication, base: str, root: Path) -> dict[str, Any]:
    """Work a queue through the real service, then quit mid-visit."""
    config = LiteConfig()
    config.storage.data_dir = root / "data"
    storage = LiteStorage(config)
    out = root / "service-pdfs"

    def request(doc: str, path: str) -> RescueRequest:
        return RescueRequest(doc, f"10.1/{doc}", None, ARTICLE_TITLE, base + path, "127.0.0.1",
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

    # Written as another process would, through a second storage: the poll finds it
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

    # A person's visit through the service: shown in a view, its PDF recorded
    view = QWebEngineView()
    view.show()
    # Its address serves a PDF, as a person's tick would clear the wall; the
    # wall is recorded here, with no event loop turn between, so the hidden
    # pass never sees it pending. The service reads the stored entry again
    # before showing it, so the stored address is the one visited.
    storage.enqueue_rescues([request("doc-g", "/pdf?g")])
    storage.record_rescue_verdict(entry_of("?g").id, RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare",)))
    person_entry = entry_of("?g")
    ended: list[Any] = []
    service.shown_visit_ended.connect(lambda e, v: ended.append(v))
    service.visit_shown(person_entry, view)
    result["shown"] = {
        "ended": run_until(app, lambda: bool(ended), SCENARIO_LIMIT_SECONDS),
        "verdict": ended[0].to_dict() if ended and ended[0] else None,
        "state": entry_of("?g").state.value,
        "rescued": "doc-g" in rescued,
    }
    service.end_shown_visit()

    # Quit mid-visit: the entry stays pending, its attempts unchanged
    storage.enqueue_rescues([request("doc-f", "/abstract?f")])
    result["visiting"] = run_until(app, lambda: "/abstract?f" in Handler.visits, SCENARIO_LIMIT_SECONDS)
    service.shutdown()
    pump(app, 0.5)
    quit_entry = entry_of("?f")
    result["quit"] = {"state": quit_entry.state.value, "attempts": quit_entry.attempts}
    Shiboken.delete(view)
    return result


def shown_quit_scenario(app: QApplication, base: str, root: Path) -> dict[str, Any]:
    """Quit while a person's visit is shown: the entry is unchanged, the page goes first.

    Its own storage and service, with the hidden pass off: a shown visit
    holds the hidden pass, so in ``service_scenarios`` it would keep the
    hidden quit scenario's entry from ever being visited.
    """
    config = LiteConfig()
    config.storage.data_dir = root / "shown-quit-data"
    config.discovery.browser_rescue_hidden = False
    storage = LiteStorage(config)
    storage.enqueue_rescues([RescueRequest("doc-h", "10.1/doc-h", None, ARTICLE_TITLE,
                                           base + "/abstract?h", "127.0.0.1", SERVICE_UNPAYWALL_PDF)])
    service = BrowserRescueService(
        storage, config, root / "shown-quit-profile",
        pdf_path_for=lambda e: root / "shown-quit.pdf",
        timeout_ms=TIMEOUT_MS, stall_ms=STALL_MS, poll_ms=POLL_MS, max_pdf_bytes=MAX_PDF_BYTES,
    )
    view = QWebEngineView()
    view.show()
    service.start()
    (entry,) = storage.rescue_entries([RescueState.PENDING])
    service.visit_shown(entry, view)
    visiting = run_until(app, lambda: "/abstract?h" in Handler.visits, SCENARIO_LIMIT_SECONDS)
    service.shutdown()
    pump(app, 0.5)
    Shiboken.delete(view)
    after = storage.rescue_entry(entry.id)
    return {"visiting": visiting, "state": after.state.value if after else None,
            "attempts": after.attempts if after else None}


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
            "shown": shown_scenarios(app, base, root),
            "service": service_scenarios(app, base, root),
            "shown_quit": shown_quit_scenario(app, base, root),
        }
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
