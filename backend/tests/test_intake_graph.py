"""Prove the first intake graph preserves language and separates inference from fact."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from typing import Literal, cast

import pytest

from app.ai import intake_graph as intake_module
from app.ai.intake_graph import IntakeState, compose_questions, intake_graph, translate
from app.ai.provider import ProviderResult
from app.ai.validator import validate_draft
from app.domain.enums import ValidationStatus


def state(
    description: str,
    *,
    locale: Literal["en", "zh-CN"] = "en",
    preferred_locale: Literal["en", "zh-CN"] | None = None,
    location: str | None = "Level 6 edge",
    activity: str | None = "Formwork",
) -> IntakeState:
    return {
        "report_id": "10000000-0000-0000-0000-000000000001",
        "lang_original": locale,
        "preferred_lang": preferred_locale or locale,
        "description_original": description,
        "description_en": None,
        "location": location,
        "activity": activity,
        "prior_answers": [],
        "round": 0,
        "observed_facts": [],
        "assumptions": [],
        "missing_information": [],
        "questions": [],
        "retrieved_chunks": [],
        "draft": None,
    }


def run(input_state: IntakeState) -> dict[str, object]:
    result = asyncio.run(intake_graph.ainvoke(dict(input_state)))
    assert type(result) is dict
    return cast(dict[str, object], result)


def test_complete_mandarin_report_has_english_and_no_gaps() -> None:
    original = "六楼模板边缘没有护栏，工人正在附近搬运材料。"
    result = run(state(original, locale="zh-CN", activity="Moving materials"))

    assert result["description_original"] == original
    assert isinstance(result["description_en"], str)
    assert result["description_en"] != original
    assert result["missing_information"] == []
    draft = cast(dict[str, object], result["draft"])
    raw_payload = cast(dict[str, object], json.loads(cast(str, draft["raw"])))
    assert raw_payload["observed_facts"] == draft["observed_facts"]
    assert raw_payload["assumptions"] == draft["assumptions"]
    assert draft["citations"] == []
    assert draft["suggested_action"] is None
    assert draft["provider"] == "stub"


def test_no_retrieval_hits_produce_a_valid_draft_without_an_action() -> None:
    result = run(state("The Level 6 edge has no guardrail."))
    draft = cast(dict[str, object], result["draft"])

    assert draft["suggested_action"] is None
    assert draft["citations"] == []
    assert "approved_work_at_height_procedure" in cast(
        list[str], draft["missing_information"]
    )
    assert validate_draft(draft) == (ValidationStatus.VALID, [])


def test_retrieved_chunk_produces_a_verbatim_citation_and_action() -> None:
    input_state = state("The Level 6 edge has no guardrail.")
    source = {
        "content": "Inspect the edge. Workers must install guardrails before work starts.",
        "document_id": "20000000-0000-0000-0000-000000000001",
        "doc_ref": "WAH-001",
        "revision": "3",
        "section": "4.2",
        "page": 7,
        "similarity": 0.91,
    }
    input_state["retrieved_chunks"] = [source]

    result = run(input_state)
    draft = cast(dict[str, object], result["draft"])
    citations = cast(list[dict[str, object]], draft["citations"])

    assert draft["suggested_action"] == (
        "Workers must install guardrails before work starts."
    )
    assert citations == [
        {
            "document_id": source["document_id"],
            "doc_ref": source["doc_ref"],
            "revision": source["revision"],
            "section": source["section"],
            "page": source["page"],
            "quote": draft["suggested_action"],
        }
    ]
    assert validate_draft(
        draft,
        citation_sources=[source],
    ) == (ValidationStatus.VALID, [])


def test_vague_report_has_decision_changing_gaps() -> None:
    result = run(state("Unsafe", location=None, activity=None))

    assert result["missing_information"] == ["hazard_detail", "location", "activity"]
    questions = cast(list[dict[str, str]], result["questions"])
    assert len(questions) == 2
    assert [question["gap"] for question in questions] == ["hazard_detail", "location"]


def test_questions_use_the_reporters_preferred_language() -> None:
    result = run(
        state(
            "Unsafe",
            preferred_locale="zh-CN",
            location=None,
            activity=None,
        )
    )
    questions = cast(list[dict[str, str]], result["questions"])

    assert len(questions) == 2
    assert all(
        any("\u4e00" <= character <= "\u9fff" for character in question["text"])
        for question in questions
    )
    assert all(question["gap"] in result["missing_information"] for question in questions)


def test_model_question_labels_are_mapped_to_known_unresolved_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RewordingProvider:
        async def complete(self, *_: object, **__: object) -> ProviderResult:
            return ProviderResult(
                data={
                    "questions": [
                        {
                            "gap": "panel_power_status",
                            "text": "Is the electric panel energized or isolated?",
                        },
                        {
                            "gap": "water_contact",
                            "text": "Is water touching electrical wiring?",
                        },
                    ]
                },
                raw="{}",
                provider="test",
                provider_ref="test-ref",
                latency_ms=1,
                tokens_in=1,
                tokens_out=1,
                cost_usd=0.0,
            )

    input_state = state("Water is leaking beside an electric panel.")
    input_state["missing_information"] = [
        "Whether the electric panel is energized or isolated.",
        "Whether water is touching electrical wiring.",
    ]
    monkeypatch.setattr(intake_module, "get_provider", lambda: RewordingProvider())

    update = asyncio.run(compose_questions(input_state))

    assert [question["gap"] for question in update["questions"]] == input_state[
        "missing_information"
    ]


def test_round_cap_skips_questions_but_keeps_unanswered_gaps() -> None:
    input_state = state("Unsafe", location=None, activity=None)
    input_state["round"] = 2

    result = run(input_state)

    assert result["questions"] == []
    assert result["missing_information"] == ["hazard_detail", "location", "activity"]
    draft = cast(dict[str, object], result["draft"])
    draft_gaps = cast(list[str], draft["missing_information"])
    assert draft_gaps[:3] == ["hazard_detail", "location", "activity"]
    assert "approved_site_safety_procedure" in draft_gaps


def test_two_prior_answers_exhaust_the_total_question_budget() -> None:
    input_state = state("Unsafe", location=None, activity=None)
    input_state["round"] = 1
    input_state["prior_answers"] = [
        {
            "gap": "hazard_detail",
            "question": "What exactly is unsafe?",
            "answer": "Loose materials",
        },
        {
            "gap": "location",
            "question": "Where exactly is the hazard?",
            "answer": "Level 6",
        },
    ]

    result = run(input_state)

    assert result["questions"] == []
    assert result["missing_information"] == ["activity"]


def test_inference_is_an_assumption_and_never_an_observed_fact() -> None:
    result = run(
        state("The guardrail is missing. The worker was careless.")
    )
    observed = cast(list[str], result["observed_facts"])
    assumptions = cast(list[str], result["assumptions"])

    assert any("careless" in item.casefold() for item in assumptions)
    assert all("careless" not in item.casefold() for item in observed)
    assert any("guardrail" in item.casefold() for item in observed)
    draft = cast(dict[str, object], result["draft"])
    assert draft["observed_facts"] == observed
    assert draft["assumptions"] == assumptions


def test_code_switched_trade_terms_survive_translation() -> None:
    original = "六楼 formwork 边缘没有 guardrail。"
    result = run(state(original, locale="zh-CN"))
    translated = cast(str, result["description_en"])

    assert "formwork" in translated
    assert "guardrail" in translated
    assert result["description_original"] == original


def test_english_translation_is_a_no_op_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def provider_must_not_be_loaded() -> None:
        raise AssertionError("provider should not be loaded for English")

    monkeypatch.setattr(intake_module, "get_provider", provider_must_not_be_loaded)
    input_state = state("The guardrail is missing.")
    update = asyncio.run(translate(input_state))

    assert update == {"description_en": input_state["description_original"]}


def test_ai_package_never_imports_asyncpg() -> None:
    ai_directory = Path(intake_module.__file__).parent
    offenders: list[str] = []
    for path in ai_directory.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                imported.name == "asyncpg" or imported.name.startswith("asyncpg.")
                for imported in node.names
            ):
                offenders.append(str(path))
            if isinstance(node, ast.ImportFrom) and node.module and (
                node.module == "asyncpg" or node.module.startswith("asyncpg.")
            ):
                offenders.append(str(path))
    assert offenders == []
