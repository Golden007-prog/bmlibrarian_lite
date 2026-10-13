"""Live acceptance: the spike's 41 walled addresses through the real hidden pass (#480, stage 2).

Reads the QtWebEngine spike's rows (DOI, group, host, result) and, from the
survey's rows, each DOI's PDF address. Queues them in a throwaway data
directory with a fresh profile, and runs the real ``BrowserRescueService``
with production timeouts and pacing until nothing is due. Writes one row per
address. PDFs go to a temporary folder, never the reader's library.

Usage:
    python scripts/browser_rescue_acceptance.py [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from bmlibrarian_lite.gui.web_engine import prepare_web_engine

prepare_web_engine()

from PySide6.QtCore import QEventLoop  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from bmlibrarian_lite.browser_rescue import RescueRequest, RescueState  # noqa: E402
from bmlibrarian_lite.config import LiteConfig  # noqa: E402
from bmlibrarian_lite.constants import SERVICE_UNPAYWALL_PDF  # noqa: E402
from bmlibrarian_lite.gui.browser_rescue_service import BrowserRescueService  # noqa: E402
from bmlibrarian_lite.storage import LiteStorage  # noqa: E402

SURVEY = Path("doc/developer/unpaywall_pdf_survey")
SPIKE_ROWS = SURVEY / "spikes/2026-10-04-qtwebengine-hidden.jsonl"
SURVEY_ROWS = (SURVEY / "2026-10-04-dev.jsonl", SURVEY / "2026-10-04-heldout.jsonl")
DEFAULT_OUT = SURVEY / "spikes/2026-10-13-browser-rescue-hidden.jsonl"
#: The longest the whole run may take: 41 visits of at most ~50 s each.
RUN_LIMIT_SECONDS = 45 * 60
#: How long the queue must stay idle before the run is over.
IDLE_SECONDS = 5
#: Milliseconds of each event-loop slice while waiting.
LOOP_SLICE_MS = 100


def addresses() -> dict[str, str]:
    """Each surveyed DOI's PDF address.

    Returns:
        A mapping from DOI to the PDF address the survey recorded.
    """
    found: dict[str, str] = {}
    for path in SURVEY_ROWS:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row.get("doi") and row.get("pdf_url"):
                found[row["doi"]] = row["pdf_url"]
    return found


def main() -> int:
    """Run the 41 addresses and write the rows.

    Returns:
        The process exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    spike = [json.loads(line) for line in SPIKE_ROWS.read_text().splitlines()]
    pdf_urls = addresses()
    app = QApplication(sys.argv)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = LiteConfig()
        config.storage.data_dir = root / "data"
        storage = LiteStorage(config)
        for index, row in enumerate(spike):
            address = pdf_urls[row["doi"]]
            storage.enqueue_rescues([RescueRequest(
                f"spike-{index}", row["doi"], None, row.get("title", ""), address,
                urlparse(address).hostname or "", SERVICE_UNPAYWALL_PDF,
            )])
        service = BrowserRescueService(storage, config, root / "profile",
                                       pdf_path_for=lambda e: root / "pdfs" / f"{e.id}.pdf")
        started = time.monotonic()
        service.start()
        idle_since = None
        while time.monotonic() - started < RUN_LIMIT_SECONDS:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, LOOP_SLICE_MS)
            idle = not service.busy and storage.next_rescue_entry() is None
            idle_since = (idle_since or time.monotonic()) if idle else None
            if idle_since and time.monotonic() - idle_since > IDLE_SECONDS:
                break
        service.shutdown()
        entries = {e.request.document_id: e for e in storage.rescue_entries(list(RescueState))}
        rows = []
        for index, row in enumerate(spike):
            entry = entries[f"spike-{index}"]
            rows.append({
                "doi": row["doi"], "group": row["group"], "host": row["host"],
                "spike_result": row["result"], "state": entry.state.value,
                "attempts": entry.attempts,
                "verdict": entry.verdict.to_dict() if entry.verdict else None,
            })
    args.out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    for group in ("pmc", "sciencedirect", "wall", "control"):
        counts = Counter(r["state"] for r in rows if r["group"] == group)
        print(f"{group:14} {dict(counts)}")
    print(f"minutes: {(time.monotonic() - started) / 60:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
