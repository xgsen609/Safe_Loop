"""Prove report reads expose only server-authorised actions and timelines."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import UUID

from fastapi import BackgroundTasks, HTTPException
import pytest

from app.api import reports as reports_api
from app.api.reports import AssignmentRequest, ReviewRequest, TransitionRequest
from app.domain.enums import ActorType, CaseRole, InputMode, ReportStatus, ReviewDecision, Role
from app.domain.transitions import TRANSITIONS
from app.services.report_service import Actor, input_mode_for_submission
from app.services.review_service import ReviewError, ReviewResult, review_report

REPORT_ID = UUID("10000000-0000-0000-0000-000000000001")
REPORTER_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_REPORTER_ID = UUID("00000000-0000-0000-0000-000000000002")
REVIEWER_ID = UUID("00000000-0000-0000-0000-000000000003")
RESPONSIBLE_ID = UUID("00000000-0000-0000-0000-000000000004")


def configure_report_read(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: str = "under_review",
) -> None:
    async def fake_report(_: UUID) -> dict[str, object]:
        return {
            "id": REPORT_ID,
            "reporter_id": REPORTER_ID,
            "status": status,
        }

    async def fake_media(_: UUID) -> list[dict[str, object]]:
        return []

    async def fake_clarifications(_: UUID) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(reports_api, "get_report", fake_report)
    monkeypatch.setattr(reports_api, "get_signed_report_media", fake_media)
    monkeypatch.setattr(
        reports_api,
        "list_report_clarifications",
        fake_clarifications,
    )


def test_available_transitions_differ_without_client_role_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_report_read(monkeypatch)

    reporter_result = asyncio.run(
        reports_api.report_detail(
            REPORT_ID,
            Actor(ActorType.HUMAN, REPORTER_ID, Role.REPORTER),
        )
    )
    reviewer_result = asyncio.run(
        reports_api.report_detail(
            REPORT_ID,
            Actor(ActorType.HUMAN, REVIEWER_ID, Role.REVIEWER),
        )
    )

    assert reporter_result["available_transitions"] == []
    assert reporter_result["can_answer_clarifications"] is False
    assert reporter_result["latest_draft"] is None
    assert reporter_result["closure_receipt"] is None
    assert reviewer_result["latest_draft"] is None
    assert reviewer_result["closure_receipt"] is None
    assert reviewer_result["can_answer_clarifications"] is False
    assert reviewer_result["available_transitions"] == [
        {
            "event": "reject",
            "target": "rejected",
            "requires_reason": True,
            "review_decision": "reject",
        },
        {
            "event": "request_info",
            "target": "info_requested",
            "requires_reason": True,
            "review_decision": "request_info",
        },
        {
            "event": "escalate",
            "target": "escalated",
            "requires_reason": True,
            "review_decision": "escalate",
        },
        {
            "event": "approve_action",
            "target": "action_assigned",
            "requires_reason": False,
            "review_decision": "approve",
        },
    ]


def test_only_the_owning_reporter_can_answer_clarifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_report_read(monkeypatch, status="clarifying")

    owner_result = asyncio.run(
        reports_api.report_detail(
            REPORT_ID,
            Actor(ActorType.HUMAN, REPORTER_ID, Role.REPORTER),
        )
    )
    reviewer_result = asyncio.run(
        reports_api.report_detail(
            REPORT_ID,
            Actor(ActorType.HUMAN, REVIEWER_ID, Role.REVIEWER),
        )
    )

    assert owner_result["can_answer_clarifications"] is True
    assert reviewer_result["can_answer_clarifications"] is False


def test_report_detail_decodes_latest_draft_and_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_report(_: UUID) -> dict[str, object]:
        return {
            "id": REPORT_ID,
            "reporter_id": REPORTER_ID,
            "status": "under_review",
            "latest_draft": json.dumps(
                {
                    "version": 2,
                    "observed_facts": ["The Level 6 edge has no guardrail."],
                    "assumptions": ["The formwork crew owns the area."],
                    "missing_information": ["Work schedule below"],
                    "proposed_category": "work_at_height",
                    "proposed_urgency": "high",
                    "suggested_action": None,
                    "validation": "invalid",
                    "validation_errors": ["confidence_below_threshold"],
                }
            ),
            "current_action": json.dumps(
                {
                    "id": "50000000-0000-0000-0000-000000000001",
                    "rework_count": 2,
                    "status": "submitted",
                }
            ),
            "verifications": json.dumps(
                [
                    {
                        "id": "60000000-0000-0000-0000-000000000001",
                        "passed": False,
                        "reason": "The lower anchor still moves.",
                    }
                ]
            ),
            "closure_receipt": json.dumps(
                {
                    "id": "70000000-0000-0000-0000-000000000001",
                    "verification_id": "60000000-0000-0000-0000-000000000001",
                    "action_text": "Secure the lower anchor.",
                    "verification_notes": "The anchor held under load.",
                    "before_media_id": None,
                    "after_media_id": None,
                }
            ),
        }

    async def empty_rows(_: UUID) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(reports_api, "get_report", fake_report)
    monkeypatch.setattr(reports_api, "get_signed_report_media", empty_rows)
    monkeypatch.setattr(reports_api, "list_report_clarifications", empty_rows)

    result = asyncio.run(
        reports_api.report_detail(
            REPORT_ID,
            Actor(ActorType.HUMAN, REVIEWER_ID, Role.REVIEWER),
        )
    )

    draft = result["latest_draft"]
    assert isinstance(draft, dict)
    assert draft["version"] == 2
    assert draft["validation_errors"] == ["confidence_below_threshold"]
    action = result["current_action"]
    assert isinstance(action, dict)
    assert action["rework_count"] == 2
    verifications = result["verifications"]
    assert isinstance(verifications, list)
    assert verifications[0]["reason"] == "The lower anchor still moves."
    receipt = result["closure_receipt"]
    assert isinstance(receipt, dict)
    assert receipt["verification_notes"] == "The anchor held under load."


def test_review_endpoint_passes_the_atomic_payload_to_the_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_review(report_id: UUID, actor: Actor, **values: object) -> ReviewResult:
        captured.update({"report_id": report_id, "actor": actor, **values})
        return ReviewResult(
            review={"id": UUID("20000000-0000-0000-0000-000000000001")},  # type: ignore[arg-type]
            report={"id": report_id, "status": "info_requested"},  # type: ignore[arg-type]
            assignment_id=None,
            corrective_action_id=None,
        )

    monkeypatch.setattr(reports_api, "review_report", fake_review)
    actor = Actor(ActorType.HUMAN, REVIEWER_ID, Role.REVIEWER)
    payload = ReviewRequest(
        decision=ReviewDecision.REQUEST_INFO,
        target=ReportStatus.INFO_REQUESTED,
        reason="Confirm the exclusion zone.",
        corrected_category="edge protection",
        correction_reason="Category was too broad.",
    )

    result = asyncio.run(reports_api.post_review(REPORT_ID, payload, actor))

    assert result == {
        "review_id": "20000000-0000-0000-0000-000000000001",
        "report_id": str(REPORT_ID),
        "status": "info_requested",
        "assignment_id": None,
        "corrective_action_id": None,
    }
    assert captured["report_id"] == REPORT_ID
    assert captured["actor"] == actor
    assert captured["decision"] is ReviewDecision.REQUEST_INFO
    assert captured["target"] is ReportStatus.INFO_REQUESTED
    assert captured["reason"] == "Confirm the exclusion zone."
    assert captured["corrected_category"] == "edge protection"
    assert captured["correction_reason"] == "Category was too broad."


def test_assignment_endpoint_approves_and_assigns_in_one_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    assignment_id = UUID("40000000-0000-0000-0000-000000000001")
    action_id = UUID("50000000-0000-0000-0000-000000000001")

    async def fake_review(report_id: UUID, actor: Actor, **values: object) -> ReviewResult:
        captured.update({"report_id": report_id, "actor": actor, **values})
        return ReviewResult(
            review={"id": UUID("20000000-0000-0000-0000-000000000002")},  # type: ignore[arg-type]
            report={"id": report_id, "status": "action_assigned"},  # type: ignore[arg-type]
            assignment_id=assignment_id,
            corrective_action_id=action_id,
        )

    monkeypatch.setattr(reports_api, "review_report", fake_review)
    actor = Actor(ActorType.HUMAN, REVIEWER_ID, Role.REVIEWER)
    due_at = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)

    result = asyncio.run(
        reports_api.post_assignment(
            REPORT_ID,
            AssignmentRequest(
                assignee_id=RESPONSIBLE_ID,
                case_role=CaseRole.RESPONSIBLE,
                due_at=due_at,
            ),
            actor,
        )
    )

    assert result["status"] == "action_assigned"
    assert result["assignment_id"] == str(assignment_id)
    assert result["corrective_action_id"] == str(action_id)
    assert captured == {
        "report_id": REPORT_ID,
        "actor": actor,
        "decision": ReviewDecision.APPROVE,
        "target": ReportStatus.ACTION_ASSIGNED,
        "assignee_id": RESPONSIBLE_ID,
        "case_role": CaseRole.RESPONSIBLE,
        "due_at": due_at,
    }


def test_assignment_service_rejects_a_non_reviewer_before_database_access() -> None:
    with pytest.raises(ReviewError) as error:
        asyncio.run(
            review_report(
                REPORT_ID,
                Actor(ActorType.HUMAN, REPORTER_ID, Role.REPORTER),
                decision=ReviewDecision.APPROVE,
                target=ReportStatus.ACTION_ASSIGNED,
                assignee_id=RESPONSIBLE_ID,
                case_role=CaseRole.RESPONSIBLE,
                due_at=datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc),
            )
        )

    assert error.value.code == "review_actor_not_permitted"


def test_approval_without_assignee_returns_the_machine_422_contract() -> None:
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            reports_api.post_assignment(
                REPORT_ID,
                AssignmentRequest(
                    case_role=CaseRole.RESPONSIBLE,
                    due_at=datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc),
                ),
                Actor(ActorType.HUMAN, REVIEWER_ID, Role.REVIEWER),
            )
        )

    assert error.value.status_code == 422
    assert error.value.detail == {
        "code": "assignment_required",
        "message": "approval requires an assignee, due date, and action",
    }


def test_timeline_read_uses_the_same_report_authorisation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_report_read(monkeypatch, status="submitted")

    async def fake_timeline(_: UUID) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(reports_api, "get_timeline", fake_timeline)

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            reports_api.report_timeline(
                REPORT_ID,
                Actor(ActorType.HUMAN, OTHER_REPORTER_ID, Role.REPORTER),
            )
        )

    assert error.value.status_code == 403
    assert error.value.detail["code"] == "report_forbidden"


def test_transition_endpoint_refuses_a_different_reporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_report_read(monkeypatch, status="draft")

    async def must_not_transition(*_: object, **__: object) -> None:
        raise AssertionError("unauthorised transition reached the status writer")

    monkeypatch.setattr(reports_api, "transition_report", must_not_transition)

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            reports_api.post_transition(
                REPORT_ID,
                TransitionRequest(target=ReportStatus.SUBMITTED),
                BackgroundTasks(),
                Actor(ActorType.HUMAN, OTHER_REPORTER_ID, Role.REPORTER),
            )
        )

    assert error.value.status_code == 403
    assert error.value.detail["code"] == "report_forbidden"


def test_successful_submission_schedules_intake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_report_read(monkeypatch, status="draft")

    captured: dict[str, object] = {}

    async def fake_submit(report_id: UUID, actor: Actor, **values: object) -> dict[str, object]:
        captured.update({"report_id": report_id, "actor": actor, **values})
        return {"id": REPORT_ID, "status": "submitted"}

    monkeypatch.setattr(reports_api, "submit_report", fake_submit)
    monkeypatch.setattr(
        reports_api,
        "current_request_id",
        lambda: "request-submit",
    )
    background_tasks = BackgroundTasks()

    asyncio.run(
        reports_api.post_transition(
            REPORT_ID,
            TransitionRequest(
                target=ReportStatus.SUBMITTED,
                confirmed_text="Confirmed hazard description",
            ),
            background_tasks,
            Actor(ActorType.HUMAN, REPORTER_ID, Role.REPORTER),
        )
    )

    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].func is reports_api.run_intake
    assert background_tasks.tasks[0].args == (REPORT_ID, "request-submit")
    assert captured["confirmed_text"] == "Confirmed hazard description"
    assert captured["transcript_id"] is None
    assert captured["audio_media_id"] is None


def test_intake_retry_runs_in_request_and_returns_advanced_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads = 0

    async def fake_report(_: UUID) -> dict[str, object]:
        nonlocal reads
        reads += 1
        return {
            "id": REPORT_ID,
            "reporter_id": REPORTER_ID,
            "status": "submitted" if reads == 1 else "clarifying",
        }

    async def fake_rate_limit(**_: object) -> None:
        return None

    async def fake_run_intake(report_id: UUID, request_id: str | None) -> bool:
        assert report_id == REPORT_ID
        assert request_id == "request-retry"
        return True

    monkeypatch.setattr(reports_api, "get_report", fake_report)
    monkeypatch.setattr(reports_api, "enforce_rate_limit", fake_rate_limit)
    monkeypatch.setattr(reports_api, "run_intake", fake_run_intake)
    monkeypatch.setattr(reports_api, "current_request_id", lambda: "request-retry")

    result = asyncio.run(
        reports_api.post_intake_retry(
            REPORT_ID,
            Actor(ActorType.HUMAN, REPORTER_ID, Role.REPORTER),
        )
    )

    assert result == {"report_id": str(REPORT_ID), "status": "clarifying"}


def test_intake_retry_reports_a_visible_failure_when_status_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_report_read(monkeypatch, status="submitted")

    async def fake_rate_limit(**_: object) -> None:
        return None

    async def failed_intake(_: UUID, __: str | None) -> bool:
        return False

    monkeypatch.setattr(reports_api, "enforce_rate_limit", fake_rate_limit)
    monkeypatch.setattr(reports_api, "run_intake", failed_intake)

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            reports_api.post_intake_retry(
                REPORT_ID,
                Actor(ActorType.HUMAN, REPORTER_ID, Role.REPORTER),
            )
        )

    assert error.value.status_code == 503
    assert error.value.detail["code"] == "intake_retry_failed"


@pytest.mark.parametrize(
    ("confirmed_text", "raw_transcript", "expected"),
    [
        ("Typed report", None, InputMode.TYPED),
        ("六楼边缘没有护栏", "六楼边缘没有护栏", InputMode.VOICE),
        ("六楼边缘没有防护栏", "六楼边缘没有护栏", InputMode.VOICE_EDITED),
    ],
)
def test_input_mode_is_computed_from_server_owned_transcript_evidence(
    confirmed_text: str,
    raw_transcript: str | None,
    expected: InputMode,
) -> None:
    assert input_mode_for_submission(confirmed_text, raw_transcript) is expected


def test_answer_endpoint_schedules_intake_after_round_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clarification_id = UUID("30000000-0000-0000-0000-000000000001")

    class StoredAnswer:
        clarification = {
            "id": clarification_id,
            "report_id": REPORT_ID,
            "answered_at": None,
        }
        rerun = True

    captured: list[object] = []

    async def fake_answer(*values: object, **__: object) -> StoredAnswer:
        captured.extend(values)
        return StoredAnswer()

    monkeypatch.setattr(reports_api, "answer_clarification", fake_answer)
    monkeypatch.setattr(
        reports_api,
        "current_request_id",
        lambda: "request-clarification",
    )
    background_tasks = BackgroundTasks()

    result = asyncio.run(
        reports_api.post_clarification_answer(
            REPORT_ID,
            clarification_id,
            reports_api.ClarificationAnswerRequest(
                answer="Level 6 east edge",
                transcript_id=UUID("70000000-0000-0000-0000-000000000001"),
            ),
            background_tasks,
            Actor(ActorType.HUMAN, REPORTER_ID, Role.REPORTER),
        )
    )

    assert result["id"] == str(clarification_id)
    assert result["round_complete"] is True
    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].func is reports_api.run_intake
    assert background_tasks.tasks[0].args == (REPORT_ID, "request-clarification")
    assert captured[-1] == UUID("70000000-0000-0000-0000-000000000001")


def test_every_state_machine_event_has_action_and_timeline_catalogue_keys() -> None:
    repository = Path(__file__).resolve().parents[2]
    for locale in ("en", "zh-CN"):
        messages = json.loads(
            (repository / "frontend" / "messages" / f"{locale}.json").read_text(
                encoding="utf-8"
            )
        )
        assert "timeline.event.create_report" in messages
        for status in ReportStatus:
            assert f"report.detail.waiting.{status.value}" in messages
        for actor_type in ActorType:
            assert f"timeline.actor.{actor_type.value}" in messages
        for role in Role:
            assert f"timeline.actor.{role.value}" in messages
        for transition in TRANSITIONS:
            assert f"action.{transition.event}" in messages
            assert f"timeline.event.{transition.event}" in messages
