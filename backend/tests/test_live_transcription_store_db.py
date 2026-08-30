"""Prove Gemini Live handoffs survive process boundaries in shared Postgres."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Iterator
import os
from typing import Any, TypeVar
from uuid import uuid4

import pytest

from app.db import close_pool, connection, init_pool
from app.domain.enums import Role
from app.services.live_transcription_store import (
    bearer_hash,
    consume_live_ticket,
    consume_pending_live_transcript,
    issue_live_ticket,
    store_pending_live_transcript,
)


DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not set")
T = TypeVar("T")
_test_loop: asyncio.AbstractEventLoop | None = None


@pytest.fixture(scope="module", autouse=True)
def database_pool() -> Iterator[None]:
    global _test_loop
    assert DATABASE_URL is not None
    _test_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_test_loop)
    _test_loop.run_until_complete(init_pool(DATABASE_URL))
    yield
    _test_loop.run_until_complete(close_pool())
    _test_loop.close()
    _test_loop = None
    asyncio.set_event_loop(None)


def run(coroutine: Coroutine[Any, Any, T]) -> T:
    assert _test_loop is not None
    return _test_loop.run_until_complete(coroutine)


def test_live_state_is_shared_hashed_and_single_use() -> None:
    actor_id = uuid4()

    async def exercise() -> None:
        async with connection() as conn:
            await conn.execute(
                "insert into profiles (id, role, preferred_lang) values ($1, 'reporter', 'en')",
                actor_id,
            )
        try:
            ticket = await issue_live_ticket(
                actor_id=actor_id,
                hint_locale="en-SG",
                ttl_seconds=45,
            )
            async with connection() as conn:
                stored_ticket = await conn.fetchrow(
                    "select token_hash from live_transcription_tickets where actor_id = $1",
                    actor_id,
                )
            assert stored_ticket is not None
            assert stored_ticket["token_hash"] == bearer_hash(ticket)
            assert stored_ticket["token_hash"] != ticket

            consumed_ticket = await consume_live_ticket(ticket)
            assert consumed_ticket is not None
            assert consumed_ticket.actor_id == actor_id
            assert consumed_ticket.role == Role.REPORTER
            assert await consume_live_ticket(ticket) is None

            session_id = await store_pending_live_transcript(
                actor_id=actor_id,
                hint_locale="en-SG",
                text="Loose guardrail at level six",
                detected_locale="en",
                duration_ms=3200,
                provider_ref="provider-session",
                latency_ms=450,
            )
            async with connection() as conn:
                stored_session = await conn.fetchrow(
                    "select session_hash from live_transcription_sessions where actor_id = $1",
                    actor_id,
                )
            assert stored_session is not None
            assert stored_session["session_hash"] == bearer_hash(session_id)

            pending = await consume_pending_live_transcript(session_id, actor_id)
            assert pending is not None
            assert pending.text == "Loose guardrail at level six"
            assert await consume_pending_live_transcript(session_id, actor_id) is None
        finally:
            async with connection() as conn:
                await conn.execute("delete from profiles where id = $1", actor_id)

    run(exercise())
