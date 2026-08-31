"""Persist one bilingual lesson only from an immutable human-verified closure."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import re
from time import perf_counter
from typing import cast
from uuid import UUID

from app.ai.lesson_graph import (
    LessonProcedure,
    LessonState,
    LocalisedText,
    QuizQuestion,
    VerifiedCase,
    lesson_graph,
)
from app.ai.provider import JsonScalar, JsonValue
from app.ai.usage import capture_ai_usage
from app.config import get_settings
from app.db import connection
from app.domain.enums import ReportStatus
from app.rag.retrieve import retrieve_chunks
from app.observability import bind_request_id, log_event, track_exception
from app.services.report_service import Actor, transition_report

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _LoadedLesson:
    state: LessonState
    retrieval_query: str


@dataclass(frozen=True)
class LessonRunStatus:
    """Expose one process-local lesson run without leaking provider details."""

    phase: str
    started_at: str | None = None
    finished_at: str | None = None


_lesson_runs: dict[UUID, LessonRunStatus] = {}
_lesson_runs_lock = asyncio.Lock()


async def queue_lesson_run(report_id: UUID) -> tuple[bool, LessonRunStatus]:
    """Claim one lesson run so repeated reviewer clicks stay idempotent."""
    async with _lesson_runs_lock:
        existing = _lesson_runs.get(report_id)
        if existing is not None and existing.phase in {"queued", "running"}:
            return False, existing
        status = LessonRunStatus(
            phase="queued",
            started_at=datetime.now(UTC).isoformat(),
        )
        _lesson_runs[report_id] = status
        return True, status


async def get_lesson_run_status(report_id: UUID) -> LessonRunStatus:
    """Return the latest observable state for the current backend process."""
    async with _lesson_runs_lock:
        return _lesson_runs.get(report_id, LessonRunStatus(phase="idle"))


async def _record_lesson_run(
    report_id: UUID,
    phase: str,
    *,
    started_at: str | None = None,
    finished: bool = False,
) -> LessonRunStatus:
    async with _lesson_runs_lock:
        previous = _lesson_runs.get(report_id)
        status = LessonRunStatus(
            phase=phase,
            started_at=started_at or (previous.started_at if previous else None),
            finished_at=datetime.now(UTC).isoformat() if finished else None,
        )
        _lesson_runs[report_id] = status
        return status


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str) or type(value) in {int, float, bool}:
        return cast(JsonScalar, value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {str(key): _json_value(item) for key, item in value.items()}
    raise TypeError("verified checklist is not JSON serialisable")


def _decoded_json(value: object) -> JsonValue:
    if isinstance(value, str):
        return _json_value(json.loads(value))
    return _json_value(value)


def _redact_names(value: str | None, names: list[str]) -> str | None:
    if value is None:
        return None
    redacted = value
    for name in sorted(names, key=len, reverse=True):
        if name.strip():
            escaped = re.escape(name.strip())
            pattern = (
                rf"(?<!\w){escaped}(?!\w)"
                if re.fullmatch(r"[A-Za-z0-9 .'-]+", name.strip())
                else escaped
            )
            redacted = re.sub(
                pattern,
                "a worker",
                redacted,
                flags=re.IGNORECASE,
            )
    return redacted.strip()


async def _load_lesson(report_id: UUID) -> _LoadedLesson | None:
    """Load only the final accepted submission and its passed verification."""
    async with connection() as conn:
        row = await conn.fetchrow(
            """
            select
              report.status::text,
              report.activity,
              report.location_text,
              report.reporter_id,
              receipt.action_text,
              receipt.verification_notes,
              receipt.verified_by_id,
              action.id as corrective_action_id,
              verification.checklist,
              assignment.assignee_id,
              accepted_submission.metadata ->> 'completed_note' as completed_note
            from reports report
            join closure_receipts receipt on receipt.report_id = report.id
            join corrective_actions action
              on action.id = receipt.corrective_action_id
             and action.report_id = report.id
             and action.status = 'verified'::action_status
            join verifications verification
              on verification.id = receipt.verification_id
             and verification.report_id = report.id
             and verification.corrective_action_id = action.id
             and verification.passed
            join report_assignments assignment
              on assignment.id = action.assignment_id
             and assignment.report_id = report.id
            left join lateral (
              select audit.metadata
              from audit_log audit
              where audit.report_id = report.id
                and audit.event = 'submit_evidence'
                and audit.metadata ->> 'corrective_action_id' = action.id::text
                and audit.created_at <= verification.created_at
              order by audit.created_at desc, audit.id desc
              limit 1
            ) accepted_submission on true
            where report.id = $1
              and report.status = 'verified_closed'::report_status
            """,
            report_id,
        )
        if row is None:
            return None
        evidence = await conn.fetch(
            """
            with accepted_submission as (
              select audit.metadata
              from audit_log audit
              join closure_receipts receipt on receipt.report_id = audit.report_id
              join verifications verification on verification.id = receipt.verification_id
              where audit.report_id = $1
                and audit.event = 'submit_evidence'
                and audit.metadata ->> 'corrective_action_id' = ($2::uuid)::text
                and audit.created_at <= verification.created_at
              order by audit.created_at desc, audit.id desc
              limit 1
            ), accepted_media as (
              select media_ref.value as media_id, media_ref.ordinality
              from accepted_submission
              cross join lateral jsonb_array_elements_text(
                case
                  when jsonb_typeof(metadata -> 'media_ids') = 'array'
                    then metadata -> 'media_ids'
                  else '[]'::jsonb
                end
              ) with ordinality as media_ref(value, ordinality)
            )
            select media.caption
            from accepted_media
            join report_media media on media.id::text = accepted_media.media_id
            where media.report_id = $1
              and media.corrective_action_id = $2::uuid
              and media.phase = 'evidence'::media_phase
            order by accepted_media.ordinality
            """,
            report_id,
            row["corrective_action_id"],
        )
        profile_ids = [
            row["reporter_id"],
            row["verified_by_id"],
            row["assignee_id"],
        ]
        name_rows = await conn.fetch(
            "select display_name from profiles where id = any($1::uuid[])",
            profile_ids,
        )

    names = [str(name["display_name"]) for name in name_rows]
    evidence_captions = [
        redacted
        for item in evidence
        if isinstance(item["caption"], str)
        and (redacted := _redact_names(item["caption"], names))
    ]
    corrective_action = _redact_names(str(row["action_text"]), names)
    verification_notes = _redact_names(str(row["verification_notes"]), names)
    if not corrective_action or not verification_notes:
        raise RuntimeError("verified closure lost required lesson material")
    case: VerifiedCase = {
        "corrective_action": corrective_action,
        "completed_note": _redact_names(
            cast(str | None, row["completed_note"]),
            names,
        ),
        "verification_notes": verification_notes,
        "verification_checklist": _decoded_json(row["checklist"]),
        "target_activity": _redact_names(cast(str | None, row["activity"]), names),
        "target_location": _redact_names(
            cast(str | None, row["location_text"]),
            names,
        ),
        "evidence_captions": evidence_captions,
        "evidence_count": len(evidence),
    }
    query_parts = [
        case["corrective_action"],
        case["completed_note"] or "",
        case["verification_notes"],
        case["target_activity"] or "",
        case["target_location"] or "",
        *case["evidence_captions"],
    ]
    state: LessonState = {
        "report_id": str(report_id),
        "verified_case": case,
        "retrieved_chunks": [],
        "case_summary": [],
        "procedure_sources": [],
        "briefing_en_sections": [],
        "briefing_zh_cn_sections": [],
        "briefing_en": "",
        "briefing_zh_cn": "",
        "quiz_questions": [],
    }
    return _LoadedLesson(
        state,
        "\n".join(part.strip() for part in query_parts if part.strip()),
    )


async def _invoke_graph(loaded: _LoadedLesson) -> LessonState:
    hits = await retrieve_chunks(loaded.retrieval_query)
    procedures: list[LessonProcedure] = [
        {
            "content": hit.content,
            "document_id": str(hit.document_id),
            "doc_ref": hit.doc_ref,
            "revision": hit.revision,
            "section": hit.section,
            "page": hit.page,
            "similarity": hit.similarity,
        }
        for hit in hits
    ]
    graph_state: LessonState = {**loaded.state, "retrieved_chunks": procedures}
    result = await lesson_graph.ainvoke(graph_state)
    if type(result) is not dict:
        raise TypeError("lesson graph must return a plain dict")
    return cast(LessonState, result)


def _locale_map(value: LocalisedText) -> dict[str, str]:
    english = value["en"].strip()
    chinese = value["zh_cn"].strip()
    if not english or not chinese:
        raise ValueError("lesson locale map is incomplete")
    return {"en": english, "zh-CN": chinese}


def _validate_result(result: LessonState) -> list[QuizQuestion]:
    if not result["briefing_en"].strip() or not result["briefing_zh_cn"].strip():
        raise ValueError("lesson briefing is missing a locale")
    questions = result["quiz_questions"]
    if len(questions) != 3:
        raise ValueError("lesson must contain exactly three questions")
    for question in questions:
        _locale_map(question["question"])
        _locale_map(question["explanation"])
        if len(question["options"]) != 4:
            raise ValueError("lesson question must contain exactly four options")
        for option in question["options"]:
            _locale_map(option)
        if not 0 <= question["correct_option"] < 4:
            raise ValueError("lesson correct option is outside the option list")
    return questions


async def _persist_lesson(
    report_id: UUID,
    result: LessonState,
) -> bool:
    questions = _validate_result(result)
    body = {
        "en": result["briefing_en"].strip(),
        "zh-CN": result["briefing_zh_cn"].strip(),
    }
    case = result["verified_case"]
    async with connection() as conn:
        async with conn.transaction():
            status = await conn.fetchval(
                "select status::text from reports where id = $1 for update",
                report_id,
            )
            if status != ReportStatus.VERIFIED_CLOSED.value:
                return False
            version = await conn.fetchval(
                """
                select coalesce(max(version), 0) + 1
                from briefings
                where report_id = $1
                """,
                report_id,
            )
            if not isinstance(version, int):
                raise RuntimeError("database returned an invalid briefing version")
            briefing = await conn.fetchrow(
                """
                insert into briefings (
                  report_id, version, body, status,
                  target_activity, target_location
                )
                values ($1, $2, $3::jsonb, 'draft'::briefing_status, $4, $5)
                returning *
                """,
                report_id,
                version,
                json.dumps(body, ensure_ascii=False),
                case["target_activity"],
                case["target_location"],
            )
            if briefing is None:
                raise RuntimeError("database did not return the briefing draft")
            await conn.executemany(
                """
                insert into quiz_questions (
                  briefing_id, position, question, explanation,
                  options, correct_option
                )
                values ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6)
                """,
                [
                    (
                        briefing["id"],
                        position,
                        json.dumps(_locale_map(question["question"]), ensure_ascii=False),
                        json.dumps(
                            _locale_map(question["explanation"]),
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            [_locale_map(option) for option in question["options"]],
                            ensure_ascii=False,
                        ),
                        question["correct_option"],
                    )
                    for position, question in enumerate(questions, start=1)
                ],
            )
            await transition_report(
                report_id,
                ReportStatus.LESSON_DRAFTED,
                Actor.ai(),
                metadata={
                    "briefing_id": str(briefing["id"]),
                    "briefing_version": version,
                    "quiz_question_count": len(questions),
                },
                transaction_connection=conn,
            )
            return True


async def run_lesson(report_id: UUID, request_id: str | None = None) -> bool:
    """Run after closure and fail closed without advancing an incomplete lesson."""
    started = perf_counter()
    run_status = await _record_lesson_run(
        report_id,
        "running",
        started_at=datetime.now(UTC).isoformat(),
    )
    provider_hint = get_settings().ai_provider.strip().lower() or "unconfigured"
    with bind_request_id(request_id) as run_request_id:
        with capture_ai_usage() as usage:
            try:
                loaded = await _load_lesson(report_id)
                if loaded is None:
                    log_event(
                        logger,
                        logging.INFO,
                        "ai_run_completed",
                        report_id=str(report_id),
                        graph="lesson",
                        latency_ms=round((perf_counter() - started) * 1000, 3),
                        validation_result="not_run",
                        outcome="skipped",
                        **usage.snapshot().as_log_fields(
                            fallback_provider=provider_hint
                        ),
                    )
                    await _record_lesson_run(
                        report_id,
                        "failed",
                        started_at=run_status.started_at,
                        finished=True,
                    )
                    return False
                loaded = _LoadedLesson(
                    cast(
                        LessonState,
                        {**loaded.state, "request_id": run_request_id},
                    ),
                    loaded.retrieval_query,
                )
                result = await _invoke_graph(loaded)
                persisted = await _persist_lesson(report_id, result)
                log_event(
                    logger,
                    logging.INFO,
                    "ai_run_completed",
                    report_id=str(report_id),
                    graph="lesson",
                    latency_ms=round((perf_counter() - started) * 1000, 3),
                    validation_result="valid" if persisted else "not_persisted",
                    outcome="persisted" if persisted else "stale",
                    **usage.snapshot().as_log_fields(
                        fallback_provider=provider_hint
                    ),
                )
                await _record_lesson_run(
                    report_id,
                    "succeeded" if persisted else "failed",
                    started_at=run_status.started_at,
                    finished=True,
                )
                return persisted
            except Exception as error:
                track_exception(
                    logger,
                    "ai_run_failed",
                    error,
                    report_id=str(report_id),
                    graph="lesson",
                    latency_ms=round((perf_counter() - started) * 1000, 3),
                    validation_result="failed",
                    outcome="failed",
                    **usage.snapshot().as_log_fields(
                        fallback_provider=provider_hint
                    ),
                )
                await _record_lesson_run(
                    report_id,
                    "failed",
                    started_at=run_status.started_at,
                    finished=True,
                )
                return False
