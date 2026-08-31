"""Prove one stub lesson run is bilingual, sourced, anonymous, and quiz-complete."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from app.ai import lesson_graph as lesson_module
from app.ai.lesson_graph import (
    MAX_BRIEFING_EN_WORDS,
    MAX_BRIEFING_ZH_CHARACTERS,
    LessonState,
    QuizResult,
    lesson_graph,
    summarise_case,
)
from app.ai.provider import ProviderResult


def lesson_state() -> LessonState:
    return {
        "report_id": "10000000-0000-0000-0000-000000000001",
        "verified_case": {
            "corrective_action": (
                "Mr Daniel Tan installed the missing guardrail and secured both anchors."
            ),
            "completed_note": "The upper and lower anchors were tightened.",
            "verification_notes": "Both anchors passed the final pull test.",
            "verification_checklist": {"hazard_removed": True},
            "target_activity": "Formwork",
            "target_location": "Level 6 east edge",
            "evidence_captions": ["Completed guardrail at the east edge"],
            "evidence_count": 1,
        },
        "retrieved_chunks": [
            {
                "content": (
                    "Workers must install guardrails before work starts. "
                    "Inspect every anchor before use."
                ),
                "document_id": "20000000-0000-0000-0000-000000000001",
                "doc_ref": "WAH-001",
                "revision": "3",
                "section": "4.2",
                "page": 7,
                "similarity": 0.92,
            },
            {
                "content": "高处作业前必须检查防护栏。防护栏必须牢固。",
                "document_id": "20000000-0000-0000-0000-000000000002",
                "doc_ref": "高处-001",
                "revision": "2",
                "section": "4.2",
                "page": 8,
                "similarity": 0.9,
            },
        ],
        "case_summary": [],
        "procedure_sources": [],
        "briefing_en_sections": [],
        "briefing_zh_cn_sections": [],
        "briefing_en": "",
        "briefing_zh_cn": "",
        "quiz_questions": [],
    }


def test_lesson_graph_produces_both_locales_and_exactly_three_questions() -> None:
    result = asyncio.run(lesson_graph.ainvoke(lesson_state()))
    assert type(result) is dict
    output = cast(LessonState, result)

    assert output["briefing_en"].strip()
    assert output["briefing_zh_cn"].strip()
    assert len(output["briefing_en"].split()) <= MAX_BRIEFING_EN_WORDS
    assert len(output["briefing_zh_cn"]) <= MAX_BRIEFING_ZH_CHARACTERS
    assert "防护栏" in output["briefing_zh_cn"]
    assert "Daniel" not in output["briefing_en"]
    assert "Daniel" not in output["briefing_zh_cn"]

    assert len(output["quiz_questions"]) == 3
    for question in output["quiz_questions"]:
        assert question["question"]["en"].strip()
        assert question["question"]["zh_cn"].strip()
        assert question["explanation"]["en"].strip()
        assert question["explanation"]["zh_cn"].strip()
        assert len(question["options"]) == 4
        assert all(option["en"].strip() for option in question["options"])
        assert all(option["zh_cn"].strip() for option in question["options"])
        assert 0 <= question["correct_option"] < 4
        assert question["source_refs"]


def test_lesson_graph_returns_plain_serialisable_state() -> None:
    result = asyncio.run(lesson_graph.ainvoke(lesson_state()))

    assert type(result) is dict
    assert isinstance(result["case_summary"], list)
    assert isinstance(result["procedure_sources"], list)
    assert isinstance(result["quiz_questions"], list)


def test_case_summary_falls_back_to_exact_verified_wording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ParaphrasingProvider:
        async def complete(self, *_: object, **__: object) -> ProviderResult:
            return ProviderResult(
                data={
                    "points": [
                        {
                            "text": "A paraphrased corrective action.",
                            "source_ref": "case:corrective_action",
                            "quote": "A quote that is not in the source.",
                        },
                        {
                            "text": "A paraphrased verification.",
                            "source_ref": "case:verification_notes",
                            "quote": "Another unsupported quote.",
                        },
                    ]
                },
                raw="{}",
                provider="test",
                provider_ref="test-ref",
                latency_ms=1,
                tokens_in=1,
                tokens_out=1,
                cost_usd=0,
            )

    monkeypatch.setattr(lesson_module, "get_provider", lambda: ParaphrasingProvider())

    result = asyncio.run(summarise_case(lesson_state()))
    points = cast(list[dict[str, str]], result["case_summary"])

    assert points[0]["quote"] == "a worker installed the missing guardrail and secured both anchors."
    assert points[1]["quote"] == "Both anchors passed the final pull test."


def test_quiz_schema_uses_vertex_compatible_inclusive_bounds() -> None:
    schema = QuizResult.model_json_schema()
    correct_option = schema["$defs"]["QuizQuestionResult"]["properties"]["correct_option"]

    assert correct_option["minimum"] == 0
    assert correct_option["maximum"] == 3
    assert "exclusiveMaximum" not in correct_option


def test_lesson_graph_uses_verified_fixtures_when_provider_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnavailableProvider:
        async def complete(self, *_: object, **__: object) -> ProviderResult:
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(lesson_module, "get_provider", lambda: UnavailableProvider())

    result = cast(LessonState, asyncio.run(lesson_graph.ainvoke(lesson_state())))

    assert result["briefing_en"].strip()
    assert result["briefing_zh_cn"].strip()
    assert len(result["quiz_questions"]) == 3
