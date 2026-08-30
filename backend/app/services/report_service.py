"""Persist reports and centralise every legal status transition."""

from __future__ import annotations

import base64
from binascii import Error as BinasciiError
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
import json
import logging
from typing import Any
from uuid import UUID

import asyncpg
from asyncpg.pool import PoolConnectionProxy
import httpx

from app.config import get_settings
from app.db import connection
from app.domain.enums import ActorType, InputMode, ReportStatus, Role, Urgency
from app.domain.transitions import TransitionError, assert_can


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Actor:
    """Identify the principal responsible for a database mutation."""

    actor_type: ActorType
    profile_id: UUID | None = None
    role: Role | None = None

    @classmethod
    def ai(cls) -> "Actor":
        """Construct the model actor, which has no human profile."""
        return cls(ActorType.AI)

    @classmethod
    def system(cls) -> "Actor":
        """Construct the trusted orchestration actor."""
        return cls(ActorType.SYSTEM)


class ReportListError(Exception):
    """Carry a stable list-query code without exposing user-facing API prose."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ReportDraftError(Exception):
    """Carry a stable draft-update code without exposing user-facing prose."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ReportSubmissionError(Exception):
    """Carry stable confirmation errors from the atomic submit boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TranscriptConfirmationError(Exception):
    """Reject transcript evidence that is absent, reused, or belongs elsewhere."""


@dataclass(frozen=True)
class ReportPage:
    """Return one stable queue page and the cursor for the following page."""

    rows: list[asyncpg.Record]
    next_cursor: str | None
    overdue_count: int = 0
    rework_count: int = 0


@dataclass(frozen=True)
class _ReportCursor:
    urgency_rank: int
    created_at: datetime
    report_id: UUID


_URGENCY_RANK_SQL = """
case r.urgency
  when 'critical'::urgency then 4
  when 'high'::urgency then 3
  when 'medium'::urgency then 2
  else 1
