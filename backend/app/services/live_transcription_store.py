"""Persist short-lived Gemini Live handoffs across backend instances."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import secrets
from uuid import UUID

from app.db import connection
from app.domain.enums import Role


@dataclass(frozen=True)
class LiveTicket:
    actor_id: UUID
    role: Role
    hint_locale: str


@dataclass(frozen=True)
class PendingLiveTranscript:
    hint_locale: str
    text: str
    detected_locale: str
    duration_ms: int
    provider_ref: str
    latency_ms: int


def bearer_hash(value: str) -> str:
    """Hash one-time bearer material before it crosses the database boundary."""
    return sha256(value.encode("utf-8")).hexdigest()


async def prune_expired_live_state() -> None:
    async with connection() as conn:
        await conn.execute(
            "delete from live_transcription_tickets where expires_at <= now()"
        )
        await conn.execute(
            "delete from live_transcription_sessions where expires_at <= now()"
        )


async def issue_live_ticket(
    *,
    actor_id: UUID,
    hint_locale: str,
    ttl_seconds: int,
) -> str:
    await prune_expired_live_state()
    token = secrets.token_urlsafe(32)
    async with connection() as conn:
        await conn.execute(
            """
            insert into live_transcription_tickets (
              token_hash, actor_id, hint_locale, expires_at
            ) values ($1, $2, $3, now() + $4::double precision * interval '1 second')
            """,
            bearer_hash(token),
            actor_id,
            hint_locale,
            ttl_seconds,
        )
    return token


async def consume_live_ticket(token: str) -> LiveTicket | None:
    async with connection() as conn:
        row = await conn.fetchrow(
            """
            delete from live_transcription_tickets as ticket
            using profiles as profile
            where ticket.token_hash = $1
              and ticket.actor_id = profile.id
              and ticket.expires_at > now()
            returning ticket.actor_id, profile.role, ticket.hint_locale
            """,
            bearer_hash(token),
        )
    if row is None:
        return None
    return LiveTicket(
        actor_id=row["actor_id"],
        role=Role(row["role"]),
        hint_locale=row["hint_locale"],
    )


async def store_pending_live_transcript(
    *,
    actor_id: UUID,
    hint_locale: str,
    text: str,
    detected_locale: str,
    duration_ms: int,
    provider_ref: str,
    latency_ms: int,
    ttl_seconds: int = 300,
) -> str:
    session_id = secrets.token_urlsafe(24)
    async with connection() as conn:
        await conn.execute(
            """
            insert into live_transcription_sessions (
              session_hash, actor_id, hint_locale, text_raw, detected_locale,
              duration_ms, provider_ref, latency_ms, expires_at
            ) values (
              $1, $2, $3, $4, $5, $6, $7, $8,
              now() + $9::double precision * interval '1 second'
            )
            """,
            bearer_hash(session_id),
            actor_id,
            hint_locale,
            text,
            detected_locale,
            duration_ms,
            provider_ref,
            latency_ms,
            ttl_seconds,
        )
    return session_id


async def consume_pending_live_transcript(
    session_id: str,
    actor_id: UUID,
) -> PendingLiveTranscript | None:
    async with connection() as conn:
        row = await conn.fetchrow(
            """
            delete from live_transcription_sessions
            where session_hash = $1
              and actor_id = $2
              and expires_at > now()
            returning hint_locale, text_raw, detected_locale, duration_ms,
                      provider_ref, latency_ms
            """,
            bearer_hash(session_id),
            actor_id,
        )
    if row is None:
        return None
    return PendingLiveTranscript(
        hint_locale=row["hint_locale"],
        text=row["text_raw"],
        detected_locale=row["detected_locale"],
        duration_ms=row["duration_ms"],
        provider_ref=row["provider_ref"],
        latency_ms=row["latency_ms"],
    )
