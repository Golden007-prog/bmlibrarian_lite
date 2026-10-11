"""A provider request that failed is told in plain words.

The settings dialog's probes wait a few seconds and do not retry, so a timeout
or a busy provider is what a reader meets most. The SDKs word them for
developers: Anthropic's timeout points at its long-request docs, an overload
shows the raw error body, Ollama's timeout says only "timed out". These are
told as what they are and what to do; anything else keeps the client's own
message, which names the cause.
"""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx
import ollama
import pytest

from bmlibrarian_lite.llm.providers.anthropic import AnthropicProvider
from bmlibrarian_lite.llm.providers.base import describe_request_failure
from bmlibrarian_lite.llm.providers.ollama import OllamaProvider

_REQUEST = httpx.Request("GET", "https://api.example.test/v1/models")
_TIMEOUT_TEXT = "No answer in time. Check the server or the network, then try again."


def _anthropic_status_error(status: int) -> anthropic.APIStatusError:
    """What the Anthropic client raises for an HTTP error status."""
    response = httpx.Response(status, request=_REQUEST)
    return anthropic.InternalServerError("raw body", response=response, body=None)


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        httpx.ReadTimeout("timed out", request=_REQUEST),
        httpx.ConnectTimeout("timed out", request=_REQUEST),
        anthropic.APITimeoutError(request=_REQUEST),
    ],
    ids=["builtin", "httpx-read", "httpx-connect", "anthropic"],
)
def test_a_timeout_is_told_as_one(error: BaseException) -> None:
    """Every client's timeout reads the same."""
    assert describe_request_failure(error) == _TIMEOUT_TEXT


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (_anthropic_status_error(529), 529),
        (anthropic.RateLimitError(
            "raw", response=httpx.Response(429, request=_REQUEST), body=None
        ), 429),
        (ollama.ResponseError("server busy, please try again", 503), 503),
    ],
    ids=["anthropic-overloaded", "anthropic-rate-limit", "ollama-queue-full"],
)
def test_a_busy_provider_is_told_as_busy(error: BaseException, status: int) -> None:
    """A rate limit or an overload is a reason to wait, not a broken setup."""
    assert describe_request_failure(error) == (
        f"The provider is busy (HTTP {status}). Try again shortly."
    )


def test_control_another_error_keeps_its_own_message() -> None:
    """A status that is not "busy" keeps what the client said."""
    error = _anthropic_status_error(500)
    assert describe_request_failure(error) == str(error)
    assert describe_request_failure(ConnectionError("refused")) == "refused"


def test_an_error_without_a_message_is_named_by_its_type() -> None:
    """An empty message never leaves the reader with nothing."""
    assert describe_request_failure(ConnectionError()) == "ConnectionError"


def test_the_anthropic_connection_test_words_a_timeout_plainly() -> None:
    """Test Connection shows the plain timeout, not the SDK's text."""
    provider = AnthropicProvider(api_key="k")

    def time_out(limit: int | None = None) -> list:
        raise anthropic.APITimeoutError(request=_REQUEST)

    provider._client = SimpleNamespace(models=SimpleNamespace(list=time_out))
    assert provider.test_connection() == (False, _TIMEOUT_TEXT)


def test_the_ollama_connection_test_words_a_timeout_plainly() -> None:
    """Test Connection shows the plain timeout, after what failed."""
    provider = OllamaProvider(base_url="http://127.0.0.1:9")

    def time_out() -> None:
        raise httpx.ReadTimeout("timed out", request=_REQUEST)

    provider._client = SimpleNamespace(list=time_out)
    assert provider.test_connection() == (False, f"Connection failed: {_TIMEOUT_TEXT}")
