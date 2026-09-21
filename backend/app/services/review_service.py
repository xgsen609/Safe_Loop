"""Bind reviewer decisions, assignments, and state transitions into one commit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from uuid import UUID

import asyncpg

from app.db import connection
from app.domain.enums import (
    ActorType,
    CaseRole,
    ReportStatus,
    ReviewDecision,
    Role,
    Urgency,
)
from app.services.notification_service import NotificationEntity, send_notification
from app.services.report_service import Actor, transition_report


class ReviewError(Exception):
    """Carry a stable review API code without user-facing prose."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ReviewResult:
    """Return the committed decision and resulting report state."""

    review: asyncpg.Record
    report: asyncpg.Record
    assignment_id: UUID | None
    corrective_action_id: UUID | None


_DECISION_TARGETS = {
    ReviewDecision.APPROVE: ReportStatus.ACTION_ASSIGNED,
    ReviewDecision.REQUEST_INFO: ReportStatus.INFO_REQUESTED,
    ReviewDecision.ESCALATE: ReportStatus.ESCALATED,
    ReviewDecision.REJECT: ReportStatus.REJECTED,
}


def _clean_optional(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        raise ReviewError("review_correction_invalid", f"{field} correction is blank")
    return cleaned


async def review_report(
    report_id: UUID,
    actor: Actor,
    *,
    decision: ReviewDecision,
    target: ReportStatus,
    reason: str | None = None,
    corrected_category: str | None = None,
    corrected_urgency: Urgency | None = None,
    corrected_action: str | None = None,
    correction_reason: str | None = None,
    assignee_id: UUID | None = None,
    case_role: CaseRole = CaseRole.RESPONSIBLE,
    due_at: datetime | None = None,
) -> ReviewResult:
    """Commit review evidence and its state-machine edge as one unit."""
    expected_target = _DECISION_TARGETS[decision]
    if target is not expected_target:
        raise ReviewError("review_target_mismatch", "review decision does not match target")
    if (
        actor.actor_type is not ActorType.HUMAN
        or actor.profile_id is None
        or actor.role is not Role.REVIEWER
    ):
        raise ReviewError("review_actor_not_permitted", "review requires a human profile")
    if target is ReportStatus.ACTION_ASSIGNED and (
        assignee_id is None or due_at is None
    ):
        raise ReviewError(
            "assignment_required",
            "approval requires an assignee, due date, and action",
        )

    category = _clean_optional(corrected_category, "category")
    action = _clean_optional(corrected_action, "action")
    clean_reason = reason.strip() if reason is not None else None
    clean_correction_reason = correction_reason.strip() if correction_reason is not None else None

    async with connection() as conn:
        try:
            async with conn.transaction():
                report = await conn.fetchrow(
                    "select reporter_id from reports where id = $1",
                    report_id,
                )
                if report is None:
                    raise ReviewError("report_not_found", "report does not exist")

                draft = await conn.fetchrow(
                    """
                    select proposed_category, proposed_urgency::text, suggested_action
                    from ai_drafts
                    where report_id = $1
                    order by version desc
                    limit 1
                    """,
                    report_id,
                )
                before_category = draft["proposed_category"] if draft is not None else None
                before_urgency = draft["proposed_urgency"] if draft is not None else None
                before_action = draft["suggested_action"] if draft is not None else None

                corrections: dict[str, dict[str, object | None]] = {}
                if category is not None and category != before_category:
                    corrections["category"] = {"before": before_category, "after": category}
                urgency_value = corrected_urgency.value if corrected_urgency is not None else None
                if urgency_value is not None and urgency_value != before_urgency:
                    corrections["urgency"] = {"before": before_urgency, "after": urgency_value}
                if action is not None and action != before_action:
                    corrections["action"] = {"before": before_action, "after": action}

                # The queue is ordered and filtered by reports.urgency. Once a
                # reviewer commits a decision, make its accepted urgency the
                # report's durable urgency instead of leaving the submission
                # default visible downstream.
                accepted_urgency = urgency_value or before_urgency
                if accepted_urgency is not None:
                    await conn.execute(
                        """
                        update reports
                        set urgency = $2::urgency
                        where id = $1
                        """,
                        report_id,
                        accepted_urgency,
                    )

                review = await conn.fetchrow(
                    """
                    insert into review_decisions (
                      report_id, reviewer_id, decision, corrections,
                      correction_reason, reason
                    )
                    values ($1, $2, $3::review_decision, $4::jsonb, $5, $6)
                    returning *
                    """,
                    report_id,
                    actor.profile_id,
                    decision.value,
                    json.dumps(corrections) if corrections else None,
                    clean_correction_reason if corrections else None,
                    clean_reason,
                )
                if review is None:
                    raise RuntimeError("database did not return review decision")

                assignment_id: UUID | None = None
                corrective_action_id: UUID | None = None
                if target is ReportStatus.ACTION_ASSIGNED:
                    action_text = action or before_action
                    if assignee_id is None or due_at is None or not action_text:
                        raise ReviewError(
                            "assignment_required",
                            "approval requires an assignee, due date, and action",
                        )
                    if due_at.tzinfo is None or due_at.utcoffset() is None:
                        raise ReviewError("due_at_invalid", "assignment due date needs a timezone")
                    assignee_role = await conn.fetchval(
                        "select role::text from profiles where id = $1",
                        assignee_id,
                    )
                    if assignee_role != "responsible":
                        raise ReviewError(
                            "assignee_not_responsible",
                            "assignment target is not a responsible profile",
                        )
                    assignment_id = await conn.fetchval(
                        """
                        insert into report_assignments (
                          report_id, assignee_id, case_role, due_at
                        )
                        values ($1, $2, $3::case_role, $4)
                        returning id
                        """,
                        report_id,
                        assignee_id,
                        case_role.value,
                        due_at,
                    )
                    corrective_action_id = await conn.fetchval(
                        """
                        insert into corrective_actions (
                          report_id, assignment_id, action_text, due_at
                        )
                        values ($1, $2, $3, $4)
                        returning id
                        """,
                        report_id,
                        assignment_id,
                        action_text,
                        due_at,
                    )
                    if assignment_id is None or corrective_action_id is None:
                        raise RuntimeError("database did not return assignment records")
                    await send_notification(
                        assignee_id,
                        "assigned",
                        NotificationEntity("report", report_id),
                        {
                            "report_id": report_id,
                            "assignment_id": assignment_id,
                            "corrective_action_id": corrective_action_id,
                        },
                        transaction_connection=conn,
                    )

                if target is ReportStatus.INFO_REQUESTED:
                    await send_notification(
                        report["reporter_id"],
                        "info_requested",
                        NotificationEntity("report", report_id),
                        {"report_id": report_id, "review_id": review["id"]},
                        transaction_connection=conn,
                    )

                transitioned = await transition_report(
                    report_id,
                    target,
                    actor,
                    reason=clean_reason,
                    metadata={"review_decision_id": str(review["id"])},
                    transaction_connection=conn,
                )
                return ReviewResult(
                    review,
                    transitioned,
                    assignment_id,
                    corrective_action_id,
                )
        except asyncpg.CheckViolationError as error:
            raise ReviewError(
                "correction_reason_required",
                "review correction requires a non-empty reason",
            ) from error
        except asyncpg.UniqueViolationError as error:
            raise ReviewError(
                "active_assignment_exists",
                "report already has an active responsible assignment",
            ) from error
