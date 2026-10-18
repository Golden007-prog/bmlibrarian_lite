"""Crossref retraction lookups over lite's transport.

bmlib builds the batched request URLs and reads each answer; this module owns
the wire and turns its failures into typed ``RequestFailure``s.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

import requests
from bmlib.publications.retraction_status import (
    CrossrefOutcome,
    CrossrefResult,
    crossref_retraction_requests,
    parse_crossref_retraction_response,
)
from urllib3.util.retry import Retry

from . import __version__
from .constants import CROSSREF_REQUEST_TIMEOUT_SECONDS
from .data_models import RequestFailure, RequestFailureKind
from .polite_session import mount_politely
from .search_failures import request_failure_from_exception

_HTTP_OK = 200
# The polite adapter owns pacing and retries; a second layer would double them.
_ADAPTER_OWNS_RETRIES = 0


@dataclass(frozen=True)
class CrossrefLookups:
    """What Crossref said, keyed by ``crossref_key(doi)``.

    Attributes:
        results: A result per DOI asked. A DOI not asked (unsendable, or
            after a cancel) has no entry: not asked is not unreachable.
        failures: Why a DOI is unreachable, for those the wire or a
            malformed answer failed.
    """

    results: dict[str, CrossrefResult]
    failures: dict[str, RequestFailure]


def crossref_session(mailto: str) -> requests.Session:
    """A polite session identifying this app and a contact address.

    Args:
        mailto: Contact address for Crossref's polite pool.

    Returns:
        The session.
    """
    session = mount_politely(
        requests.Session(), retry=Retry(total=_ADAPTER_OWNS_RETRIES)
    )
    session.headers["User-Agent"] = f"bmlibrarian-lite/{__version__} (mailto:{mailto})"
    return session


def lookup_crossref(
    dois: Iterable[str],
    session: requests.Session,
    *,
    mailto: str,
    should_cancel: Callable[[], bool] = lambda: False,
) -> CrossrefLookups:
    """Ask Crossref about *dois*, one batch at a time.

    Args:
        dois: The DOIs, in any form bmlib normalises.
        session: The session to send over.
        mailto: Contact address added to each request URL.
        should_cancel: Checked before each batch; once true, no more are sent.

    Returns:
        The results and the failures. Never raises for a wire failure.
    """
    results: dict[str, CrossrefResult] = {}
    failures: dict[str, RequestFailure] = {}
    for batch in crossref_retraction_requests(dois, mailto).batches:
        if should_cancel():
            break
        try:
            response = session.get(batch.url, timeout=CROSSREF_REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            failure = request_failure_from_exception(exc)
            for doi in batch.dois:
                results[doi] = CrossrefResult(
                    CrossrefOutcome.UNREACHABLE, reason=failure.kind.value
                )
                failures[doi] = failure
            continue
        parsed = parse_crossref_retraction_response(
            response.status_code, response.content, batch.dois
        )
        for doi, result in parsed.items():
            results[doi] = result
            if result.outcome is CrossrefOutcome.UNREACHABLE:
                failures[doi] = (
                    RequestFailure(RequestFailureKind.HTTP_STATUS, response.status_code)
                    if response.status_code != _HTTP_OK
                    # A 200 that bmlib calls unreachable is an unreadable body
                    # or unreadable relations: a garbled answer either way.
                    else RequestFailure(RequestFailureKind.MALFORMED_RESPONSE)
                )
    return CrossrefLookups(results, failures)
