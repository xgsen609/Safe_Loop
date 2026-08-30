"""Expose the thin HTTP surface for report creation, reads, and transitions."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.api.deps import current_actor
from app.api.rate_limits import enforce_rate_limit
from app.config import get_settings
from app.domain.enums import (
    CaseRole,
    MediaPhase,
    ReportStatus,
    ReviewDecision,
    Role,
    Urgency,
)
from app.domain.transitions import TransitionError, allowed_targets, find
from app.observability import current_request_id
from app.services.action_service import ActionError, submit_action
from app.services.content_localization_service import (
    localize_report_items_zh,
    localize_report_zh,
)
from app.services.media_service import (
    MediaError,
    assert_report_readable,
    get_signed_media_urls,
    get_signed_report_media,
    register_report_media,
)
from app.services.intake_service import (
    ClarificationError,
    answer_clarification,
    list_report_clarifications,
    run_intake,
)
from app.services.lesson_service import (
    get_lesson_run_status,
    queue_lesson_run,
    run_lesson,
)
from app.services.report_service import (
    Actor,
    ReportDraftError,
    ReportListError,
    ReportSubmissionError,
    create_report,
    get_report,
    get_timeline,
    list_reports,
    submit_report,
    transition_report,
    update_draft_report,
)
from app.services.review_service import ReviewError, review_report
from app.services.verification_service import (
    VerificationError,
    verify_report,
)

router = APIRouter(prefix="/reports", tags=["reports"])


class CreateReportRequest(BaseModel):
    """Fields needed to create a draft observation."""

    description_original: str = Field(default="", max_length=4000)
    lang_original: str = "en"
    urgency: str = "medium"
    location_text: str | None = None
    activity: str | None = None
    level_or_zone: str | None = None
    grid_ref: str | None = None
    is_confidential: bool = False


class TransitionRequest(BaseModel):
    """Requested state-machine edge and its optional audit context."""

    target: ReportStatus
    reason: str | None = None
    metadata: dict[str, Any] | None = None
    confirmed_text: str | None = Field(default=None, max_length=4000)
    transcript_id: UUID | None = None
    audio_media_id: UUID | None = None


class RegisterMediaRequest(BaseModel):
    """Describe an object already uploaded to private Supabase Storage."""

    storage_path: str = Field(min_length=1, max_length=1024)
    mime_type: str = Field(min_length=1, max_length=100)
    phase: MediaPhase
    caption: str | None = Field(default=None, max_length=500)


class ReviewRequest(BaseModel):
    """Capture one reviewer decision and its optional correction diff."""

    decision: ReviewDecision
    target: ReportStatus
    reason: str | None = None
    corrected_category: str | None = Field(default=None, max_length=200)
    corrected_urgency: Urgency | None = None
    corrected_action: str | None = Field(default=None, max_length=4000)
    correction_reason: str | None = Field(default=None, max_length=2000)
    assignee_id: UUID | None = None
    due_at: datetime | None = None


class AssignmentRequest(BaseModel):
    """Approve the current action while naming its responsible owner."""

    assignee_id: UUID | None = None
    case_role: CaseRole
    due_at: datetime | None = None


class ActionSubmitRequest(BaseModel):
    """Reference proof already registered through the private media endpoint."""

    completed_note: str | None = Field(default=None, max_length=4000)
    media_ids: list[UUID] = Field(default_factory=list, max_length=10)
    transcript_id: UUID | None = None


class VerifyRequest(BaseModel):
    """Record one human inspection and any deadline for the next rework cycle."""

    # Deliberately typed-only: reviewer decisions, reasons, and verification notes
    # must remain conscious human acts. Do not add voice transcription here.

    passed: bool
    checklist: dict[str, object] | list[object] | None = None
    notes: str = Field(default="", max_length=4000)
    reason: str | None = Field(default=None, max_length=2000)
    new_due_at: datetime | None = None


class ClarificationAnswerRequest(BaseModel):
    """Carry reporter-supplied text for one pending clarification."""

    answer: str = Field(max_length=4000)
    transcript_id: UUID | None = None


_REVIEW_DECISION_BY_EVENT = {
    "approve_action": ReviewDecision.APPROVE,
    "approve_after_escalation": ReviewDecision.APPROVE,
    "request_info": ReviewDecision.REQUEST_INFO,
    "escalate": ReviewDecision.ESCALATE,
    "reject": ReviewDecision.REJECT,
    "reject_after_escalation": ReviewDecision.REJECT,
}


def transition_error(error: TransitionError) -> HTTPException:
    """Map machine error codes to the HTTP contract without user-facing prose."""
    code_status = {
        "illegal_transition": status.HTTP_409_CONFLICT,
        "terminal_state": status.HTTP_409_CONFLICT,
        "role_not_permitted": status.HTTP_403_FORBIDDEN,
        "actor_not_permitted": status.HTTP_403_FORBIDDEN,
        "reason_required": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "assignment_required": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "unknown_event": status.HTTP_400_BAD_REQUEST,
        "database_guard": status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    return HTTPException(
        code_status.get(error.code, status.HTTP_500_INTERNAL_SERVER_ERROR),
        {"code": error.code, "message": error.message},
    )


def submission_error(error: ReportSubmissionError) -> HTTPException:
    """Map confirmation failures without exposing transcript text."""
    code_status = {
        "report_not_found": status.HTTP_404_NOT_FOUND,
        "submission_actor_forbidden": status.HTTP_403_FORBIDDEN,
        "submission_forbidden": status.HTTP_403_FORBIDDEN,
        "report_not_submittable": status.HTTP_409_CONFLICT,
        "confirmed_text_required": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "transcript_not_found": status.HTTP_422_UNPROCESSABLE_ENTITY,
    }
    return HTTPException(
        code_status.get(error.code, status.HTTP_422_UNPROCESSABLE_ENTITY),
        {"code": error.code, "message": error.message},
    )


def review_error(error: ReviewError) -> HTTPException:
    """Map atomic-review failures to localisable machine codes."""
    code_status = {
        "report_not_found": status.HTTP_404_NOT_FOUND,
        "review_actor_not_permitted": status.HTTP_403_FORBIDDEN,
        "review_target_mismatch": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "review_correction_invalid": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "correction_reason_required": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "assignment_required": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "due_at_invalid": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "assignee_not_responsible": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "active_assignment_exists": status.HTTP_409_CONFLICT,
    }
    return HTTPException(
        code_status.get(error.code, status.HTTP_500_INTERNAL_SERVER_ERROR),
        {"code": error.code, "message": error.message},
    )


def media_error(error: MediaError) -> HTTPException:
    """Map media failures to stable machine-readable HTTP contracts."""
    code_status = {
        "report_not_found": status.HTTP_404_NOT_FOUND,
        "report_forbidden": status.HTTP_403_FORBIDDEN,
        "media_actor_not_permitted": status.HTTP_403_FORBIDDEN,
        "media_phase_not_permitted": status.HTTP_403_FORBIDDEN,
        "media_path_invalid": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "media_type_not_allowed": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        "media_type_mismatch": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "media_too_large": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        "media_object_not_found": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "media_object_invalid": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "media_already_registered": status.HTTP_409_CONFLICT,
        "storage_not_configured": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "storage_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
        "storage_sign_failed": status.HTTP_502_BAD_GATEWAY,
    }
    return HTTPException(
        code_status.get(error.code, status.HTTP_500_INTERNAL_SERVER_ERROR),
        {"code": error.code, "message": error.message},
    )


def action_error(error: ActionError) -> HTTPException:
    """Map corrective-action failures to localisable machine contracts."""
    code_status = {
        "action_actor_forbidden": status.HTTP_403_FORBIDDEN,
        "action_forbidden": status.HTTP_403_FORBIDDEN,
        "action_not_found": status.HTTP_404_NOT_FOUND,
        "action_not_submittable": status.HTTP_409_CONFLICT,
        "action_evidence_required": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "action_media_invalid": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "action_transcript_not_found": status.HTTP_422_UNPROCESSABLE_ENTITY,
    }
    return HTTPException(
        code_status.get(error.code, status.HTTP_500_INTERNAL_SERVER_ERROR),
        {"code": error.code, "message": error.message},
    )


def verification_error(error: VerificationError) -> HTTPException:
    """Map atomic-verification failures to localisable machine contracts."""
    code_status = {
        "verification_actor_forbidden": status.HTTP_403_FORBIDDEN,
        "verification_not_found": status.HTTP_404_NOT_FOUND,
        "verification_action_not_found": status.HTTP_404_NOT_FOUND,
        "verification_not_ready": status.HTTP_409_CONFLICT,
        "verification_assignment_changed": status.HTTP_409_CONFLICT,
        "verification_notes_required": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "verification_reason_required": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "verification_reason_too_vague": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "verification_due_at_required": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "verification_due_at_invalid": status.HTTP_422_UNPROCESSABLE_ENTITY,
    }
    return HTTPException(
        code_status.get(error.code, status.HTTP_500_INTERNAL_SERVER_ERROR),
        {"code": error.code, "message": error.message},
    )


def report_list_error(error: ReportListError) -> HTTPException:
    """Map list-query failures to stable API error contracts."""
    code_status = {
        "report_list_forbidden": status.HTTP_403_FORBIDDEN,
        "invalid_cursor": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "invalid_page_size": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "invalid_assignee": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "assignee_me_forbidden": status.HTTP_403_FORBIDDEN,
    }
    return HTTPException(
        code_status.get(error.code, status.HTTP_500_INTERNAL_SERVER_ERROR),
        {"code": error.code, "message": error.message},
    )


def _resolve_assignee_filter(
    assignee: str | UUID | None,
    actor: Actor,
) -> UUID | None:
    if assignee is None or isinstance(assignee, UUID):
        return assignee
    if assignee == "me":
        if actor.role is not Role.RESPONSIBLE or actor.profile_id is None:
            raise ReportListError(
                "assignee_me_forbidden",
                "assignee me filter requires a responsible profile",
            )
        return actor.profile_id
    try:
        return UUID(assignee)
    except ValueError as error:
        raise ReportListError(
            "invalid_assignee",
            "assignee filter must be a profile id or me",
        ) from error


def report_draft_error(error: ReportDraftError) -> HTTPException:
    """Map reporter-owned draft finalisation failures to stable codes."""
    code_status = {
        "report_not_found": status.HTTP_404_NOT_FOUND,
        "report_forbidden": status.HTTP_403_FORBIDDEN,
        "draft_update_forbidden": status.HTTP_409_CONFLICT,
    }
    return HTTPException(
        code_status.get(error.code, status.HTTP_500_INTERNAL_SERVER_ERROR),
        {"code": error.code, "message": error.message},
    )


def clarification_error(error: ClarificationError) -> HTTPException:
    """Map clarification failures to localisable machine codes."""
    code_status = {
        "report_not_found": status.HTTP_404_NOT_FOUND,
        "clarification_not_found": status.HTTP_404_NOT_FOUND,
        "clarification_actor_forbidden": status.HTTP_403_FORBIDDEN,
        "clarification_forbidden": status.HTTP_403_FORBIDDEN,
        "clarification_answer_required": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "report_not_clarifying": status.HTTP_409_CONFLICT,
        "clarification_already_answered": status.HTTP_409_CONFLICT,
        "clarification_round_invalid": status.HTTP_409_CONFLICT,
        "clarification_transcript_not_found": status.HTTP_422_UNPROCESSABLE_ENTITY,
    }
    return HTTPException(
        code_status.get(error.code, status.HTTP_500_INTERNAL_SERVER_ERROR),
        {"code": error.code, "message": error.message},
    )


@router.get("")
async def report_list(
    report_status: ReportStatus | None = Query(default=None, alias="status"),
    urgency: Urgency | None = Query(default=None),
    assignee_id: str | UUID | None = Query(default=None, alias="assignee"),
    needs_manual_triage: bool = Query(default=False),
    q: str | None = Query(default=None, max_length=200),
    cursor: str | None = Query(default=None, max_length=1000),
    limit: int = Query(default=25, ge=1, le=100),
    locale: str | None = Query(default=None),
    actor: Actor = Depends(current_actor),
) -> dict[str, object]:
    """Return one role-scoped queue page and its opaque continuation cursor."""
    try:
        resolved_assignee_id = _resolve_assignee_filter(assignee_id, actor)
        page = await list_reports(
            actor,
            report_status=report_status,
            urgency=urgency,
            assignee_id=resolved_assignee_id,
            needs_manual_triage=needs_manual_triage,
            query=q,
            cursor=cursor,
            limit=limit,
        )
        raw_items = [dict(row) for row in page.rows]
        paths: list[str] = []
        for item in raw_items:
            thumbnail_path = item.get("thumbnail_storage_path")
            if isinstance(thumbnail_path, str):
                paths.append(thumbnail_path)
            previous_evidence = item.get("previous_evidence", [])
            if isinstance(previous_evidence, str):
                previous_evidence = json.loads(previous_evidence)
            if not isinstance(previous_evidence, list):
                previous_evidence = []
            normalized_evidence = [
                dict(evidence)
                for evidence in previous_evidence
                if isinstance(evidence, dict)
            ]
            item["previous_evidence"] = normalized_evidence
            paths.extend(
                evidence_path
                for evidence in normalized_evidence
                if isinstance(
                    evidence_path := evidence.get("storage_path"),
                    str,
                )
            )
        signed_urls, expires_at = await get_signed_media_urls(paths)
    except ReportListError as error:
        raise report_list_error(error) from error
    except MediaError as error:
        raise media_error(error) from error

    items: list[dict[str, object]] = []
    for item in raw_items:
        item.pop("_urgency_rank", None)
        storage_path = item.pop("thumbnail_storage_path", None)
        item["thumbnail_url"] = signed_urls.get(storage_path) if isinstance(storage_path, str) else None
        item["thumbnail_url_expires_at"] = expires_at if isinstance(storage_path, str) else None
        previous_evidence = item.get("previous_evidence", [])
        if isinstance(previous_evidence, list):
            for evidence in previous_evidence:
                if not isinstance(evidence, dict):
                    continue
                evidence_path = evidence.pop("storage_path", None)
                evidence["signed_url"] = (
                    signed_urls.get(evidence_path)
                    if isinstance(evidence_path, str)
                    else None
                )
                evidence["signed_url_expires_at"] = (
                    expires_at if isinstance(evidence_path, str) else None
                )
        items.append(item)
    if locale == "zh-CN":
        items = await localize_report_items_zh(items)
    return cast(
        dict[str, object],
        jsonable_encoder(
            {
                "items": items,
                "next_cursor": page.next_cursor,
                "counts": {
                    "overdue": page.overdue_count,
                    "rework": page.rework_count,
                },
            }
        ),
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_report(payload: CreateReportRequest, actor: Actor = Depends(current_actor)) -> dict[str, UUID]:
    """Create a draft owned by the authenticated debug actor."""
    if actor.profile_id is None:
        raise HTTPException(403, {"code": "profile_required", "message": "human profile is required"})
    await enforce_rate_limit(
        scope="report_submission",
        subject=str(actor.profile_id),
        limit=get_settings().report_submission_rate_limit_per_minute,
        error_code="report_rate_limited",
    )
    report_id = await create_report(
        actor.profile_id,
        payload.description_original,
        lang_original=payload.lang_original,
        urgency=payload.urgency,
        location_text=payload.location_text,
        activity=payload.activity,
        level_or_zone=payload.level_or_zone,
        grid_ref=payload.grid_ref,
        is_confidential=payload.is_confidential,
    )
    return {"id": report_id}


@router.patch("/{report_id}")
async def patch_report_draft(
    report_id: UUID,
    payload: CreateReportRequest,
    actor: Actor = Depends(current_actor),
) -> dict[str, object]:
    """Update only the caller's draft before the normal submit transition."""
    if actor.profile_id is None:
        raise HTTPException(403, {"code": "profile_required", "message": "human profile is required"})
    try:
        report = await update_draft_report(
            report_id,
            actor.profile_id,
            payload.description_original,
            lang_original=payload.lang_original,
            location_text=payload.location_text,
            activity=payload.activity,
            level_or_zone=payload.level_or_zone,
            grid_ref=payload.grid_ref,
            is_confidential=payload.is_confidential,
        )
    except ReportDraftError as error:
        raise report_draft_error(error) from error
    return cast(dict[str, object], jsonable_encoder(dict(report)))


