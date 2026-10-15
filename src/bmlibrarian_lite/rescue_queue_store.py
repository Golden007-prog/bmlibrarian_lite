# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2026 Dr Horst Herb
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The browser rescue queue's table (#480, stages 2 and 3).

SQL only; the rules are ``browser_rescue``'s. Every function takes an open
connection whose ``row_factory`` is ``sqlite3.Row``. ``LiteStorage`` opens
it and commits after :func:`enqueue`. :func:`record_verdict` runs its own
transaction, because the MCP server writes this table from another process.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .browser_rescue import (
    OPEN_RESCUE_STATES,
    RescueEntry,
    RescueRequest,
    RescueState,
    RescueVerdict,
    dismissed_state,
    next_state,
    stamp,
)
from .constants import RESCUE_RETRY_DELAY_SECONDS

logger = logging.getLogger(__name__)

TABLE = "browser_rescue_queue"

# No foreign key to documents: the MCP server queues under its own document
# ID, which storage may not hold (section 2 of
# docs/superpowers/specs/2026-10-12-browser-rescue-design.md).
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    doi TEXT NOT NULL,
    pmid TEXT,
    title TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL,
    host TEXT NOT NULL,
    service TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    verdict TEXT,      -- JSON, RescueVerdict.to_dict(); NULL before a visit
    priority INTEGER NOT NULL DEFAULT 0,
    retry_after TEXT,  -- NULL: may be visited now
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (document_id, address)
);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_next
    ON {TABLE}(state, priority, created_at);
