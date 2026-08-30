"""Probe required services through narrow seams that remain mockable offline."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import logging
from time import perf_counter
from typing import TypedDict
from urllib.parse import quote

import httpx

from app.ai.live_transcription import LiveTranscriptionConfig
from app.ai.provider import AIProvider, get_provider
from app.config import get_settings
from app.db import connection
from app.observability import track_exception

logger = logging.getLogger(__name__)


class ComponentHealth(TypedDict):
    ok: bool
    code: str
    latency_ms: float


class DeepHealth(TypedDict):
    ok: bool
    checks: dict[str, ComponentHealth]


HealthCheck = Callable[[], Awaitable[None]]


class HealthCheckFailure(RuntimeError):
    """Expose a machine code while retaining the underlying exception in logs."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def check_database() -> None:
    """Verify both connectivity and the application schema expected by requests."""
    async with connection() as conn:
        ready = await conn.fetchval(
            """
            select 1
            where to_regclass('public.reports') is not null
              and to_regclass('public.audit_log') is not null
              and to_regclass('public.live_transcription_tickets') is not null
              and to_regclass('public.live_transcription_sessions') is not null
            """
        )
    if ready != 1:
        raise HealthCheckFailure("database_schema_missing")


async def check_storage(*, client: httpx.AsyncClient | None = None) -> None:
    """Reach the regional Storage API and verify both private production buckets."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise HealthCheckFailure("storage_not_configured")
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }
    active_client = client or httpx.AsyncClient(
        timeout=settings.deep_health_timeout_seconds
    )
    try:
        for bucket in (
            settings.report_media_bucket,
            settings.report_audio_bucket,
            settings.documents_bucket,
        ):
            endpoint = (
                f"{settings.supabase_url.rstrip('/')}/storage/v1/bucket/"
                f"{quote(bucket, safe='')}"
            )
            response = await active_client.get(endpoint, headers=headers)
            if response.status_code != 200:
                raise HealthCheckFailure("storage_unreachable")
    except httpx.HTTPError as error:
        raise HealthCheckFailure("storage_unreachable") from error
    finally:
        if client is None:
            await active_client.aclose()


async def check_provider(*, provider: AIProvider | None = None) -> None:
    """Use the selected provider's cheapest regional metadata probe."""
    active_provider = provider or get_provider()
    reachable = await active_provider.health()
    if not reachable:
        raise HealthCheckFailure("provider_unreachable")


async def check_live_transcription() -> None:
    """Fail production readiness when Gemini Live is disabled or misconfigured."""
    settings = get_settings()
    if not settings.live_transcription_enabled:
        raise HealthCheckFailure("live_transcription_disabled")
    try:
        LiveTranscriptionConfig.from_settings(settings).validate()
    except ValueError as error:
        raise HealthCheckFailure("live_transcription_misconfigured") from error


async def _run_component(name: str, checker: HealthCheck) -> ComponentHealth:
    started = perf_counter()
    try:
        await asyncio.wait_for(
            checker(),
            timeout=get_settings().deep_health_timeout_seconds,
        )
    except Exception as error:
        code = error.code if isinstance(error, HealthCheckFailure) else f"{name}_unreachable"
        track_exception(logger, "deep_health_check_failed", error, component=name, code=code)
        return {
            "ok": False,
            "code": code,
            "latency_ms": round((perf_counter() - started) * 1000, 3),
        }
    return {
        "ok": True,
        "code": "ok",
        "latency_ms": round((perf_counter() - started) * 1000, 3),
    }


async def run_deep_health(
    *,
    checkers: Mapping[str, HealthCheck] | None = None,
) -> DeepHealth:
    """Run independent checks concurrently so one timeout cannot hide the others."""
    if checkers is not None:
        active_checkers = checkers
    else:
        active_checkers = {
            "database": check_database,
            "storage": check_storage,
            "provider": check_provider,
        }
        settings = get_settings()
        if settings.app_env == "production" or settings.live_transcription_enabled:
            active_checkers["live_transcription"] = check_live_transcription
    results = await asyncio.gather(
        *(_run_component(name, checker) for name, checker in active_checkers.items())
    )
    checks = dict(zip(active_checkers, results, strict=True))
    return {"ok": all(item["ok"] for item in checks.values()), "checks": checks}