@router.get("/{report_id}")
async def report_detail(
    report_id: UUID,
    actor: Actor = Depends(current_actor),
    locale: str | None = Query(default=None),
) -> dict[str, object]:
    """Return a report and the legal targets for the calling actor."""
    report = await get_report(report_id)
    if report is None:
        raise HTTPException(404, {"code": "report_not_found", "message": "report does not exist"})
    try:
        assert_report_readable(report, actor)
        media = await get_signed_report_media(report_id)
        clarifications = await list_report_clarifications(report_id)
    except MediaError as error:
        raise media_error(error) from error
    source = ReportStatus(report["status"])
    result = dict(report)
    latest_draft = result.get("latest_draft")
    if isinstance(latest_draft, str):
        result["latest_draft"] = json.loads(latest_draft)
    elif latest_draft is None:
        result["latest_draft"] = None
    current_action = result.get("current_action")
    if isinstance(current_action, str):
        result["current_action"] = json.loads(current_action)
    elif current_action is None:
        result["current_action"] = None
    verifications = result.get("verifications")
    if isinstance(verifications, str):
        result["verifications"] = json.loads(verifications)
    elif verifications is None:
        result["verifications"] = []
    closure_receipt = result.get("closure_receipt")
    if isinstance(closure_receipt, str):
        result["closure_receipt"] = json.loads(closure_receipt)
    elif closure_receipt is None:
        result["closure_receipt"] = None
    current_briefing = result.get("current_briefing")
    if isinstance(current_briefing, str):
        result["current_briefing"] = json.loads(current_briefing)
    elif current_briefing is None:
        result["current_briefing"] = None
    result["media"] = media
    result["clarifications"] = [dict(row) for row in clarifications]
    result["can_answer_clarifications"] = (
        source is ReportStatus.CLARIFYING
        and actor.role is Role.REPORTER
        and actor.profile_id == report["reporter_id"]
    )
    available: list[dict[str, object]] = []
    for target in allowed_targets(source, actor.actor_type, actor.role):
        transition = find(source, target)
        if transition is None:
            raise RuntimeError("allowed state-machine target has no transition")
        transition_payload: dict[str, object] = {
            "event": transition.event,
            "target": transition.target.value,
            "requires_reason": transition.requires_reason,
        }
        decision = _REVIEW_DECISION_BY_EVENT.get(transition.event)
        if decision is not None:
            transition_payload["review_decision"] = decision.value
        available.append(transition_payload)
    result["available_transitions"] = available
    if locale == "zh-CN":
        result = await localize_report_zh(result)
    return cast(dict[str, object], jsonable_encoder(result))


