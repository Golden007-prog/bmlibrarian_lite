"""The browser rescue queue's table (#480, stage 2)."""

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from bmlibrarian_lite import rescue_queue_store as store
from bmlibrarian_lite.browser_rescue import (
    RescueOutcome,
    RescueRequest,
    RescueState,
    RescueVerdict,
    fetchable_host,
)
from bmlibrarian_lite.config import LiteConfig
from bmlibrarian_lite.constants import (
    MAX_RESCUE_ATTEMPTS,
    RESCUE_PRIORITY_WAITING,
    RESCUE_RETRY_DELAY_SECONDS,
    SERVICE_UNPAYWALL_PDF,
)
from bmlibrarian_lite.storage import LiteStorage

T0 = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
PDF = RescueVerdict(RescueOutcome.PDF)
TRANSPORT = RescueVerdict(RescueOutcome.TRANSPORT)


def _request(document_id: str = "doc-1", address: str = "https://a.example/1.pdf") -> RescueRequest:
    host = fetchable_host(address)
    assert host is not None
    return RescueRequest(document_id, "10.1/x", None, "Title", address, host, SERVICE_UNPAYWALL_PDF)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """An in-memory queue table."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(store.SCHEMA)
    yield connection
    connection.close()


def _states(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(f"SELECT document_id, address, state FROM {store.TABLE}").fetchall()
    return {f"{r['document_id']} {r['address']}": r["state"] for r in rows}


def test_a_new_request_is_queued_pending(conn: sqlite3.Connection) -> None:
    """A new request is queued pending with no attempts."""
    assert store.enqueue(conn, [_request()], 0, T0) == 1
    entry = store.next_pending(conn, T0)
    assert entry is not None
    assert (entry.request, entry.state, entry.attempts, entry.verdict, entry.priority) == (
        _request(), RescueState.PENDING, 0, None, 0
    )


def test_enqueue_is_idempotent_and_never_reopens_a_settled_entry(conn: sqlite3.Connection) -> None:
    """Re-queueing never adds a duplicate nor reopens a settled entry."""
    store.enqueue(conn, [_request()], 0, T0)
    assert store.enqueue(conn, [_request()], 0, T0) == 0
    entry = store.next_pending(conn, T0)
    assert entry is not None
    store.record_verdict(conn, entry.id, RescueVerdict(RescueOutcome.NO_PDF, 200), T0)
    assert store.enqueue(conn, [_request()], RESCUE_PRIORITY_WAITING, T0) == 0
    assert _states(conn) == {"doc-1 https://a.example/1.pdf": "no_pdf"}
    assert conn.execute(f"SELECT COUNT(*) FROM {store.TABLE}").fetchone()[0] == 1


def test_a_higher_priority_raises_an_open_entry(conn: sqlite3.Connection) -> None:
    """A higher priority raises an open entry; a lower one never lowers it."""
    store.enqueue(conn, [_request()], 0, T0)
    assert store.enqueue(conn, [_request()], RESCUE_PRIORITY_WAITING, T0) == 1
    entry = store.next_pending(conn, T0)
    assert entry is not None and entry.priority == RESCUE_PRIORITY_WAITING
    # and a lower one never lowers it
    assert store.enqueue(conn, [_request()], 0, T0) == 0


def test_the_next_entry_is_priority_first_then_oldest(conn: sqlite3.Connection) -> None:
    """The next entry is the highest priority, then the oldest."""
    store.enqueue(conn, [_request("old")], 0, T0)
    store.enqueue(conn, [_request("new")], 0, T0 + timedelta(seconds=1))
    store.enqueue(conn, [_request("urgent")], RESCUE_PRIORITY_WAITING, T0 + timedelta(seconds=2))
    entry = store.next_pending(conn, T0 + timedelta(seconds=3))
    assert entry is not None and entry.request.document_id == "urgent"
    store.record_verdict(conn, entry.id, PDF, T0)
    entry = store.next_pending(conn, T0 + timedelta(seconds=3))
    assert entry is not None and entry.request.document_id == "old"


def test_a_transport_failure_waits_before_the_next_try(conn: sqlite3.Connection) -> None:
    """A transport failure holds the entry back for the retry delay."""
    store.enqueue(conn, [_request()], 0, T0)
    entry = store.next_pending(conn, T0)
    assert entry is not None
    recorded = store.record_verdict(conn, entry.id, TRANSPORT, T0)
    updated = recorded.entry
    assert recorded.applied and updated is not None
    assert (updated.state, updated.attempts) == (RescueState.PENDING, 1)
    due = store.stamp(T0 + timedelta(seconds=RESCUE_RETRY_DELAY_SECONDS))
    assert updated.retry_after == due
    assert store.next_pending(conn, T0 + timedelta(seconds=RESCUE_RETRY_DELAY_SECONDS - 1)) is None
    again = store.next_pending(conn, T0 + timedelta(seconds=RESCUE_RETRY_DELAY_SECONDS))
    assert again is not None and again.retry_after == due


def test_the_attempt_cap_hands_the_entry_to_a_person(conn: sqlite3.Connection) -> None:
    """Reaching the attempt cap hands the entry to a person and clears its retry time."""
    store.enqueue(conn, [_request()], 0, T0)
    entry = store.next_pending(conn, T0)
    assert entry is not None
    now = T0
    for _ in range(MAX_RESCUE_ATTEMPTS):
        updated = store.record_verdict(conn, entry.id, TRANSPORT, now).entry
        now += timedelta(seconds=RESCUE_RETRY_DELAY_SECONDS)
    assert updated is not None
    assert (updated.state, updated.attempts) == (RescueState.NEEDS_PERSON, MAX_RESCUE_ATTEMPTS)
    assert updated.retry_after is None
    assert store.next_pending(conn, now) is None
    row = conn.execute(f"SELECT retry_after FROM {store.TABLE}").fetchone()
    assert row["retry_after"] is None


def test_a_rescue_supersedes_the_documents_other_open_entries(conn: sqlite3.Connection) -> None:
    """A rescue supersedes the document's other open entries, not other documents'."""
    store.enqueue(
        conn,
        [_request("doc-1", "https://a.example/1.pdf"), _request("doc-1", "https://b.example/1.pdf"),
         _request("doc-2", "https://a.example/2.pdf")],
        0,
        T0,
    )
    entry = store.next_pending(conn, T0)
    assert entry is not None and entry.request.address == "https://a.example/1.pdf"
    store.record_verdict(conn, entry.id, PDF, T0)
    assert _states(conn) == {
        "doc-1 https://a.example/1.pdf": "rescued",
        "doc-1 https://b.example/1.pdf": "superseded",
        "doc-2 https://a.example/2.pdf": "pending",
    }


def test_a_settled_sibling_is_not_superseded(conn: sqlite3.Connection) -> None:
    """A settled sibling keeps its state when another address is rescued."""
    store.enqueue(conn, [_request("doc-1", "https://a.example/1.pdf")], 0, T0)
    store.enqueue(conn, [_request("doc-1", "https://b.example/1.pdf")], 0, T0 + timedelta(seconds=1))
    first = store.next_pending(conn, T0 + timedelta(seconds=2))
    assert first is not None
    store.record_verdict(conn, first.id, RescueVerdict(RescueOutcome.GONE, 404), T0)
    second = store.next_pending(conn, T0 + timedelta(seconds=2))
    assert second is not None
    store.record_verdict(conn, second.id, PDF, T0)
    assert _states(conn)["doc-1 https://a.example/1.pdf"] == "gone"


def test_a_verdict_for_an_entry_no_longer_open_changes_nothing(conn: sqlite3.Connection) -> None:
    """A verdict for an entry no longer open leaves it unchanged."""
    store.enqueue(conn, [_request()], 0, T0)
    entry = store.next_pending(conn, T0)
    assert entry is not None
    conn.execute(f"UPDATE {store.TABLE} SET state = 'dismissed'")
    conn.commit()
    recorded = store.record_verdict(conn, entry.id, PDF, T0)
    assert not recorded.applied
    assert recorded.entry is not None and recorded.entry.state is RescueState.DISMISSED
    assert _states(conn) == {"doc-1 https://a.example/1.pdf": "dismissed"}


def test_a_second_verdict_for_a_rescued_entry_is_not_applied(conn: sqlite3.Connection) -> None:
    """A rescue another process recorded first is shown, not applied again."""
    store.enqueue(conn, [_request()], 0, T0)
    entry = store.next_pending(conn, T0)
    assert entry is not None
    assert store.record_verdict(conn, entry.id, PDF, T0).applied
    again = store.record_verdict(conn, entry.id, PDF, T0)
    assert not again.applied
    assert again.entry is not None and again.entry.state is RescueState.RESCUED
    assert again.entry.attempts == 1


def test_a_verdict_for_a_missing_entry_is_none(conn: sqlite3.Connection) -> None:
    """A verdict for a missing entry returns no entry and is not applied."""
    assert store.record_verdict(conn, 999, PDF, T0) == store.RecordedVerdict(None, applied=False)


def test_the_sql_open_states_are_the_rules_open_states() -> None:
    """The SQL's open states follow OPEN_RESCUE_STATES, so they cannot drift."""
    from bmlibrarian_lite.browser_rescue import OPEN_RESCUE_STATES

    assert set(store._OPEN) == {state.value for state in OPEN_RESCUE_STATES}
    assert store._OPEN_MARKS.count("?") == len(OPEN_RESCUE_STATES)