"""

#: The open states' stored values, for SQL, and their placeholders.
_OPEN = tuple(sorted(state.value for state in OPEN_RESCUE_STATES))
_OPEN_MARKS = ", ".join("?" for _ in _OPEN)


@dataclass(frozen=True)
class RecordedVerdict:
    """What recording one visit's verdict, or a dismissal, did.

    Attributes:
        entry: The entry as it now stands; ``None`` when it is gone or its
            state is a newer build's.
        applied: Whether this verdict changed it; ``False`` when it was no
            longer open (dismissed or settled meanwhile, perhaps by another
            process), so a rescue it shows was announced already.
    """

    entry: RescueEntry | None
    applied: bool

    def __post_init__(self) -> None:
        """An applied change has the entry it changed.

        Raises:
            ValueError: If it was applied with no entry.
        """
        if self.applied and self.entry is None:
            raise ValueError("An applied queue change needs the entry it changed")


def entry_from_row(row: sqlite3.Row) -> RescueEntry | None:
    """Read one stored entry.

    Args:
        row: A row of the table.

    Returns:
        The entry, its verdict ``None`` when unreadable; ``None`` for a
        state this build does not know (a newer build's), or a request it
        cannot trust (damage: no web address, a host that is not the
        address's, no DOI), which is left alone (logged).
    """
    try:
        state = RescueState(row["state"])
    except ValueError:
        logger.debug("Leaving rescue entry %s alone: state %r is a newer build's", row["id"], row["state"])
        return None
    verdict = None
    if row["verdict"] is not None:
        try:
            verdict = RescueVerdict.from_dict(json.loads(row["verdict"]))
        except (TypeError, ValueError):
            verdict = None
        if verdict is None:
            # Shown as unreadable, never as "not visited yet"
            logger.warning("The last verdict of rescue entry %s could not be read", row["id"])
    try:
        request = RescueRequest(
            document_id=row["document_id"],
            doi=row["doi"],
            pmid=row["pmid"],
            title=row["title"],
            address=row["address"],
            host=row["host"],
            service=row["service"],
        )
    except (TypeError, AttributeError, ValueError) as e:
        # Never handed to a browser: a damaged row is left alone, as a newer
        # build's state is
        logger.warning("Leaving rescue entry %s alone: it cannot be read (%s)", row["id"], e)
        return None
    return RescueEntry(
        id=row["id"],
        request=request,
        state=state,
        attempts=row["attempts"],
        verdict=verdict,
        priority=row["priority"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        retry_after=row["retry_after"],
    )


def enqueue(conn: sqlite3.Connection, requests: Sequence[RescueRequest], priority: int, now: datetime) -> int:
    """Queue walled PDFs, idempotently; the caller commits.

    A new (document, address) is added ``pending``. An existing one is left
    as it is, open or settled, so a later run never reopens a settled entry.
    The one exception: a higher priority raises an open entry's.

    Args:
        conn: The connection.
        requests: What to queue.
        priority: Their priority.
        now: The moment, as ``created_at`` and ``updated_at``.

    Returns:
        How many entries were added or raised.
    """
    at = stamp(now)
    changed = 0
    for request in requests:
        cursor = conn.execute(
            f"""
            INSERT INTO {TABLE} (
                document_id, doi, pmid, title, address, host, service,
                state, priority, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (document_id, address) DO UPDATE
                SET priority = excluded.priority, updated_at = excluded.updated_at
                WHERE {TABLE}.priority < excluded.priority
                  AND {TABLE}.state IN ({_OPEN_MARKS})
            """,
            (
                request.document_id, request.doi, request.pmid, request.title,
                request.address, request.host, request.service,
                RescueState.PENDING.value, priority, at, at, *_OPEN,
            ),
        )
        changed += cursor.rowcount
    return changed


def next_pending(conn: sqlite3.Connection, now: datetime) -> RescueEntry | None:
    """The entry the hidden browser visits next.

    Args:
        conn: The connection.
        now: The moment; an entry held back past it is not due.

    Returns:
        The due ``pending`` entry with the highest priority, then the
        oldest; ``None`` when none is due.
    """
    rows = conn.execute(
        f"""
        SELECT * FROM {TABLE}
        WHERE state = ? AND (retry_after IS NULL OR retry_after <= ?)
        ORDER BY priority DESC, created_at, id
        """,
        (RescueState.PENDING.value, stamp(now)),
    )
    for row in rows:
        entry = entry_from_row(row)
        if entry is not None:
            return entry
    return None


def record_verdict(
    conn: sqlite3.Connection, entry_id: int, verdict: RescueVerdict, now: datetime
) -> RecordedVerdict:
    """Apply one visit's verdict to its entry; on a rescue, supersede its siblings.

    One immediate transaction, taken before the row is read, so a dismissal
    or rescue another process wrote in between is not overwritten. Work the
    caller left uncommitted on ``conn`` (an :func:`enqueue` with no commit)
    is committed first, since it cannot share that transaction. A transport
    failure that leaves the entry ``pending`` holds it back for
    ``RESCUE_RETRY_DELAY_SECONDS``.

    Args:
        conn: The connection.
        entry_id: The entry visited.
        verdict: What the visit concluded.
        now: The moment.

    Returns:
        The entry as it now stands, and whether the verdict was applied. An
        entry no longer open (dismissed or settled meanwhile) is returned
        unchanged and not applied; its entry is ``None`` when it is gone or
        its state is a newer build's.
    """
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(f"SELECT * FROM {TABLE} WHERE id = ?", (entry_id,)).fetchone()
        current = entry_from_row(row) if row is not None else None
        if current is None or current.state not in OPEN_RESCUE_STATES:
            conn.rollback()
            return RecordedVerdict(current, applied=False)
        attempts = current.attempts + 1
        state = next_state(current.state, verdict, attempts)
        at = stamp(now)
        retry_after = (
            stamp(now + timedelta(seconds=RESCUE_RETRY_DELAY_SECONDS))
            if state is RescueState.PENDING
            else None
        )
        conn.execute(
            f"""
            UPDATE {TABLE}
            SET state = ?, attempts = ?, verdict = ?, retry_after = ?, updated_at = ?
            WHERE id = ?
            """,
            (state.value, attempts, json.dumps(verdict.to_dict()), retry_after, at, entry_id),
        )
        if state is RescueState.RESCUED:
            conn.execute(
                f"""
                UPDATE {TABLE} SET state = ?, retry_after = NULL, updated_at = ?
                WHERE document_id = ? AND id != ? AND state IN ({_OPEN_MARKS})
                """,
                (RescueState.SUPERSEDED.value, at, current.request.document_id, entry_id, *_OPEN),
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return RecordedVerdict(
        replace(
            current, state=state, attempts=attempts, verdict=verdict, updated_at=at,
            retry_after=retry_after,
        ),
        applied=True,
    )


def entries(conn: sqlite3.Connection, states: Iterable[RescueState]) -> list[RescueEntry]:
    """The entries in the given states, as the queue window orders them.

    Args:
        conn: The connection.
        states: The states wanted.

    Returns:
        Highest priority first, then oldest; rows this build cannot read
        are left out.
    """
    values = [state.value for state in states]
    if not values:
        return []
    marks = ", ".join("?" for _ in values)
    rows = conn.execute(
        f"SELECT * FROM {TABLE} WHERE state IN ({marks}) ORDER BY priority DESC, created_at, id",
        values,
    )
    return [entry for entry in map(entry_from_row, rows) if entry is not None]


def entry(conn: sqlite3.Connection, entry_id: int) -> RescueEntry | None:
    """One entry by its ID.

    Args:
        conn: The connection.
        entry_id: The entry.

    Returns:
        The entry; ``None`` when it is gone or its state is a newer build's.
    """
    row = conn.execute(f"SELECT * FROM {TABLE} WHERE id = ?", (entry_id,)).fetchone()
    return entry_from_row(row) if row is not None else None


def dismiss(conn: sqlite3.Connection, entry_id: int, now: datetime) -> RecordedVerdict:
    """Set an open entry aside for the reader; its last verdict is kept.

    One immediate transaction, as :func:`record_verdict` takes, so a rescue
    another process recorded in between is not overwritten.

    Args:
        conn: The connection.
        entry_id: The entry.
        now: The moment.

    Returns:
        The entry as it now stands, and whether it was dismissed; an entry no
        longer open is returned unchanged and not applied.
    """
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = entry(conn, entry_id)
        if current is None or current.state not in OPEN_RESCUE_STATES:
            conn.rollback()
            return RecordedVerdict(current, applied=False)
        state = dismissed_state(current.state)
        at = stamp(now)
        conn.execute(
            f"UPDATE {TABLE} SET state = ?, retry_after = NULL, updated_at = ? WHERE id = ?",
            (state.value, at, entry_id),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return RecordedVerdict(replace(current, state=state, retry_after=None, updated_at=at), applied=True)


def count(conn: sqlite3.Connection, states: Iterable[RescueState]) -> int:
    """How many entries are in the given states.

    Args:
        conn: The connection.
        states: The states counted.

    Returns:
        The count; 0 for no states.
    """
    values = [state.value for state in states]
    if not values:
        return 0
    marks = ", ".join("?" for _ in values)
    row = conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE state IN ({marks})", values).fetchone()
    return int(row[0])


def for_document(conn: sqlite3.Connection, document_id: str) -> list[RescueEntry]:
    """A document's entries, in any state, oldest first.

    Args:
        conn: The connection.
        document_id: The document.

    Returns:
        Its entries; rows this build cannot read are left out.
    """
    rows = conn.execute(
        f"SELECT * FROM {TABLE} WHERE document_id = ? ORDER BY created_at, id", (document_id,)
    )
    return [e for e in map(entry_from_row, rows) if e is not None]
