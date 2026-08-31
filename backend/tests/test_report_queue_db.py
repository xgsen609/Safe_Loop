"""Measure the indexed reviewer queue against a rolled-back 5,000-row fixture."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os
from time import perf_counter
from typing import AsyncIterator
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.domain.enums import ActorType, ReportStatus, Role
from app.services import report_service
from app.services.report_service import Actor, list_reports

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not set")
REPORTER_ID = UUID("00000000-0000-0000-0000-000000000001")
REVIEWER_ID = UUID("00000000-0000-0000-0000-000000000003")


def test_reviewer_queue_is_under_one_second_with_5000_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> tuple[float, str, str, str | None]:
        assert DATABASE_URL is not None
        conn = await asyncpg.connect(DATABASE_URL)
        transaction = conn.transaction()
        await transaction.start()
        prefix = uuid4().hex[:10]
        try:
            await conn.execute(
                """
                insert into reports (
                  id, human_ref, reporter_id, status, urgency,
                  description_original, location_text, created_at, updated_at
                )
                select
                  gen_random_uuid(),
                  'PERF-' || $1 || '-' || lpad(series::text, 5, '0'),
                  $2,
                  'under_review'::report_status,
                  case series % 4
                    when 0 then 'critical'::urgency
                    when 1 then 'high'::urgency
                    when 2 then 'medium'::urgency
                    else 'low'::urgency
                  end,
                  'queue performance fixture',
                  'Tower A',
                  now() - make_interval(secs => series),
                  now() - make_interval(secs => series)
                from generate_series(1, 5000) as series
                """,
                prefix,
                REPORTER_ID,
            )

            @asynccontextmanager
            async def same_connection() -> AsyncIterator[asyncpg.Connection[asyncpg.Record]]:
                yield conn

            monkeypatch.setattr(report_service, "connection", same_connection)
            actor = Actor(ActorType.HUMAN, REVIEWER_ID, Role.REVIEWER)
            await list_reports(
                actor,
                report_status=ReportStatus.UNDER_REVIEW,
                query=f"PERF-{prefix}",
            )
            started = perf_counter()
            page = await list_reports(
                actor,
                report_status=ReportStatus.UNDER_REVIEW,
                query=f"PERF-{prefix}",
            )
            elapsed = perf_counter() - started
            assert page.next_cursor is not None
            second_page = await list_reports(
                actor,
                report_status=ReportStatus.UNDER_REVIEW,
                query=f"PERF-{prefix}",
                cursor=page.next_cursor,
            )
            index_name = await conn.fetchval(
                "select to_regclass('reports_queue_status_newest_order')::text"
            )
            return elapsed, page.rows[0]["human_ref"], second_page.rows[0]["human_ref"], index_name
        finally:
            await transaction.rollback()
            await conn.close()

    elapsed, first_human_ref, second_page_human_ref, index_name = asyncio.run(exercise())

    assert index_name == "reports_queue_status_newest_order"
    assert first_human_ref.endswith("00004")
    assert second_page_human_ref != first_human_ref
    assert elapsed < 1.0