@router.post("/{report_id}/media", status_code=status.HTTP_201_CREATED)
async def post_report_media(
    report_id: UUID,
    payload: RegisterMediaRequest,
    actor: Actor = Depends(current_actor),
) -> dict[str, object]:
    """Register one private object only after checking its stored metadata."""
    try:
        media = await register_report_media(
            report_id,
            actor,
            storage_path=payload.storage_path,
            mime_type=payload.mime_type,
            phase=payload.phase,
            caption=payload.caption,
        )
    except MediaError as error:
        raise media_error(error) from error
    return cast(dict[str, object], jsonable_encoder(dict(media)))


@router.post("/{report_id}/assignments", status_code=status.HTTP_201_CREATED)
async def post_assignment(
    report_id: UUID,
    payload: AssignmentRequest,
    actor: Actor = Depends(current_actor),
) -> dict[str, object]:
    """Approve and assign through the existing all-or-nothing review transaction."""
    try:
        result = await review_report(
            report_id,
            actor,
            decision=ReviewDecision.APPROVE,
            target=ReportStatus.ACTION_ASSIGNED,
            assignee_id=payload.assignee_id,
            case_role=payload.case_role,
            due_at=payload.due_at,
        )
    except ReviewError as error:
        raise review_error(error) from error
    except TransitionError as error:
        raise transition_error(error) from error
    return cast(
        dict[str, object],
        jsonable_encoder(
            {
                "review_id": result.review["id"],
                "report_id": result.report["id"],
                "status": result.report["status"],
                "assignment_id": result.assignment_id,
                "corrective_action_id": result.corrective_action_id,
            }
        ),
    )


