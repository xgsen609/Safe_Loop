"""Expose role-scoped profile choices used by assignment workflows."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from app.api.deps import current_actor
from app.domain.enums import Role
from app.services.profile_service import list_technicians
from app.services.report_service import Actor


router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get("/technicians")
async def technician_list(
    actor: Actor = Depends(current_actor),
) -> dict[str, object]:
    """Return assignable technicians to reviewers and administrators only."""
    if actor.role not in {Role.REVIEWER, Role.ADMIN}:
        raise HTTPException(
            403,
            {
                "code": "technician_list_forbidden",
                "message": "technician choices require a reviewer or administrator",
            },
        )
    rows = await list_technicians()
    return cast(
        dict[str, object],
        jsonable_encoder({"items": [dict(row) for row in rows]}),
    )
