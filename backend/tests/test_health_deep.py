"""Exercise deep dependency probes without opening a network socket."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.ai.provider import StubProvider
from app.config import get_settings
from app.health import (
    HealthCheckFailure,
    check_live_transcription,
    check_provider,
    check_storage,
    run_deep_health,
)


def test_deep_health_reports_each_component_and_overall_failure() -> None:
    async def healthy() -> None:
        return None

    async def unhealthy() -> None:
        raise HealthCheckFailure("storage_unreachable")

    result = asyncio.run(
        run_deep_health(
            checkers={
                "database": healthy,
                "storage": unhealthy,
                "provider": healthy,
            }
        )
    )

    assert result["ok"] is False
    assert result["checks"]["database"]["code"] == "ok"
    assert result["checks"]["storage"]["code"] == "storage_unreachable"
    assert result["checks"]["provider"]["ok"] is True


def test_storage_health_checks_private_buckets_without_network(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-test-key")
    get_settings.cache_clear()
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["authorization"] == "Bearer service-test-key"
        return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1]})

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await check_storage(client=client)

    try:
        asyncio.run(exercise())
    finally:
        get_settings.cache_clear()

    assert paths == [
        "/storage/v1/bucket/report-media",
        "/storage/v1/bucket/report-audio",
        "/storage/v1/bucket/documents",
    ]


def test_stub_provider_health_is_network_free() -> None:
    asyncio.run(check_provider(provider=StubProvider()))


def test_live_transcription_health_requires_enabled_valid_config(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("LIVE_TRANSCRIPTION_ENABLED", "false")
    get_settings.cache_clear()
    with pytest.raises(HealthCheckFailure) as disabled:
        asyncio.run(check_live_transcription())
    assert disabled.value.code == "live_transcription_disabled"

    monkeypatch.setenv("LIVE_TRANSCRIPTION_ENABLED", "true")
    monkeypatch.setenv("VERTEX_PROJECT_ID", "safe-loop")
    monkeypatch.setenv("VERTEX_LIVE_TRANSCRIPTION_LOCATION", "asia-southeast1")
    get_settings.cache_clear()
    with pytest.raises(HealthCheckFailure) as invalid:
        asyncio.run(check_live_transcription())
    assert invalid.value.code == "live_transcription_misconfigured"
    get_settings.cache_clear()