@router.post("/{report_id}/actions/{action_id}/submit")
async def post_action_submission(
    report_id: UUID,
    action_id: UUID,
    payload: ActionSubmitRequest,
    actor: Actor = Depends(current_actor),
) -> dict[str, object]:
    """Commit technician proof and the submit-evidence transition together."""
    try:
        result = await submit_action(
            report_id,
            action_id,
            actor,
            completed_note=payload.completed_note,
            media_ids=payload.media_ids,
            transcript_id=payload.transcript_id,
        )
    except ActionError as error:
        raise action_error(error) from error
    except TransitionError as error:
        raise transition_error(error) from error
    return cast(
        dict[str, object],
        jsonable_encoder(
            {
                "report_id": result.report["id"],
                "action_id": result.action["id"],
                "status": result.report["status"],
                "completed_note": result.action["completed_note"],
                "submitted_at": result.action["submitted_at"],
                "media_ids": result.media_ids,
            }
        ),
    )


@router.post("/{report_id}/verify")
async def post_verification(
    report_id: UUID,
    payload: VerifyRequest,
    background_tasks: BackgroundTasks,
    actor: Actor = Depends(current_actor),
) -> dict[str, object]:
    """Commit append-only inspection evidence and its report transition together."""
    try:
        result = await verify_report(
            report_id,
            actor,
            passed=payload.passed,
            checklist=payload.checklist,
            notes=payload.notes,
            reason=payload.reason,
            new_due_at=payload.new_due_at,
        )
    except VerificationError as error:
        raise verification_error(error) from error
    except TransitionError as error:
        raise transition_error(error) from error
    if payload.passed and result.report["status"] == ReportStatus.VERIFIED_CLOSED.value:
        queued, _ = await queue_lesson_run(report_id)
        if queued:
            background_tasks.add_task(run_lesson, report_id, current_request_id())
    return cast(
        dict[str, object],
        jsonable_encoder(
            {
                "verification_id": result.verification["id"],
                "report_id": result.report["id"],
                "status": result.report["status"],
                "closed_at": result.report["closed_at"],
                "corrective_action_id": result.action["id"],
                "action_status": result.action["status"],
                "rework_count": result.action["rework_count"],
                "assignment_id": result.assignment["id"],
                "due_at": result.assignment["due_at"],
            }
        ),
    )


