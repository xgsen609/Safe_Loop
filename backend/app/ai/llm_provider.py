"""Call Vertex AI only through a Singapore-bound, fail-closed provider."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hashlib import sha256
import json
import logging
import random
import time
from typing import Final, Protocol, TypeVar, cast

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from app.ai.prompts import render_prompt
from app.ai.provider import (
    EMBEDDING_DIMENSIONS,
    JsonValue,
    ProviderConfigurationError,
    ProviderResult,
    Vector,
    _json_value,
)
from app.ai.usage import record_ai_usage
from app.config import DEFAULT_VERTEX_LOCATION, Settings

logger = logging.getLogger(__name__)

PROVIDER_NAME: Final = "vertex-gemini"
REQUEST_TIMEOUT_SECONDS: Final = 30.0
MAX_RETRIES: Final = 2
RETRY_BASE_SECONDS: Final = 0.25
MAX_DETERMINISTIC_SEED: Final = 2**31 - 1

_ResultT = TypeVar("_ResultT")
_Sleep = Callable[[float], Awaitable[None]]
_Clock = Callable[[], float]
_Random = Callable[[], float]


class ProviderUnavailableError(RuntimeError):
    """Stop the graph when a real provider cannot return trustworthy output."""


class ProviderResponseError(ValueError):
    """Treat malformed model output as an unavailable provider, never as a draft."""


class CircuitOpenError(ProviderUnavailableError):
    """Reject calls while an unhealthy provider is cooling down."""


def _completion_seed(
    prompt_name: str,
    rendered_prompt: str,
    schema: type[BaseModel],
) -> int:
    """Derive a stable Vertex seed from the complete structured request."""
    canonical = json.dumps(
        {
            "prompt_name": prompt_name,
            "prompt": rendered_prompt,
            "schema": schema.model_json_schema(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return int.from_bytes(sha256(canonical.encode()).digest()[:4], "big") % (
        MAX_DETERMINISTIC_SEED + 1
    )


class _Usage(Protocol):
    prompt_token_count: int | None
    candidates_token_count: int | None
    thoughts_token_count: int | None


class _GenerateResponse(Protocol):
    @property
    def text(self) -> str | None: ...

    response_id: str | None
    model_version: str | None
    usage_metadata: _Usage | None


class _EmbeddingStatistics(Protocol):
    token_count: float | None


class _Embedding(Protocol):
    values: list[float] | None
    statistics: _EmbeddingStatistics | None


class _EmbedResponse(Protocol):
    embeddings: list[_Embedding] | None


class _AsyncModels(Protocol):
    async def generate_content(
        self,
        *,
        model: str,
        contents: str,
        config: object,
    ) -> _GenerateResponse: ...

    async def embed_content(
        self,
        *,
        model: str,
        contents: str,
        config: object,
    ) -> _EmbedResponse: ...

    async def get(self, *, model: str) -> object: ...


class _AsyncClient(Protocol):
    models: _AsyncModels


class _VertexClient(Protocol):
    aio: _AsyncClient


@dataclass(frozen=True)
class LLMProviderConfig:
    """Make endpoint, model, resilience, and cost assumptions explicit and hashable."""

    project_id: str
    location: str
    model: str
    embedding_model: str
    max_output_tokens: int
    input_cost_per_million_usd: float
    output_cost_per_million_usd: float
    embedding_cost_per_million_usd: float
    circuit_failure_threshold: int
    circuit_reset_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> LLMProviderConfig:
        return cls(
            project_id=settings.vertex_project_id.strip(),
            location=settings.vertex_location.strip(),
            model=settings.vertex_model.strip(),
            embedding_model=settings.vertex_embedding_model.strip(),
            max_output_tokens=settings.vertex_max_output_tokens,
            input_cost_per_million_usd=(
                settings.vertex_input_cost_per_million_usd
            ),
            output_cost_per_million_usd=(
                settings.vertex_output_cost_per_million_usd
            ),
            embedding_cost_per_million_usd=(
                settings.vertex_embedding_cost_per_million_usd
            ),
            circuit_failure_threshold=settings.ai_circuit_failure_threshold,
            circuit_reset_seconds=settings.ai_circuit_reset_seconds,
        )

    @property
    def endpoint(self) -> str:
        """Expose the host used by the SDK so residency can be audited."""
        return f"https://{self.location}-aiplatform.googleapis.com"

    def validate(self) -> None:
        if not self.project_id:
            raise ProviderConfigurationError("VERTEX_PROJECT_ID is required")
        if self.location != DEFAULT_VERTEX_LOCATION:
            raise ProviderConfigurationError(
                "VERTEX_LOCATION must be the configured Singapore region"
            )
        if not self.model or not self.embedding_model:
            raise ProviderConfigurationError("Vertex model names are required")


class _CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int,
        reset_seconds: float,
        clock: _Clock,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._reset_seconds = reset_seconds
        self._clock = clock
        self._failures = 0
        self._open_until = 0.0
        self._probe_in_flight = False
        self._lock = asyncio.Lock()

    async def before_call(self) -> None:
        async with self._lock:
            now = self._clock()
            if self._open_until == 0.0:
                return
            if now < self._open_until:
                raise CircuitOpenError("Vertex provider circuit is open")
            if self._probe_in_flight:
                raise CircuitOpenError("Vertex provider recovery probe is in progress")
            self._probe_in_flight = True

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._open_until = 0.0
            self._probe_in_flight = False

    async def record_failure(self) -> None:
        async with self._lock:
            self._probe_in_flight = False
            self._failures += 1
            if self._failures >= self._failure_threshold:
                self._open_until = self._clock() + self._reset_seconds


def _make_client(config: LLMProviderConfig) -> _VertexClient:
    client = genai.Client(
        vertexai=True,
        project=config.project_id,
        location=config.location,
        http_options=types.HttpOptions(
            api_version="v1",
            timeout=int(REQUEST_TIMEOUT_SECONDS * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    return cast(_VertexClient, client)


def _token_count(value: int | None) -> int:
    return max(0, value or 0)


def _cost_usd(tokens: int, rate_per_million: float) -> float:
    return round(tokens * rate_per_million / 1_000_000, 8)


class LLMProvider:
    """Validate every Vertex response before it can enter graph state."""

    provider_name: Final = PROVIDER_NAME

    def __init__(
        self,
        config: LLMProviderConfig,
        *,
        client: _VertexClient | None = None,
        sleep: _Sleep = asyncio.sleep,
        clock: _Clock = time.monotonic,
        random_source: _Random = random.random,
    ) -> None:
        config.validate()
        self.config = config
        self._client = client or _make_client(config)
        self._sleep = sleep
        self._clock = clock
        self._random = random_source
        self._circuit = _CircuitBreaker(
            failure_threshold=config.circuit_failure_threshold,
            reset_seconds=config.circuit_reset_seconds,
            clock=clock,
        )

    async def _request(
        self,
        operation_name: str,
        operation: Callable[[], Awaitable[_ResultT]],
    ) -> _ResultT:
        await self._circuit.before_call()
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                    result = await operation()
                await self._circuit.record_success()
                return result
            except asyncio.CancelledError:
                raise
            except Exception as error:
                last_error = error
                if attempt == MAX_RETRIES:
                    break
                delay = RETRY_BASE_SECONDS * (2**attempt) * (
                    0.5 + self._random()
                )
                logger.warning(
                    "ai_provider_retry",
                    extra={
                        "provider": self.provider_name,
                        "operation": operation_name,
                        "attempt": attempt + 1,
                        "delay_seconds": round(delay, 4),
                        "error_type": type(error).__name__,
                    },
                )
                await self._sleep(delay)
        await self._circuit.record_failure()
        logger.error(
            "ai_provider_unavailable",
            extra={
                "provider": self.provider_name,
                "operation": operation_name,
                "attempts": MAX_RETRIES + 1,
                "error_type": type(last_error).__name__,
            },
        )
        raise ProviderUnavailableError("Vertex provider call failed") from last_error

    async def complete(
        self,
        prompt_name: str,
        variables: dict[str, object],
        *,
        schema: type[BaseModel],
    ) -> ProviderResult:
        rendered_prompt = render_prompt(prompt_name, variables)
        deterministic_seed = _completion_seed(
            prompt_name,
            rendered_prompt,
            schema,
        )
        started = self._clock()

        async def generate() -> tuple[
            _GenerateResponse,
            str,
            dict[str, JsonValue],
        ]:
            response = await self._client.aio.models.generate_content(
                model=self.config.model,
                contents=rendered_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    candidate_count=1,
                    seed=deterministic_seed,
                    max_output_tokens=self.config.max_output_tokens,
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )
            raw = response.text
            if not isinstance(raw, str) or not raw.strip():
                raise ProviderResponseError("Vertex returned no structured output")
            try:
                validated = schema.model_validate_json(raw)
            except (ValidationError, ValueError) as error:
                raise ProviderResponseError(
                    "Vertex returned invalid structured output"
                ) from error
            serialised = _json_value(validated.model_dump(mode="json"))
            if not isinstance(serialised, dict):
                raise ProviderResponseError(
                    "completion schema must serialise to an object"
                )
            return response, raw, cast(dict[str, JsonValue], serialised)

        response, raw, serialised = await self._request(
            f"complete:{prompt_name}",
            generate,
        )

        usage = response.usage_metadata
        tokens_in = _token_count(
            usage.prompt_token_count if usage is not None else None
        )
        tokens_out = _token_count(
            (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
            if usage is not None
            else None
        )
        latency_ms = max(0, round((self._clock() - started) * 1000))
        provider_ref = (
            response.response_id
            or response.model_version
            or f"sha256:{sha256(raw.encode()).hexdigest()[:24]}"
        )
        estimated_cost = _cost_usd(
            tokens_in,
            self.config.input_cost_per_million_usd,
        ) + _cost_usd(
            tokens_out,
            self.config.output_cost_per_million_usd,
        )
        logger.info(
            "ai_provider_call",
            extra={
                "provider": self.provider_name,
                "operation": "complete",
                "prompt_name": prompt_name,
                "model": self.config.model,
                "region": self.config.location,
                "provider_ref": provider_ref,
                "latency_ms": latency_ms,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "estimated_cost_usd": round(estimated_cost, 8),
            },
        )
        result = ProviderResult(
            data=serialised,
            raw=raw,
            provider=self.provider_name,
            provider_ref=provider_ref,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=round(estimated_cost, 8),
        )
        record_ai_usage(
            provider=result.provider,
            provider_ref=result.provider_ref,
            operation=f"complete:{prompt_name}",
            latency_ms=result.latency_ms,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd,
        )
        return result

    async def embed(self, texts: list[str]) -> list[Vector]:
        vectors: list[Vector] = []
        for text in texts:
            started = self._clock()

            async def generate_embedding() -> tuple[
                Vector,
                _EmbeddingStatistics | None,
            ]:
                response = await self._client.aio.models.embed_content(
                    model=self.config.embedding_model,
                    contents=text,
                    config=types.EmbedContentConfig(
                        output_dimensionality=EMBEDDING_DIMENSIONS,
                        auto_truncate=False,
                    ),
                )
                embeddings = response.embeddings
                if embeddings is None or len(embeddings) != 1:
                    raise ProviderResponseError("Vertex returned no embedding")
                values = embeddings[0].values
                if values is None or len(values) != EMBEDDING_DIMENSIONS:
                    raise ProviderResponseError(
                        "Vertex returned an embedding with the wrong dimensions"
                    )
                return (
                    [float(value) for value in values],
                    embeddings[0].statistics,
                )

            vector, statistics = await self._request(
                "embed",
                generate_embedding,
            )
            tokens_in = round(statistics.token_count or 0) if statistics else 0
            latency_ms = max(0, round((self._clock() - started) * 1000))
            logger.info(
                "ai_provider_call",
                extra={
                    "provider": self.provider_name,
                    "operation": "embed",
                    "model": self.config.embedding_model,
                    "region": self.config.location,
                    "latency_ms": latency_ms,
                    "tokens_in": tokens_in,
                    "tokens_out": 0,
                    "estimated_cost_usd": _cost_usd(
                        tokens_in,
                        self.config.embedding_cost_per_million_usd,
                    ),
                },
            )
            record_ai_usage(
                provider=self.provider_name,
                provider_ref=self.config.embedding_model,
                operation="embed",
                latency_ms=latency_ms,
                tokens_in=tokens_in,
                tokens_out=0,
                cost_usd=_cost_usd(
                    tokens_in,
                    self.config.embedding_cost_per_million_usd,
                ),
            )
            vectors.append(vector)
        return vectors

    async def health(self) -> bool:
        """Probe model metadata on the same regional client used for completions."""
        await self._client.aio.models.get(model=self.config.model)
        return True
