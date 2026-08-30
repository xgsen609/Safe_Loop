"""Prove report queue scope, cursors, and thumbnail batching without a database."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator
from uuid import UUID

from fastapi import HTTPException
import pytest

from app.api import reports as reports_api
from app.domain.enums import ActorType, ReportStatus, Role, Urgency
from app.services import report_service
from app.services.report_service import Actor, ReportListError, ReportPage, list_reports

REPORTER_ID = UUID("00000000-0000-0000-0000-000000000001")
REVIEWER_ID = UUID("00000000-0000-0000-0000-000000000003")
RESPONSIBLE_ID = UUID("00000000-0000-0000-0000-000000000004")
REPORT_IDS = [UUID(f"10000000-0000-0000-0000-{index:012d}") for index in range(1, 4)]


class FakeConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.query = ""
        self.arguments: tuple[object, ...] = ()
        self.count_query = ""

    async def fetch(self, query: str, *arguments: object) -> list[dict[str, object]]:
        self.query = query
        self.arguments = arguments
        return self.rows

    async def fetchrow(self, query: str, *arguments: object) -> dict[str, object]:
        self.count_query = query
        return {"overdue_count": 2, "rework_count": 3}


def queue_row(index: int, urgency_rank: int = 4) -> dict[str, object]:
    created_at = datetime(2026, 8, 22, tzinfo=timezone.utc) + timedelta(minutes=index)
    return {
        "id": REPORT_IDS[index],
        "human_ref": f"SL-2026-{index + 1:05d}",
        "status": "under_review",
        "urgency": "critical",
        "summary": f"Hazard {index}",
        "location_text": "Tower A",
        "created_at": created_at,
        "_urgency_rank": urgency_rank,
        "thumbnail_storage_path": None,
        "thumbnail_caption": None,
        "rework_count": 0,
        "sent_back_unresolved": False,
    }


def use_fake_connection(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, object]],
) -> FakeConnection:
    fake = FakeConnection(rows)

    @asynccontextmanager
    async def fake_connection() -> AsyncIterator[FakeConnection]:
        yield fake

    monkeypatch.setattr(report_service, "connection", fake_connection)
    return fake


def test_queue_uses_urgency_and_newest_first_keyset_not_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = use_fake_connection(monkeypatch, [queue_row(0), queue_row(1), queue_row(2)])
    actor = Actor(ActorType.HUMAN, REVIEWER_ID, Role.REVIEWER)

    first_page = asyncio.run(
        list_reports(actor, report_status=ReportStatus.UNDER_REVIEW, limit=2)
    )

    assert len(first_page.rows) == 2
    assert first_page.next_cursor is not None
    assert (
        "order by _urgency_rank desc, r.created_at desc, r.id desc"
        in fake.query.lower()
    )
    assert "offset" not in fake.query.lower()
    assert "coalesce(action.rework_count, 0) >= 2 as rework_attention" in fake.query.lower()
    assert "as overdue_count" in fake.count_query.lower()
    assert first_page.overdue_count == 2
    assert first_page.rework_count == 3

    second_fake = use_fake_connection(monkeypatch, [queue_row(2)])
    asyncio.run(
        list_reports(
            actor,
            report_status=ReportStatus.UNDER_REVIEW,
            cursor=first_page.next_cursor,
            limit=2,
        )
    )
    assert "(r.created_at, r.id) <" in second_fake.query
    assert REPORT_IDS[1] in second_fake.arguments


@pytest.mark.parametrize(
    ("role", "profile_id", "scope_fragment"),
    [
        (Role.REPORTER, REPORTER_ID, "r.reporter_id ="),
        (Role.RESPONSIBLE, RESPONSIBLE_ID, "role_assignment.assignee_id ="),
    ],
)
def test_non_reviewer_queue_queries_are_role_scoped(
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
    profile_id: UUID,
    scope_fragment: str,
) -> None:
    fake = use_fake_connection(monkeypatch, [])
    asyncio.run(list_reports(Actor(ActorType.HUMAN, profile_id, role)))

    assert scope_fragment in fake.query
    assert profile_id in fake.arguments


def test_reviewer_filters_include_status_urgency_assignee_and_literal_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = use_fake_connection(monkeypatch, [])
    asyncio.run(
        list_reports(
            Actor(ActorType.HUMAN, REVIEWER_ID, Role.REVIEWER),
            report_status=ReportStatus.UNDER_REVIEW,
            urgency=Urgency.CRITICAL,
            assignee_id=RESPONSIBLE_ID,
            query="100%_safe",
        )
    )

    assert "r.reporter_id =" not in fake.query
    assert "r.status =" in fake.query
    assert "r.urgency =" in fake.query
    assert "filtered_assignment.assignee_id =" in fake.query
    assert "%100\\%\\_safe%" in fake.arguments


def test_assignee_me_resolves_to_the_responsible_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_list(*_: object, **values: object) -> ReportPage:
        captured.update(values)
        return ReportPage([], None)

    async def fake_sign(_: list[str]) -> tuple[dict[str, str], datetime]:
        return {}, datetime(2026, 8, 22, tzinfo=timezone.utc)

    monkeypatch.setattr(reports_api, "list_reports", fake_list)
    monkeypatch.setattr(reports_api, "get_signed_media_urls", fake_sign)
    actor = Actor(ActorType.HUMAN, RESPONSIBLE_ID, Role.RESPONSIBLE)

    asyncio.run(
        reports_api.report_list(
            report_status=None,
            urgency=None,
            assignee_id="me",
            needs_manual_triage=False,
            q=None,
            cursor=None,
            limit=25,
            actor=actor,
        )
    )

    assert captured["assignee_id"] == RESPONSIBLE_ID


def test_assignee_me_is_rejected_for_a_non_responsible_role() -> None:
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            reports_api.report_list(
                report_status=None,
                urgency=None,
                assignee_id="me",
                needs_manual_triage=False,
                q=None,
                cursor=None,
                limit=25,
                actor=Actor(ActorType.HUMAN, REVIEWER_ID, Role.REVIEWER),
            )
        )

    assert error.value.status_code == 403
    assert error.value.detail["code"] == "assignee_me_forbidden"


def test_manual_triage_filter_uses_only_the_latest_invalid_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = use_fake_connection(monkeypatch, [])

    asyncio.run(
        list_reports(
            Actor(ActorType.HUMAN, REVIEWER_ID, Role.REVIEWER),
            needs_manual_triage=True,
        )
    )

    assert "r.status = 'ai_drafted'::report_status" in fake.query
    assert "manual_triage_draft.validation = 'invalid'::validation_status" in fake.query
    assert "max(latest_draft.version)" in fake.query


def test_manual_triage_filter_is_reviewer_scoped() -> None:
    with pytest.raises(ReportListError) as error:
        asyncio.run(
            list_reports(
                Actor(ActorType.HUMAN, REPORTER_ID, Role.REPORTER),
                needs_manual_triage=True,
            )
        )
    assert error.value.code == "report_list_forbidden"


def test_crew_and_machine_actors_cannot_list_reports() -> None:
    actors = [
        Actor(ActorType.HUMAN, REPORTER_ID, Role.CREW),
        Actor.system(),
        Actor.ai(),
    ]
    for actor in actors:
        with pytest.raises(ReportListError) as error:
            asyncio.run(list_reports(actor))
        assert error.value.code == "report_list_forbidden"


@pytest.mark.parametrize("cursor", ["not-base64!", "_w"])
def test_invalid_cursors_return_a_machine_error(cursor: str) -> None:
    with pytest.raises(ReportListError) as error:
        asyncio.run(
            list_reports(
                Actor(ActorType.HUMAN, REVIEWER_ID, Role.REVIEWER),
                cursor=cursor,
            )
        )
    assert error.value.code == "invalid_cursor"


def test_report_list_endpoint_batch_signs_one_thumbnail_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [queue_row(0), queue_row(1)]
    rows[0]["thumbnail_storage_path"] = "reporter/report/photo-a.jpg"
    rows[1]["thumbnail_storage_path"] = "reporter/report/photo-b.jpg"
    captured_paths: list[str] = []

    async def fake_list(*_: object, **__: object) -> ReportPage:
        return ReportPage(rows, "next-page", 4, 5)  # type: ignore[arg-type]

    async def fake_sign(paths: list[str]) -> tuple[dict[str, str], datetime]:
        captured_paths.extend(paths)
        return (
            {path: f"https://storage.example/{path}" for path in paths},
            datetime(2026, 8, 22, 1, 10, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(reports_api, "list_reports", fake_list)
    monkeypatch.setattr(reports_api, "get_signed_media_urls", fake_sign)
    result = asyncio.run(
        reports_api.report_list(
            report_status=ReportStatus.UNDER_REVIEW,
            urgency=None,
            assignee_id=None,
            q=None,
            cursor=None,
            limit=25,
            actor=Actor(ActorType.HUMAN, REVIEWER_ID, Role.REVIEWER),
        )
    )

    assert captured_paths == [
        "reporter/report/photo-a.jpg",
        "reporter/report/photo-b.jpg",
    ]
    items = result["items"]
    assert isinstance(items, list)
    assert items[0]["thumbnail_url"].endswith("photo-a.jpg")
    assert "thumbnail_storage_path" not in items[0]
    assert "_urgency_rank" not in items[0]
    assert result["next_cursor"] == "next-page"
    assert result["counts"] == {"overdue": 4, "rework": 5}


def test_responsible_queue_batch_signs_previous_action_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = queue_row(0)
    row.update(
        {
            "action_id": UUID("50000000-0000-0000-0000-000000000001"),
            "action_text": "Secure the guardrail.",
            "action_status": "assigned",
            "action_due_at": datetime(2026, 8, 25, tzinfo=timezone.utc),
            "completed_note": "Tightened the upper anchor.",
            "action_submitted_at": datetime(2026, 8, 22, tzinfo=timezone.utc),
            "deficiency_reason": "The lower anchor still moves.",
            "deficiency_notes": None,
            "deficiency_created_at": datetime(2026, 8, 22, 1, tzinfo=timezone.utc),
            "deficiency_reviewer_name": "SO Lim",
            "previous_evidence": [
                {
                    "id": "media-id",
                    "storage_path": "responsible/report/proof.jpg",
                    "caption": None,
                    "created_at": "2026-08-22T00:00:00Z",
                }
            ],
        }
    )
    captured_paths: list[str] = []

    async def fake_list(*_: object, **__: object) -> ReportPage:
        return ReportPage([row], None)  # type: ignore[list-item]

    async def fake_sign(paths: list[str]) -> tuple[dict[str, str], datetime]:
        captured_paths.extend(paths)
        return (
            {path: f"https://storage.example/{path}" for path in paths},
            datetime(2026, 8, 22, 1, 10, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(reports_api, "list_reports", fake_list)
    monkeypatch.setattr(reports_api, "get_signed_media_urls", fake_sign)
    result = asyncio.run(
        reports_api.report_list(
            report_status=ReportStatus.ACTION_ASSIGNED,
            urgency=None,
            assignee_id="me",
            q=None,
            cursor=None,
            limit=25,
            actor=Actor(ActorType.HUMAN, RESPONSIBLE_ID, Role.RESPONSIBLE),
        )
    )

    assert captured_paths == ["responsible/report/proof.jpg"]
    items = result["items"]
    assert isinstance(items, list)
    evidence = items[0]["previous_evidence"]
    assert evidence[0]["signed_url"].endswith("proof.jpg")
    assert "storage_path" not in evidence[0]