@router.post("/{report_id}/lesson-draft", status_code=status.HTTP_202_ACCEPTED)
async def post_lesson_draft(
    report_id: UUID,
    background_tasks: BackgroundTasks,
    actor: Actor = Depends(current_actor),
) -> dict[str, str]:
    """Let a reviewer safely restart a lesson job stranded after verification."""
    report = await get_report(report_id)
    if report is None:
        raise HTTPException(404, {"code": "report_not_found", "message": "report does not exist"})
    try:
        assert_report_readable(report, actor)
    except MediaError as error:
        raise media_error(error) from error
    if actor.role is not Role.REVIEWER:
        raise HTTPException(
            403,
            {"code": "lesson_actor_forbidden", "message": "lesson drafting requires a reviewer"},
        )
    if ReportStatus(report["status"]) is not ReportStatus.VERIFIED_CLOSED:
        raise HTTPException(
            409,
            {"code": "lesson_not_ready", "message": "report is not waiting for lesson drafting"},
        )
    queued, run_status = await queue_lesson_run(report_id)
    if queued:
        background_tasks.add_task(run_lesson, report_id, current_request_id())
    return {"report_id": str(report_id), "status": run_status.phase}


@router.get("/{report_id}/lesson-draft/status")
async def lesson_draft_status(
    report_id: UUID,
    actor: Actor = Depends(current_actor),
) -> dict[str, object]:
    """Report whether drafting is idle, running, complete, or failed."""
    report = await get_report(report_id)
    if report is None:
        raise HTTPException(404, {"code": "report_not_found", "message": "report does not exist"})
    try:
        assert_report_readable(report, actor)
    except MediaError as error:
        raise media_error(error) from error
    if actor.role is not Role.REVIEWER:
        raise HTTPException(
            403,
            {"code": "lesson_actor_forbidden", "message": "lesson status requires a reviewer"},
        )
    report_status = ReportStatus(report["status"])
    if report_status in {ReportStatus.LESSON_DRAFTED, ReportStatus.LESSON_PUBLISHED}:
        current_briefing = report["current_briefing"]
        briefing = json.loads(current_briefing) if isinstance(current_briefing, str) else current_briefing
        return {
            "report_id": str(report_id),
            "status": "succeeded",
            "started_at": None,
            "finished_at": None,
            "briefing_id": briefing.get("id") if isinstance(briefing, dict) else None,
        }
    run_status = await get_lesson_run_status(report_id)
    return {
        "report_id": str(report_id),
        "status": run_status.phase,
        "started_at": run_status.started_at,
        "finished_at": run_status.finished_at,
        "briefing_id": None,
    }


