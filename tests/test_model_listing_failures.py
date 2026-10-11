# BMLibrarian Lite - Biomedical Literature Research Tool
# Copyright (C) 2024-2025 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""A model list that cannot be fetched is reported, never disguised.

Both desktop providers used to swallow a failed model-list request: Ollama
answered an unreachable server with an empty list - indistinguishable from a
server with nothing pulled - and Anthropic answered any failure with its
hardcoded pricing table, so a bad key or an offline machine still showed a
plausible, stale picker. Anthropic models missing from that table were also
charged at a flat Sonnet rate, understating the Fable tier.
"""

from types import SimpleNamespace

import pytest

from bmlibrarian_lite.constants import (
    ANTHROPIC_MODELS_PAGE_LIMIT,
    anthropic_model_pricing,
    get_model_pricing,
)
from bmlibrarian_lite.llm.client import LLMClient
from bmlibrarian_lite.llm.providers.anthropic import AnthropicProvider
from bmlibrarian_lite.llm.providers.ollama import OllamaProvider

# Port 9 (discard) is closed on any normal machine: a connection there is
# refused at once, which is what an Ollama server that is not running looks like.
UNREACHABLE_OLLAMA_HOST = "http://127.0.0.1:9"


class _FailingModels:
    """Stands in for anthropic.Anthropic().models when the API is unreachable."""

    def list(self, limit: int | None = None) -> list:
        raise ConnectionError("API unreachable")


class _ListedModels:
    """Stands in for anthropic.Anthropic().models answering with a line-up."""

    def __init__(self, ids: list[str]) -> None:
        self._ids = ids
        self.limits: list[int | None] = []

    def list(self, limit: int | None = None) -> list:
        self.limits.append(limit)
        return [SimpleNamespace(id=i, display_name=i) for i in self._ids]


def _anthropic_with_models(models: object) -> AnthropicProvider:
    provider = AnthropicProvider(api_key="test-key")
    provider._client = SimpleNamespace(models=models)
    return provider


class _EmptyOllamaClient:
    """An Ollama server that answers and has no models pulled."""

    def list(self) -> SimpleNamespace:
        return SimpleNamespace(models=[])


class _CountingOllamaClient:
    """An Ollama server that counts its list and ``show`` requests.

    ``show`` answers rather than raising: the provider swallows a failed
    ``show``, so a raise would go unnoticed.
    """

    def __init__(self, names: list[str]) -> None:
        self._names = names
        self.list_calls = 0
        self.show_calls = 0

    def list(self) -> SimpleNamespace:
        self.list_calls += 1
        return SimpleNamespace(models=[SimpleNamespace(model=n) for n in self._names])

    def show(self, name: str) -> dict:
        self.show_calls += 1
        return {}


class TestOllamaModelList:
    """Ollama model listing."""

    def test_unreachable_server_raises(self) -> None:
        """A server that is not running is an error, not an empty list."""
        provider = OllamaProvider(base_url=UNREACHABLE_OLLAMA_HOST)
        with pytest.raises(ConnectionError):
            provider.list_models()

    def test_reachable_server_with_no_models_is_empty(self) -> None:
        """A server with nothing pulled still answers with an empty list."""
        provider = OllamaProvider(base_url=UNREACHABLE_OLLAMA_HOST)
        provider._client = _EmptyOllamaClient()
        assert provider.list_models() == []

    def test_model_ids_are_one_request(self) -> None:
        """The IDs come from the list alone, not a ``show`` per model."""
        provider = OllamaProvider(base_url=UNREACHABLE_OLLAMA_HOST)
        client = _CountingOllamaClient(["llama3.2", "", "qwen3"])
        provider._client = client
        assert provider.list_model_ids() == ["llama3.2", "qwen3"]
        assert (client.list_calls, client.show_calls) == (1, 0)

    def test_control_full_metadata_asks_show_per_model(self) -> None:
        """Control: ``list_models`` does ask ``show`` once per model."""
        provider = OllamaProvider(base_url=UNREACHABLE_OLLAMA_HOST)
        client = _CountingOllamaClient(["llama3.2", "qwen3"])
        provider._client = client
        assert [m.model_id for m in provider.list_models()] == ["llama3.2", "qwen3"]
        assert (client.list_calls, client.show_calls) == (1, 2)

    def test_model_ids_of_an_unreachable_server_raise(self) -> None:
        """A server that is not running is an error, not an empty list."""
        provider = OllamaProvider(base_url=UNREACHABLE_OLLAMA_HOST)
        with pytest.raises(ConnectionError):
            provider.list_model_ids()


class TestAnthropicModelList:
    """Anthropic model listing."""

    def test_failed_fetch_raises_instead_of_returning_hardcoded_list(self) -> None:
        """A failed fetch is an error, not the hardcoded table."""
        provider = _anthropic_with_models(_FailingModels())
        with pytest.raises(ConnectionError):
            provider.list_models()

    def test_live_list_is_returned_and_priced(self) -> None:
        """A successful fetch returns exactly what the API listed."""
        provider = _anthropic_with_models(_ListedModels(["claude-sonnet-5", "claude-fable-5-1"]))
        models = provider.list_models()
        assert [m.model_id for m in models] == ["claude-sonnet-5", "claude-fable-5-1"]
        assert (models[1].pricing.input_cost, models[1].pricing.output_cost) == (10.00, 50.00)

    def test_the_list_and_the_connection_test_ask_for_one_page(self) -> None:
        """The whole list comes in one request, not a page of 20 at a time."""
        listed = _ListedModels(["claude-sonnet-5"])
        provider = _anthropic_with_models(listed)
        provider.list_models()
        assert provider.test_connection() == (True, "Connected. 1 models available.")
        assert provider.list_model_ids() == ["claude-sonnet-5"]
        assert listed.limits == [ANTHROPIC_MODELS_PAGE_LIMIT, ANTHROPIC_MODELS_PAGE_LIMIT]


class TestLLMClientModelList:
    """The client-level listing a named provider goes through."""

    def test_named_provider_failure_propagates(self) -> None:
        """The client no longer turns a provider failure into []."""
        client = LLMClient(default_provider="ollama", ollama_host=UNREACHABLE_OLLAMA_HOST)
        with pytest.raises(ConnectionError):
            client.list_models("ollama")


class TestAnthropicPricing:
    """Anthropic rates shared by the provider and the benchmarks."""

    @pytest.mark.parametrize(
        ("model_id", "expected"),
        [
            ("claude-fable-5-1", (10.00, 50.00)),
            ("claude-opus-5-5", (4.00, 20.00)),
            ("claude-opus-5", (5.00, 25.00)),
            ("claude-sonnet-5", (2.00, 10.00)),
            ("claude-haiku-4-5-20251001", (1.00, 5.00)),
            # Newer Opus 4.x must not be captured by the retired Opus 4 rate.
            ("claude-opus-4-8", (5.00, 25.00)),
            ("claude-opus-4-20250514", (15.00, 75.00)),
            ("claude-3-haiku-20240307", (0.25, 1.25)),
        ],
    )
    def test_listed_models(self, model_id: str, expected: tuple[float, float]) -> None:
        """Each tabled model resolves to its own rate."""
        assert anthropic_model_pricing(model_id) == expected

    def test_unlisted_model_gets_its_family_ceiling(self) -> None:
        """A model newer than the table is not quoted below its family."""
        assert anthropic_model_pricing("claude-sonnet-6") == (3.00, 15.00)
        assert anthropic_model_pricing("claude-lyric-1") == (10.00, 50.00)

    def test_provider_charges_the_same_rates(self) -> None:
        """Recorded cost uses the shared rates."""
        pricing = AnthropicProvider(api_key="test-key").get_model_pricing("claude-fable-5-1")
        assert (pricing.input_cost, pricing.output_cost) == (10.00, 50.00)

    def test_benchmark_pricing_uses_the_same_rates(self) -> None:
        """Benchmark estimates use the shared rates."""
        assert get_model_pricing("anthropic:claude-sonnet-5") == {"input": 2.00, "output": 10.00}

    def test_benchmark_pricing_for_ollama_is_unchanged(self) -> None:
        """Non-Anthropic lookups still go through the flat table."""
        assert get_model_pricing("ollama:llama3.2") == {"input": 0.0, "output": 0.0}