def test_the_verdict_is_stored_as_json(conn: sqlite3.Connection) -> None:
    """The verdict is stored as JSON and read back equal."""
    store.enqueue(conn, [_request()], 0, T0)
    entry = store.next_pending(conn, T0)
    assert entry is not None
    verdict = RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare",))
    store.record_verdict(conn, entry.id, verdict, T0)
    row = conn.execute(f"SELECT verdict FROM {store.TABLE}").fetchone()
    assert json.loads(row["verdict"]) == verdict.to_dict()
    (stored,) = store.entries(conn, [RescueState.NEEDS_PERSON])
    assert stored.verdict == verdict


def test_a_newer_builds_state_is_left_alone(conn: sqlite3.Connection) -> None:
    """A state this build does not know is left out of reads."""
    store.enqueue(conn, [_request()], 0, T0)
    conn.execute(f"UPDATE {store.TABLE} SET state = 'teleported'")
    conn.commit()
    assert store.next_pending(conn, T0) is None
    assert store.entries(conn, list(RescueState)) == []


def test_a_damaged_verdict_reads_as_none(conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture) -> None:
    """A damaged stored verdict reads as None rather than failing, and is logged."""
    store.enqueue(conn, [_request()], 0, T0)
    conn.execute(f"UPDATE {store.TABLE} SET verdict = '{{not json'")
    conn.commit()
    entry = store.next_pending(conn, T0)
    assert entry is not None and entry.verdict is None
    assert "could not be read" in caplog.text