@router.post("/{report_id}/review")
async def post_review(
    report_id: UUID,
    payload: ReviewRequest,
    actor: Actor = Depends(current_actor),
) -> dict[str, object]:
    """Commit the review row and its transition through the sole status writer."""
    try:
        result = await review_report(
            report_id,
            actor,
            decision=payload.decision,
            target=payload.target,
            reason=payload.reason,
            corrected_category=payload.corrected_category,
            corrected_urgency=payload.corrected_urgency,
            corrected_action=payload.corrected_action,
            correction_reason=payload.correction_reason,
            assignee_id=payload.assignee_id,
            due_at=payload.due_at,
        )
    except ReviewError as error:
        raise review_error(error) from error
    except TransitionError as error:
        raise transition_error(error) from error
    return cast(
        dict[str, object],
        jsonable_encoder(
            {
                "review_id": result.review["id"],
                "report_id": result.report["id"],
                "status": result.report["status"],
                "assignment_id": result.assignment_id,
                "corrective_action_id": result.corrective_action_id,
            }
        ),
    )


@router.get("/{report_id}/timeline")
async def report_timeline(report_id: UUID, actor: Actor = Depends(current_actor)) -> list[dict[str, object]]:
    """Return the report's audit timeline."""
    report = await get_report(report_id)
    if report is None:
        raise HTTPException(404, {"code": "report_not_found", "message": "report does not exist"})
    try:
        assert_report_readable(report, actor)
    except MediaError as error:
        raise media_error(error) from error
    return cast(
        list[dict[str, object]],
        jsonable_encoder([dict(row) for row in await get_timeline(report_id)]),
    )


