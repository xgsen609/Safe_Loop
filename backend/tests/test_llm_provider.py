"""Exercise the real provider's safety boundary without opening a socket."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import cast

from google.genai import types
from pydantic import BaseModel
import pytest

from app.ai.llm_provider import (
    MAX_RETRIES,
    REQUEST_TIMEOUT_SECONDS,
    CircuitOpenError,
    LLMProvider,
    LLMProviderConfig,
    ProviderUnavailableError,
    _VertexClient,
    _make_client,
)
from app.ai import llm_provider as llm_module
from app.ai.provider import EMBEDDING_DIMENSIONS, ProviderConfigurationError
from app.config import DEFAULT_VERTEX_LOCATION


class CompletionSchema(BaseModel):
    observation: str
    confidence: float


@dataclass
class FakeUsage:
    prompt_token_count: int | None = 120
    candidates_token_count: int | None = 30
    thoughts_token_count: int | None = 5


@dataclass
class FakeGenerateResponse:
    text: str | None
    response_id: str | None = "vertex-response-1"
    model_version: str | None = "gemini-test"
    usage_metadata: FakeUsage | None = None


@dataclass
class FakeEmbeddingStatistics:
    token_count: float | None = 12.0


@dataclass
class FakeEmbedding:
    values: list[float] | None
    statistics: FakeEmbeddingStatistics | None = None


@dataclass
class FakeEmbedResponse:
    embeddings: list[FakeEmbedding] | None


class FakeModels:
    def __init__(
        self,
        *,
        generate_results: list[FakeGenerateResponse | Exception] | None = None,
        embed_results: list[FakeEmbedResponse | Exception] | None = None,
    ) -> None:
        self.generate_results = generate_results or []
        self.embed_results = embed_results or []
        self.generate_calls: list[tuple[str, str, object]] = []
        self.embed_calls: list[tuple[str, str, object]] = []
        self.get_calls: list[str] = []

    @staticmethod
    def _next[T](values: list[T], index: int) -> T:
        if not values:
            raise AssertionError("fake provider response is missing")
        return values[min(index, len(values) - 1)]

    async def generate_content(
        self,
        *,
        model: str,
        contents: str,
        config: object,
    ) -> FakeGenerateResponse:
        index = len(self.generate_calls)
        self.generate_calls.append((model, contents, config))
        result = self._next(self.generate_results, index)
        if isinstance(result, Exception):
            raise result
        return result

    async def embed_content(
        self,
        *,
        model: str,
        contents: str,
        config: object,
    ) -> FakeEmbedResponse:
        index = len(self.embed_calls)
        self.embed_calls.append((model, contents, config))
        result = self._next(self.embed_results, index)
        if isinstance(result, Exception):
            raise result
        return result

    async def get(self, *, model: str) -> object:
        self.get_calls.append(model)
        return {"name": model}


@dataclass
class FakeAsyncClient:
    models: FakeModels


@dataclass
class FakeClient:
    aio: FakeAsyncClient


def config(**overrides: object) -> LLMProviderConfig:
    values: dict[str, object] = {
        "project_id": "safeloop-test",
        "location": DEFAULT_VERTEX_LOCATION,
        "model": "gemini-test",
        "embedding_model": "gemini-embedding-001",
        "max_output_tokens": 4096,
        "input_cost_per_million_usd": 1.0,
        "output_cost_per_million_usd": 2.0,
        "embedding_cost_per_million_usd": 0.2,
        "circuit_failure_threshold": 3,
        "circuit_reset_seconds": 60.0,
    }
    values.update(overrides)
    return LLMProviderConfig(**values)  # type: ignore[arg-type]


def provider_with(models: FakeModels, **config_overrides: object) -> LLMProvider:
    client = cast(_VertexClient, FakeClient(FakeAsyncClient(models)))
    return LLMProvider(config(**config_overrides), client=client)


def complete(provider: LLMProvider):
    return asyncio.run(
        provider.complete(
            "provider_fixture",
            {"report": "Loose guardrail", "locale": "en"},
            schema=CompletionSchema,
        )
    )


def test_structured_completion_is_revalidated_and_logs_tokens_and_cost(
    caplog: pytest.LogCaptureFixture,
) -> None:
    models = FakeModels(
        generate_results=[
            FakeGenerateResponse(
                '{"observation":"Loose guardrail","confidence":0.9}',
                usage_metadata=FakeUsage(),
            )
        ]
    )
    provider = provider_with(models)

    with caplog.at_level(logging.INFO):
        result = complete(provider)

    assert result.data == {"observation": "Loose guardrail", "confidence": 0.9}
    assert result.tokens_in == 120
    assert result.tokens_out == 35
    assert result.cost_usd == pytest.approx(0.00019)
    assert result.provider == "vertex-gemini"
    assert result.provider_ref == "vertex-response-1"
    assert len(models.generate_calls) == 1
    model, prompt, request_config = models.generate_calls[0]
    assert model == "gemini-test"
    assert "Loose guardrail" in prompt
    assert isinstance(request_config, types.GenerateContentConfig)
    assert request_config.temperature == 0.0
    assert request_config.candidate_count == 1
    assert isinstance(request_config.seed, int)
    assert request_config.response_mime_type == "application/json"
    assert request_config.response_schema is CompletionSchema
    record = next(
        record for record in caplog.records if record.message == "ai_provider_call"
    )
    assert record.tokens_in == 120
    assert record.tokens_out == 35
    assert record.estimated_cost_usd == 0.00019


def test_identical_completion_inputs_use_the_same_seed() -> None:
    models = FakeModels(
        generate_results=[
            FakeGenerateResponse(
                '{"observation":"Loose guardrail","confidence":0.9}'
            )
        ]
    )
    provider = provider_with(models)

    complete(provider)
    complete(provider)

    first_config = models.generate_calls[0][2]
    second_config = models.generate_calls[1][2]
    assert isinstance(first_config, types.GenerateContentConfig)
    assert isinstance(second_config, types.GenerateContentConfig)
    assert first_config.seed == second_config.seed


def test_health_uses_model_metadata_on_the_configured_regional_client() -> None:
    models = FakeModels()
    provider = provider_with(models)

    assert asyncio.run(provider.health()) is True
    assert models.get_calls == ["gemini-test"]


def test_real_provider_is_available_through_the_provider_seam() -> None:
    from app.ai.provider import LLMProvider as ExportedProvider

    assert ExportedProvider is LLMProvider


def test_sdk_client_is_bound_to_vertex_singapore_with_one_sdk_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    fake_client = FakeClient(FakeAsyncClient(FakeModels()))

    def capture_client(**kwargs: object) -> FakeClient:
        captured.update(kwargs)
        return fake_client

    monkeypatch.setattr(llm_module.genai, "Client", capture_client)

    result = _make_client(config())

    assert result is fake_client
    assert captured["vertexai"] is True
    assert captured["project"] == "safeloop-test"
    assert captured["location"] == DEFAULT_VERTEX_LOCATION
    http_options = captured["http_options"]
    assert isinstance(http_options, types.HttpOptions)
    assert http_options.api_version == "v1"
    assert http_options.timeout == 30_000
    assert http_options.retry_options is not None
    assert http_options.retry_options.attempts == 1


def test_two_retries_use_jitter_before_a_valid_response() -> None:
    models = FakeModels(
        generate_results=[
            RuntimeError("temporary one"),
            RuntimeError("temporary two"),
            FakeGenerateResponse(
                '{"observation":"Loose guardrail","confidence":0.9}'
            ),
        ]
    )
    delays: list[float] = []

    async def capture_sleep(delay: float) -> None:
        delays.append(delay)

    client = cast(_VertexClient, FakeClient(FakeAsyncClient(models)))
    provider = LLMProvider(
        config(),
        client=client,
        sleep=capture_sleep,
        random_source=lambda: 0.5,
    )

    result = complete(provider)

    assert result.data["confidence"] == 0.9
    assert len(models.generate_calls) == MAX_RETRIES + 1
    assert delays == [0.25, 0.5]


def test_invalid_structured_output_is_retried_then_fails_closed() -> None:
    models = FakeModels(generate_results=[FakeGenerateResponse('{"wrong":true}')])
    provider = provider_with(models)

    with pytest.raises(ProviderUnavailableError):
        complete(provider)

    assert len(models.generate_calls) == MAX_RETRIES + 1


def test_circuit_opens_after_the_configured_failure_threshold() -> None:
    models = FakeModels(generate_results=[RuntimeError("provider down")])
    client = cast(_VertexClient, FakeClient(FakeAsyncClient(models)))
    provider = LLMProvider(
        config(circuit_failure_threshold=1),
        client=client,
        sleep=_no_sleep,
        clock=lambda: 0.0,
    )

    with pytest.raises(ProviderUnavailableError):
        complete(provider)
    calls_after_failure = len(models.generate_calls)
    with pytest.raises(CircuitOpenError):
        complete(provider)

    assert calls_after_failure == MAX_RETRIES + 1
    assert len(models.generate_calls) == calls_after_failure


async def _no_sleep(_delay: float) -> None:
    return None


def test_embedding_model_is_called_once_per_text_at_pgvector_dimensions() -> None:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    models = FakeModels(
        embed_results=[
            FakeEmbedResponse(
                [FakeEmbedding(vector, FakeEmbeddingStatistics())]
            )
        ]
    )
    provider = provider_with(models)

    result = asyncio.run(provider.embed(["one", "two"]))

    assert result == [vector, vector]
    assert [call[1] for call in models.embed_calls] == ["one", "two"]
    assert all(
        isinstance(call[2], types.EmbedContentConfig)
        and call[2].output_dimensionality == EMBEDDING_DIMENSIONS
        and call[2].auto_truncate is False
        for call in models.embed_calls
    )


def test_non_singapore_and_global_locations_are_rejected_before_a_call() -> None:
    models = FakeModels()
    client = cast(_VertexClient, FakeClient(FakeAsyncClient(models)))

    for location in ("global", "us-central1"):
        with pytest.raises(ProviderConfigurationError):
            LLMProvider(config(location=location), client=client)

    assert config().endpoint == (
        "https://asia-southeast1-aiplatform.googleapis.com"
    )
    assert REQUEST_TIMEOUT_SECONDS == 30.0
