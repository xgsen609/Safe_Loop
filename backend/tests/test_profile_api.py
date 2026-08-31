"""Prove technician choices are named, assignable, and reviewer-scoped."""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import HTTPException
import pytest

from app.api import profiles as profiles_api
from app.domain.enums import ActorType, Role
from app.services.report_service import Actor


REVIEWER_ID = UUID("00000000-0000-0000-0000-000000000003")
REPORTER_ID = UUID("00000000-0000-0000-0000-000000000001")


def test_reviewer_can_list_named_technicians(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_list() -> list[dict[str, object]]:
        return [
            {
                "id": UUID("00000000-0000-0000-0000-000000000004"),
                "display_name": "Ah Hock",
            }
        ]

    monkeypatch.setattr(profiles_api, "list_technicians", fake_list)
    result = asyncio.run(
        profiles_api.technician_list(
            Actor(ActorType.HUMAN, REVIEWER_ID, Role.REVIEWER)
        )
    )

    assert result == {
        "items": [
            {
                "id": "00000000-0000-0000-0000-000000000004",
                "display_name": "Ah Hock",
            }
        ]
    }


def test_reporter_cannot_list_technicians() -> None:
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            profiles_api.technician_list(
                Actor(ActorType.HUMAN, REPORTER_ID, Role.REPORTER)
            )
        )

    assert error.value.status_code == 403
    assert error.value.detail["code"] == "technician_list_forbidden"