def test_a_newer_builds_verdict_is_logged_as_unreadable(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """Valid JSON with an outcome this build does not know is unreadable too, never silently none."""
    store.enqueue(conn, [_request()], 0, T0)
    conn.execute(f"UPDATE {store.TABLE} SET verdict = ?", (json.dumps({"outcome": "newer"}),))
    conn.commit()
    entry = store.next_pending(conn, T0)
    assert entry is not None and entry.verdict is None
    assert "could not be read" in caplog.text


def test_a_superseded_sibling_keeps_no_retry_time(conn: sqlite3.Connection) -> None:
    """Only a pending entry is held back: superseding one held back clears its retry time."""
    first, second = _queued(conn, _request("doc-1", "https://a.example/1.pdf"),
                            _request("doc-1", "https://b.example/1.pdf"))
    store.record_verdict(conn, second, TRANSPORT, T0)
    store.record_verdict(conn, first, PDF, T0)
    sibling = store.entry(conn, second)
    assert sibling is not None and sibling.state is RescueState.SUPERSEDED
    assert sibling.retry_after is None


def test_stamps_sort_as_time_does() -> None:
    """Stored timestamps sort in time order regardless of timezone."""
    assert store.stamp(T0) < store.stamp(T0 + timedelta(microseconds=1))
    assert store.stamp(T0.astimezone(timezone(timedelta(hours=10)))) == store.stamp(T0)


# -- through LiteStorage --------------------------------------------------------


@pytest.fixture
def storage(tmp_path: Path) -> LiteStorage:
    """A LiteStorage in a fresh data directory."""
    config = LiteConfig()
    config.storage.data_dir = tmp_path
    return LiteStorage(config)


def test_storage_creates_the_table_and_round_trips_an_entry(storage: LiteStorage) -> None:
    """LiteStorage creates the table and round-trips an entry through a verdict."""
    assert storage.enqueue_rescues([_request()]) == 1
    entry = storage.next_rescue_entry()
    assert entry is not None
    recorded = storage.record_rescue_verdict(entry.id, PDF)
    assert recorded.applied
    assert recorded.entry is not None and recorded.entry.state is RescueState.RESCUED
    assert [e.state for e in storage.rescue_entries([RescueState.RESCUED])] == [RescueState.RESCUED]


def test_a_second_storage_on_the_same_file_sees_the_queue(storage: LiteStorage) -> None:
    """The MCP server writes from another process; the GUI reads the same file."""
    LiteStorage(storage.config).enqueue_rescues([_request()])
    assert storage.next_rescue_entry() is not None


# -- stage 3: the window's reads and the reader's dismissal ---------------------

CHALLENGE = RescueVerdict(RescueOutcome.CHALLENGE, 403, ("cloudflare",))


def _queued(conn: sqlite3.Connection, *requests: RescueRequest) -> list[int]:
    store.enqueue(conn, list(requests), 0, T0)
    return [r["id"] for r in conn.execute(f"SELECT id FROM {store.TABLE} ORDER BY id")]


def test_a_person_s_rescue_settles_a_needs_person_entry_and_its_siblings(conn: sqlite3.Connection) -> None:
    """needs_person -> rescued, superseding the document's other open entries."""
    first, _second = _queued(conn, _request(), _request(address="https://b.example/2.pdf"))
    store.record_verdict(conn, first, CHALLENGE, T0)
    recorded = store.record_verdict(conn, first, PDF, T0)
    assert recorded.applied
    assert recorded.entry is not None and recorded.entry.state is RescueState.RESCUED
    assert recorded.entry.attempts == 2
    assert _states(conn)["doc-1 https://b.example/2.pdf"] == RescueState.SUPERSEDED.value


@pytest.mark.parametrize("before", [None, CHALLENGE])
def test_an_open_entry_is_dismissed_and_never_visited(conn: sqlite3.Connection, before: RescueVerdict | None) -> None:
    """A pending or needs_person entry is dismissed; the hidden pass never takes it."""
    (entry_id,) = _queued(conn, _request())
    if before is not None:
        store.record_verdict(conn, entry_id, before, T0)
    recorded = store.dismiss(conn, entry_id, T0)
    assert recorded.applied
    assert recorded.entry is not None and recorded.entry.state is RescueState.DISMISSED
    assert recorded.entry.retry_after is None
    assert store.next_pending(conn, T0 + timedelta(days=1)) is None


def test_a_dismissal_clears_a_pending_entry_s_retry_time(conn: sqlite3.Connection) -> None:
    """A held-back entry, dismissed, keeps no retry time in its row."""
    (entry_id,) = _queued(conn, _request())
    store.record_verdict(conn, entry_id, TRANSPORT, T0)
    store.dismiss(conn, entry_id, T0)
    row = conn.execute(f"SELECT retry_after FROM {store.TABLE}").fetchone()
    assert row["retry_after"] is None


def test_a_dismissal_keeps_the_last_verdict(conn: sqlite3.Connection) -> None:
    """The window still shows what the last visit saw."""
    (entry_id,) = _queued(conn, _request())
    store.record_verdict(conn, entry_id, CHALLENGE, T0)
    store.dismiss(conn, entry_id, T0)
    entry = store.entry(conn, entry_id)
    assert entry is not None and entry.verdict == CHALLENGE


def test_a_settled_entry_is_not_dismissed(conn: sqlite3.Connection) -> None:
    """A rescue another process recorded first stands."""
    (entry_id,) = _queued(conn, _request())
    store.record_verdict(conn, entry_id, PDF, T0)
    recorded = store.dismiss(conn, entry_id, T0)
    assert not recorded.applied
    assert recorded.entry is not None and recorded.entry.state is RescueState.RESCUED


def test_dismissing_a_missing_entry_is_none(conn: sqlite3.Connection) -> None:
    """An entry deleted meanwhile is not dismissed."""
    assert store.dismiss(conn, 999, T0) == store.RecordedVerdict(None, applied=False)


def test_a_dismissed_entry_is_not_reopened_by_a_later_run(conn: sqlite3.Connection) -> None:
    """A later analysis of the same document leaves the dismissal as it is."""
    (entry_id,) = _queued(conn, _request())
    store.dismiss(conn, entry_id, T0)
    assert store.enqueue(conn, [_request()], RESCUE_PRIORITY_WAITING, T0) == 0
    assert _states(conn) == {"doc-1 https://a.example/1.pdf": RescueState.DISMISSED.value}


def test_a_verdict_after_a_dismissal_is_not_applied(conn: sqlite3.Connection) -> None:
    """A visit that ends after the reader dismissed its entry changes nothing."""
    (entry_id,) = _queued(conn, _request())
    store.dismiss(conn, entry_id, T0)
    assert not store.record_verdict(conn, entry_id, PDF, T0).applied


def test_one_entry_is_read_by_id(conn: sqlite3.Connection) -> None:
    """An entry is read by its ID; a missing one is None."""
    (entry_id,) = _queued(conn, _request())
    entry = store.entry(conn, entry_id)
    assert entry is not None and entry.request == _request()
    assert store.entry(conn, entry_id + 1) is None


def test_the_count_is_of_the_states_asked_for(conn: sqlite3.Connection) -> None:
    """Counted by state; no states, no count."""
    first, _second = _queued(conn, _request(), _request(address="https://b.example/2.pdf"))
    store.record_verdict(conn, first, CHALLENGE, T0)
    assert store.count(conn, [RescueState.NEEDS_PERSON]) == 1
    assert store.count(conn, [RescueState.NEEDS_PERSON, RescueState.PENDING]) == 2
    assert store.count(conn, []) == 0


def test_a_document_s_entries_are_its_own_oldest_first(conn: sqlite3.Connection) -> None:
    """Only that document's entries, in the order they were queued, any state."""
    store.enqueue(conn, [_request()], 0, T0)
    store.enqueue(conn, [_request("doc-2")], 0, T0 + timedelta(seconds=1))
    store.enqueue(conn, [_request(address="https://b.example/2.pdf")], 0, T0 + timedelta(seconds=2))
    entries = store.for_document(conn, "doc-1")
    assert [e.request.address for e in entries] == ["https://a.example/1.pdf", "https://b.example/2.pdf"]


def test_storage_reads_counts_and_dismisses(storage: LiteStorage) -> None:
    """LiteStorage's four new methods, end to end on a file."""
    storage.enqueue_rescues([_request()])
    (entry,) = storage.rescue_entries_for_document("doc-1")
    assert storage.rescue_entry(entry.id) == entry
    assert storage.count_rescue_entries([RescueState.PENDING]) == 1
    assert storage.dismiss_rescue(entry.id).applied
    assert storage.count_rescue_entries([RescueState.PENDING]) == 0


@pytest.mark.parametrize(
    ("column", "value"),
    [("address", "file:///etc/passwd"), ("host", "elsewhere.example"), ("doi", "")],
)
def test_a_damaged_row_is_never_handed_to_a_browser(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture, column: str, value: str
) -> None:
    """A row whose address, host or DOI cannot be trusted is left alone, logged."""
    store.enqueue(conn, [_request()], 0, T0)
    conn.execute(f"UPDATE {store.TABLE} SET {column} = ?", (value,))
    conn.commit()
    assert store.next_pending(conn, T0) is None
    assert "cannot be read" in caplog.text