end
""".strip()


def _encode_cursor(cursor: _ReportCursor) -> str:
    payload = json.dumps(
        {
            "u": cursor.urgency_rank,
            "c": cursor.created_at.isoformat(),
            "i": str(cursor.report_id),
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(value: str) -> _ReportCursor:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding))
        if not isinstance(payload, dict):
            raise ValueError
        urgency_rank = int(payload["u"])
        created_at = datetime.fromisoformat(str(payload["c"]))
        report_id = UUID(str(payload["i"]))
        if urgency_rank not in {1, 2, 3, 4} or created_at.tzinfo is None:
            raise ValueError
    except (
        BinasciiError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise ReportListError("invalid_cursor", "report list cursor is invalid") from error
    return _ReportCursor(urgency_rank, created_at, report_id)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_reports(
    actor: Actor,
    *,
    report_status: ReportStatus | None = None,
    urgency: Urgency | None = None,
    assignee_id: UUID | None = None,
    needs_manual_triage: bool = False,
    query: str | None = None,
    cursor: str | None = None,
    limit: int = 25,
) -> ReportPage:
    """List only role-visible reports using a stable urgency-and-age cursor."""
    if (
        actor.actor_type is not ActorType.HUMAN
        or actor.profile_id is None
        or actor.role is None
        or actor.role is Role.CREW
    ):
        raise ReportListError("report_list_forbidden", "actor cannot list reports")
    if not 1 <= limit <= 100:
        raise ReportListError("invalid_page_size", "report list page size is invalid")
    if needs_manual_triage and actor.role not in {Role.REVIEWER, Role.ADMIN}:
        raise ReportListError(
            "report_list_forbidden",
            "manual triage is available only to reviewers and administrators",
        )

    values: list[object] = []

    def bind(value: object) -> str:
        values.append(value)
        return f"${len(values)}"

    clauses: list[str] = []
    if actor.role is Role.REPORTER:
        clauses.append(f"r.reporter_id = {bind(actor.profile_id)}")
    elif actor.role is Role.RESPONSIBLE:
        actor_parameter = bind(actor.profile_id)
        clauses.append(
            "exists (select 1 from report_assignments role_assignment "
            f"where role_assignment.report_id = r.id and role_assignment.active "
            f"and role_assignment.assignee_id = {actor_parameter})"
        )

    if report_status is not None:
        clauses.append(f"r.status = {bind(report_status.value)}::report_status")
    if urgency is not None:
        clauses.append(f"r.urgency = {bind(urgency.value)}::urgency")
    if assignee_id is not None:
        assignee_parameter = bind(assignee_id)
        clauses.append(
            "exists (select 1 from report_assignments filtered_assignment "
            f"where filtered_assignment.report_id = r.id and filtered_assignment.active "
            f"and filtered_assignment.assignee_id = {assignee_parameter})"
        )
    if needs_manual_triage:
        clauses.append(
            "r.status = 'ai_drafted'::report_status and exists ("
            "select 1 from ai_drafts manual_triage_draft "
            "where manual_triage_draft.report_id = r.id "
            "and manual_triage_draft.validation = 'invalid'::validation_status "
            "and manual_triage_draft.version = ("
            "select max(latest_draft.version) from ai_drafts latest_draft "
            "where latest_draft.report_id = r.id))"
        )

    normalized_query = query.strip() if query else ""
    if normalized_query:
        search_parameter = bind(f"%{_escape_like(normalized_query)}%")
        clauses.append(
            "(coalesce(r.human_ref, '') || ' ' || "
            "coalesce(r.description_en, '') || ' ' || "
            "coalesce(r.description_original, '') || ' ' || "
            "coalesce(r.location_text, '') || ' ' || "
            "coalesce(r.activity, '')) "
            f"ilike {search_parameter} escape '\\'"
        )

    decoded_cursor = _decode_cursor(cursor) if cursor else None
    if decoded_cursor is not None:
        urgency_parameter = bind(decoded_cursor.urgency_rank)
        created_parameter = bind(decoded_cursor.created_at)
        id_parameter = bind(decoded_cursor.report_id)
        clauses.append(
            f"(({_URGENCY_RANK_SQL}) < {urgency_parameter} or "
            f"(({_URGENCY_RANK_SQL}) = {urgency_parameter} and "
            f"(r.created_at, r.id) > ({created_parameter}, {id_parameter})))"
        )

    where_sql = " and ".join(clauses) if clauses else "true"
    page_size_parameter = bind(limit + 1)
    sql = f"""
        with queue_page as (
          select
            r.id,
            r.human_ref,
            r.status::text as status,
            r.urgency::text as urgency,
            coalesce(nullif(btrim(r.description_en), ''), r.description_original) as summary,
            r.location_text,
            r.created_at,
            ({_URGENCY_RANK_SQL}) as _urgency_rank
          from reports r
          where {where_sql}
          order by _urgency_rank desc, r.created_at, r.id
          limit {page_size_parameter}
        )
        select
          queue_page.*,
          media.storage_path as thumbnail_storage_path,
          media.caption as thumbnail_caption,
          action.id as action_id,
          action.action_text,
          action.action_status,
          action.action_due_at,
          action.completed_note,
          action.submitted_at as action_submitted_at,
          coalesce(action.rework_count, 0)::integer as rework_count,
          coalesce(action.rework_count, 0) >= 2 as rework_attention,
          coalesce(action.rework_count, 0) >= 1
            and action.action_status = 'assigned' as sent_back_unresolved,
          action.deficiency_reason,
          action.deficiency_notes,
          action.deficiency_created_at,
          action.deficiency_reviewer_name,
          coalesce(action.previous_evidence, '[]'::jsonb) as previous_evidence
        from queue_page
        left join lateral (
          select report_media.storage_path, report_media.caption
          from report_media
          where report_media.report_id = queue_page.id
            and report_media.phase = 'original'::media_phase
            and report_media.mime_type in ('image/jpeg', 'image/png', 'image/webp')
          order by report_media.created_at, report_media.id
          limit 1
        ) media on true
        left join lateral (
          select
            corrective_action.id,
            corrective_action.action_text,
            corrective_action.status::text as action_status,
            corrective_action.due_at as action_due_at,
            corrective_action.completed_note,
            corrective_action.submitted_at,
            corrective_action.rework_count::integer as rework_count,
            failed_verification.reason as deficiency_reason,
            failed_verification.notes as deficiency_notes,
            failed_verification.created_at as deficiency_created_at,
            failed_verification.reviewer_name as deficiency_reviewer_name,
            evidence.previous_evidence
          from corrective_actions corrective_action
          join report_assignments assignment
            on assignment.id = corrective_action.assignment_id
           and assignment.report_id = corrective_action.report_id
           and assignment.active
          left join lateral (
            select
              verification.reason,
              verification.notes,
              verification.created_at,
              coalesce(
                nullif(btrim(reviewer.display_name), ''),
                reviewer.role::text
              ) as reviewer_name
            from verifications verification
            join profiles reviewer on reviewer.id = verification.reviewer_id
            where verification.corrective_action_id = corrective_action.id
              and not verification.passed
            order by verification.created_at desc, verification.id desc
            limit 1
          ) failed_verification on true
          left join lateral (
            select jsonb_agg(
              jsonb_build_object(
                'id', evidence_media.id,
                'storage_path', evidence_media.storage_path,
                'caption', evidence_media.caption,
                'created_at', evidence_media.created_at
              )
              order by evidence_media.created_at, evidence_media.id
            ) as previous_evidence
            from report_media evidence_media
            where evidence_media.corrective_action_id = corrective_action.id
              and evidence_media.phase = 'evidence'::media_phase
          ) evidence on true
          where corrective_action.report_id = queue_page.id
          order by corrective_action.created_at desc, corrective_action.id desc
          limit 1
        ) action on true
        order by queue_page._urgency_rank desc, queue_page.created_at, queue_page.id
    """
    async with connection() as conn:
        rows = await conn.fetch(sql, *values)
        queue_counts = None
        if actor.role in {Role.REVIEWER, Role.ADMIN}:
            queue_counts = await conn.fetchrow(
                """
                select
                  count(*) filter (
                    where action.status = 'assigned'::action_status
                      and action.due_at < now()
                      and assignment.active
                      and report.status = 'action_assigned'::report_status
                  )::integer as overdue_count,
                  count(*) filter (
                    where action.status <> 'verified'::action_status
                      and action.rework_count >= 1
                      and assignment.active
                  )::integer as rework_count
                from corrective_actions action
                join report_assignments assignment
                  on assignment.id = action.assignment_id
                 and assignment.report_id = action.report_id
                join reports report on report.id = action.report_id
                """
            )

    visible_rows = rows[:limit]
    next_cursor = None
    if len(rows) > limit and visible_rows:
        last = visible_rows[-1]
        next_cursor = _encode_cursor(
            _ReportCursor(last["_urgency_rank"], last["created_at"], last["id"])
        )
    return ReportPage(
        visible_rows,
        next_cursor,
        int(queue_counts["overdue_count"]) if queue_counts is not None else 0,
        int(queue_counts["rework_count"]) if queue_counts is not None else 0,
    )


async def create_report(
    reporter_id: UUID,
    description_original: str,
    *,
    lang_original: str = "en",
    urgency: str = "medium",
    location_text: str | None = None,
    activity: str | None = None,
    level_or_zone: str | None = None,
    grid_ref: str | None = None,
    is_confidential: bool = False,
) -> UUID:
    """Create a draft; the database trigger assigns its human reference."""
    async with connection() as conn:
        async with conn.transaction():
            report_id = await conn.fetchval(
                """
                INSERT INTO reports (
                  reporter_id, description_original, lang_original, urgency,
                  location_text, activity, level_or_zone, grid_ref,
                  is_confidential
                )
                VALUES ($1, $2, $3, $4::urgency, $5, $6, $7, $8, $9)
                RETURNING id
                """,
                reporter_id,
                description_original,
                lang_original,
                urgency,
                location_text,
                activity,
                level_or_zone,
                grid_ref,
                is_confidential,
            )
            await conn.execute(
                """
                INSERT INTO audit_log (report_id, actor_type, actor_id, event, target, metadata)
                VALUES ($1, 'human', $2, 'create_report', 'draft', '{}'::jsonb)
                """,
                report_id,
                reporter_id,
            )
    if not isinstance(report_id, UUID):
        raise RuntimeError("database returned an invalid report id")
    return report_id


async def update_draft_report(
    report_id: UUID,
    reporter_id: UUID,
    description_original: str,
    *,
    lang_original: str,
    location_text: str | None,
    activity: str | None,
    level_or_zone: str | None,
    grid_ref: str | None,
    is_confidential: bool,
) -> asyncpg.Record:
    """Finish an urgent draft without creating a second path for status writes."""
    async with connection() as conn:
        async with conn.transaction():
            report = await conn.fetchrow(
                "select * from reports where id = $1 for update",
                report_id,
            )
            if report is None:
                raise ReportDraftError("report_not_found", "report does not exist")
            if report["reporter_id"] != reporter_id:
                raise ReportDraftError("report_forbidden", "draft belongs to another reporter")
            if ReportStatus(report["status"]) is not ReportStatus.DRAFT:
                raise ReportDraftError("draft_update_forbidden", "only a draft can be updated")
            updated = await conn.fetchrow(
                """
                update reports
                set description_original = $2,
                    lang_original = $3,
                    location_text = $4,
                    activity = $5,
                    level_or_zone = $6,
                    grid_ref = $7,
                    is_confidential = $8
                where id = $1
                returning *
                """,
                report_id,
                description_original,
                lang_original,
                location_text,
                activity,
                level_or_zone,
                grid_ref,
                is_confidential,
            )
            if updated is None:
                raise RuntimeError("draft disappeared while updating")
            return updated


async def get_report(report_id: UUID) -> asyncpg.Record | None:
    """Read one report with its immutable draft and verification history attached."""
    async with connection() as conn:
        return await conn.fetchrow(
            """
            select
              r.*,
              latest.latest_draft,
              current_action.current_action,
              verification_history.verifications,
              closure.closure_receipt
            from reports r
            left join lateral (
              select jsonb_build_object(
                'id', d.id,
                'version', d.version,
                'observed_facts', d.observed_facts,
                'assumptions', d.assumptions,
                'missing_information', d.missing_information,
                'proposed_category', d.proposed_category,
                'proposed_urgency', d.proposed_urgency::text,
                'suggested_owner_role', d.suggested_owner_role::text,
                'suggested_action', d.suggested_action,
                'confidence', d.confidence,
                'needs_escalation', d.needs_escalation,
                'escalation_reason', d.escalation_reason,
                'citations', d.citations,
                'validation', d.validation::text,
                'validation_errors', d.validation_errors,
                'created_at', d.created_at
              ) as latest_draft
              from ai_drafts d
              where d.report_id = r.id
              order by d.version desc
              limit 1
            ) latest on true
            left join lateral (
              select jsonb_build_object(
                'id', action.id,
                'assignment_id', assignment.id,
                'assignee_id', assignment.assignee_id,
                'assignee_name', coalesce(
                  nullif(btrim(assignee.display_name), ''),
                  assignee.role::text
                ),
                'assignment_active', assignment.active,
                'action_text', action.action_text,
                'status', action.status::text,
                'rework_count', action.rework_count,
                'due_at', action.due_at,
                'completed_note', action.completed_note,
                'submitted_at', action.submitted_at
              ) as current_action
              from corrective_actions action
              join report_assignments assignment
                on assignment.id = action.assignment_id
               and assignment.report_id = action.report_id
              join profiles assignee on assignee.id = assignment.assignee_id
              where action.report_id = r.id
              order by action.created_at desc, action.id desc
              limit 1
            ) current_action on true
            left join lateral (
              select coalesce(
                jsonb_agg(
                  jsonb_build_object(
                    'id', verification.id,
                    'corrective_action_id', verification.corrective_action_id,
                    'reviewer_id', verification.reviewer_id,
                    'reviewer_name', coalesce(
                      nullif(btrim(reviewer.display_name), ''),
                      reviewer.role::text
                    ),
                    'passed', verification.passed,
                    'checklist', verification.checklist,
                    'notes', verification.notes,
                    'reason', verification.reason,
                    'new_due_at', verification.new_due_at,
                    'created_at', verification.created_at
                  )
                  order by verification.created_at, verification.id
                ),
                '[]'::jsonb
              ) as verifications
              from verifications verification
              join profiles reviewer on reviewer.id = verification.reviewer_id
              where verification.report_id = r.id
            ) verification_history on true
            left join lateral (
              select jsonb_build_object(
                'id', receipt.id,
                'verification_id', receipt.verification_id,
                'corrective_action_id', receipt.corrective_action_id,
                'reporter_locale', receipt.reporter_locale,
                'action_text', receipt.action_text,
                'verification_notes', receipt.verification_notes,
                'verified_by_id', receipt.verified_by_id,
                'verified_by_name', receipt.verified_by_name,
                'before_media_id', receipt.before_media_id,
                'after_media_id', receipt.after_media_id,
                'created_at', receipt.created_at
              ) as closure_receipt
              from closure_receipts receipt
              where receipt.report_id = r.id
            ) closure on true
            where r.id = $1
            """,
            report_id,
        )


async def get_timeline(report_id: UUID) -> list[asyncpg.Record]:
    """Read the immutable report audit trail in chronological order."""
    async with connection() as conn:
        return await conn.fetch(
            """
            SELECT audit_log.*, profiles.role::text AS actor_role
            FROM audit_log
            LEFT JOIN profiles ON profiles.id = audit_log.actor_id
            WHERE audit_log.report_id = $1
            ORDER BY audit_log.created_at, audit_log.id
            """,
            report_id,
        )


@asynccontextmanager
async def _transition_connection(
    existing: PoolConnectionProxy[asyncpg.Record] | None,
) -> AsyncIterator[PoolConnectionProxy[asyncpg.Record]]:
    if existing is not None:
        yield existing
        return
    async with connection() as conn:
        yield conn


def input_mode_for_submission(
    confirmed_text: str,
    raw_transcript: str | None,
) -> InputMode:
    """Classify input from server-owned transcript evidence, never a client flag."""
    if raw_transcript is None:
        return InputMode.TYPED
    if confirmed_text == raw_transcript:
        return InputMode.VOICE
    return InputMode.VOICE_EDITED


async def confirm_transcript_text(
    conn: PoolConnectionProxy[asyncpg.Record],
    *,
    report_id: UUID,
    transcript_id: UUID | None,
    confirmed_text: str,
    context: str,
    context_id: UUID,
) -> InputMode:
    """Classify and immutably link server-owned ASR evidence to editable text."""
    if transcript_id is None:
        return InputMode.TYPED
    raw_transcript = await conn.fetchval(
        "select text_raw from transcripts where id = $1 and report_id = $2",
        transcript_id,
        report_id,
    )
    if raw_transcript is None:
        raise TranscriptConfirmationError("transcript does not belong to this report")
    input_mode = input_mode_for_submission(confirmed_text, str(raw_transcript))
    try:
        await conn.execute(
            """
            insert into transcript_confirmations (
              transcript_id, report_id, context, context_id,
              confirmed_text, input_mode
            ) values ($1, $2, $3, $4, $5, $6::input_mode)
            """,
            transcript_id,
            report_id,
            context,
            context_id,
            confirmed_text,
            input_mode.value,
        )
    except asyncpg.UniqueViolationError as error:
        raise TranscriptConfirmationError("transcript was already confirmed") from error
    return input_mode


async def submit_report(
    report_id: UUID,
    actor: Actor,
    *,
    confirmed_text: str,
    transcript_id: UUID | None,
    audio_media_id: UUID | None = None,
) -> asyncpg.Record:
    """Persist confirmed text and only the reporter's final audio recording."""
    confirmed = confirmed_text.strip()
    if not confirmed:
        raise ReportSubmissionError(
            "confirmed_text_required",
            "confirmed report text is required",
        )
    if actor.actor_type is not ActorType.HUMAN or actor.profile_id is None:
        raise ReportSubmissionError(
            "submission_actor_forbidden",
            "report submission requires a human profile",
        )

    discarded_audio_paths: list[str] = []
    async with connection() as conn:
        async with conn.transaction():
            report = await conn.fetchrow(
                "select reporter_id, status::text from reports where id = $1 for update",
                report_id,
            )
            if report is None:
                raise ReportSubmissionError(
                    "report_not_found", "report does not exist"
                )
            if report["reporter_id"] != actor.profile_id:
                raise ReportSubmissionError(
                    "submission_forbidden", "draft belongs to another reporter"
                )
            if report["status"] != ReportStatus.DRAFT.value:
                raise ReportSubmissionError(
                    "report_not_submittable", "only a draft can be submitted"
                )

            selected_audio_id = audio_media_id
            if transcript_id is not None:
                transcript_media_id = await conn.fetchval(
                    "select media_id from transcripts where id = $1 and report_id = $2",
                    transcript_id,
                    report_id,
                )
                if transcript_media_id is None:
                    raise ReportSubmissionError(
                        "transcript_not_found",
                        "transcript does not belong to this report",
                    )
                if selected_audio_id is None:
                    selected_audio_id = transcript_media_id
                elif selected_audio_id != transcript_media_id:
                    raise ReportSubmissionError(
                        "audio_transcript_mismatch",
                        "final audio does not match the confirmed transcript",
                    )

            if selected_audio_id is not None:
                selected_audio = await conn.fetchrow(
                    """
                    select id
                    from report_media
                    where id = $1
                      and report_id = $2
                      and phase = 'original'::media_phase
                      and mime_type like 'audio/%'
                    """,
                    selected_audio_id,
                    report_id,
                )
                if selected_audio is None:
                    raise ReportSubmissionError(
                        "audio_media_not_found",
                        "final audio does not belong to this report",
                    )

            discarded_audio = await conn.fetch(
                """
                delete from report_media
                where report_id = $1
                  and phase = 'original'::media_phase
                  and mime_type like 'audio/%'
                  and ($2::uuid is null or id <> $2)
                returning storage_path
                """,
                report_id,
                selected_audio_id,
            )
            discarded_audio_paths = [str(row["storage_path"]) for row in discarded_audio]

            try:
                input_mode = await confirm_transcript_text(
                    conn,
                    report_id=report_id,
                    transcript_id=transcript_id,
                    confirmed_text=confirmed,
                    context="report_description",
                    context_id=report_id,
                )
            except TranscriptConfirmationError as error:
                raise ReportSubmissionError(
                    "transcript_not_found", str(error)
                ) from error
            await conn.execute(
                """
                update reports
                set description_original = $2,
                    input_mode = $3::input_mode
                where id = $1
                """,
                report_id,
                confirmed,
                input_mode.value,
            )
            submitted_report = await transition_report(
                report_id,
                ReportStatus.SUBMITTED,
                actor,
                metadata={
                    "input_mode": input_mode.value,
                    "transcript_id": (
                        str(transcript_id) if transcript_id is not None else None
                    ),
                    "audio_media_id": (
                        str(selected_audio_id)
                        if selected_audio_id is not None
                        else None
                    ),
                },
                transaction_connection=conn,
            )

    if discarded_audio_paths:
        settings = get_settings()
        if settings.supabase_url and settings.supabase_service_role_key:
            endpoint = (
                f"{settings.supabase_url.rstrip('/')}/storage/v1/object/"
                f"{settings.report_audio_bucket}"
            )
            headers = {
                "apikey": settings.supabase_service_role_key,
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.request(
                        "DELETE",
                        endpoint,
                        headers=headers,
                        json={"prefixes": discarded_audio_paths},
                    )
                    response.raise_for_status()
            except httpx.HTTPError:
                logger.warning(
                    "final audio selected but discarded storage objects could not be removed",
                    extra={"report_id": str(report_id)},
                )
    return submitted_report


async def transition_report(
    report_id: UUID,
    target: ReportStatus,
    actor: Actor,
    *,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    transaction_connection: PoolConnectionProxy[asyncpg.Record] | None = None,
) -> asyncpg.Record:
    """Apply one state-machine edge and its audit row in one transaction."""
    async with _transition_connection(transaction_connection) as conn:
        try:
            async with conn.transaction():
                report = await conn.fetchrow(
                    "SELECT * FROM reports WHERE id = $1 FOR UPDATE", report_id
                )
                if report is None:
                    raise TransitionError("illegal_transition", "report does not exist")

                source = ReportStatus(report["status"])
                transition = assert_can(source, target, actor.actor_type, actor.role, reason)
                if target is ReportStatus.ACTION_ASSIGNED:
                    assignment_ready = await conn.fetchval(
                        """
                        select exists (
                          select 1
                          from report_assignments assignment
                          join corrective_actions action
                            on action.assignment_id = assignment.id
                           and action.report_id = assignment.report_id
                          where assignment.report_id = $1 and assignment.active
                        )
                        """,
                        report_id,
                    )
                    if not assignment_ready:
                        raise TransitionError(
                            "assignment_required",
                            "action assignment and corrective action are required",
                        )
                await conn.execute(
                    "SELECT set_config('safeloop.actor_type', $1, true)",
                    actor.actor_type.value,
                )
                await conn.execute(
                    """
                    UPDATE reports
                    SET status = $2::report_status,
                        submitted_at = CASE WHEN $2::report_status = 'submitted'
                          THEN COALESCE(submitted_at, now()) ELSE submitted_at END,
                        closed_at = CASE WHEN $2::report_status = 'verified_closed'
                          THEN COALESCE(closed_at, now()) ELSE closed_at END
                    WHERE id = $1
                    """,
                    report_id,
                    target.value,
                )
                await conn.execute(
                    """
                    INSERT INTO audit_log (
                      report_id, actor_type, actor_id, event, source, target, reason, metadata
                    )
                    VALUES ($1, $2::actor_type, $3, $4, $5::report_status,
                            $6::report_status, $7, $8::jsonb)
                    """,
                    report_id,
                    actor.actor_type.value,
                    actor.profile_id,
                    transition.event,
                    source.value,
                    target.value,
                    reason,
                    json.dumps(metadata or {}),
                )
                result = await conn.fetchrow("SELECT * FROM reports WHERE id = $1", report_id)
                if result is None:
                    raise RuntimeError("report disappeared after transition")
                return result
        except asyncpg.InsufficientPrivilegeError as error:
            raise TransitionError("database_guard", "database guard rejected transition") from error