@router.post("/{report_id}/transition")
async def post_transition(
    report_id: UUID,
    payload: TransitionRequest,
    background_tasks: BackgroundTasks,
    actor: Actor = Depends(current_actor),
) -> dict[str, object]:
    """Apply one legal transition through the sole status-writing service."""
    existing_report = await get_report(report_id)
    if existing_report is None:
        raise HTTPException(404, {"code": "report_not_found", "message": "report does not exist"})
    try:
        assert_report_readable(existing_report, actor)
        if payload.target is ReportStatus.SUBMITTED:
            report = await submit_report(
                report_id,
                actor,
                confirmed_text=payload.confirmed_text or "",
                transcript_id=payload.transcript_id,
                audio_media_id=payload.audio_media_id,
            )
        else:
            report = await transition_report(
                report_id,
                payload.target,
                actor,
                reason=payload.reason,
                metadata=payload.metadata,
            )
    except MediaError as error:
        raise media_error(error) from error
    except TransitionError as error:
        raise transition_error(error) from error
    except ReportSubmissionError as error:
        raise submission_error(error) from error
    if payload.target is ReportStatus.SUBMITTED:
        background_tasks.add_task(run_intake, report_id, current_request_id())
    return cast(dict[str, object], jsonable_encoder(dict(report)))


@router.post("/{report_id}/clarifications/{clarification_id}/answer")
async def post_clarification_answer(
    report_id: UUID,
    clarification_id: UUID,
    payload: ClarificationAnswerRequest,
    background_tasks: BackgroundTasks,
    actor: Actor = Depends(current_actor),
) -> dict[str, object]:
    """Store one answer and resume intake after the active round is complete."""
    try:
        result = await answer_clarification(
            report_id,
            clarification_id,
            actor,
            payload.answer,
            payload.transcript_id,
        )
    except ClarificationError as error:
        raise clarification_error(error) from error
    if result.rerun:
        background_tasks.add_task(run_intake, report_id, current_request_id())
    return cast(
        dict[str, object],
        jsonable_encoder(
            {
                "id": result.clarification["id"],
                "report_id": result.clarification["report_id"],
                "answered_at": result.clarification["answered_at"],
                "round_complete": result.rerun,
            }
        ),
    )
