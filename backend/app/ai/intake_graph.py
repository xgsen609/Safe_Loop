"""Run the first pure intake stages without persisting or advancing a report."""

from __future__ import annotations

import re
from typing import Final, Literal, NotRequired, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from app.ai.provider import JsonValue, get_provider
from app.domain.enums import Role, Urgency


class PriorAnswer(TypedDict):
    """Keep prior human clarification text JSON-serialisable."""

    gap: str
    question: str
    answer: str


class IntakeQuestion(TypedDict):
    """Tie reporter-facing clarification text to one decision-changing gap."""

    gap: str
    text: str


class RetrievedProcedure(TypedDict):
    """Carry approved chunk text into the database-pure graph as plain data."""

    content: str
    document_id: str
    doc_ref: str
    revision: str
    section: str | None
    page: int | None
    similarity: float


class DraftCitation(TypedDict):
    """Keep one recommendation traceable to an exact corpus coordinate."""

    document_id: str
    doc_ref: str
    revision: str
    section: str | None
    page: int | None
    quote: str


class DraftPayload(TypedDict):
    """Keep model claims explicit and directly mappable to the append-only row."""

    observed_facts: list[str]
    assumptions: list[str]
    missing_information: list[str]
    proposed_category: str | None
    proposed_urgency: str | None
    suggested_owner_role: str | None
    suggested_action: str | None
    confidence: float
    needs_escalation: bool
    escalation_reason: str | None
    citations: list[DraftCitation]


class DraftEnvelope(DraftPayload):
    """Carry structured output and the provider evidence needed for persistence."""

    raw: str
    provider: str
    provider_ref: str
    latency_ms: int
    tokens_in: int
    tokens_out: int


class IntakeState(TypedDict):
    """Carry only plain data across durable, independently restartable graph runs."""

    report_id: str
    request_id: NotRequired[str]
    lang_original: Literal["en", "zh-CN"]
    preferred_lang: Literal["en", "zh-CN"]
    description_original: str
    description_en: str | None
    location: str | None
    activity: str | None
    prior_answers: list[PriorAnswer]
    round: int
    observed_facts: list[str]
    assumptions: list[str]
    missing_information: list[str]
    questions: list[IntakeQuestion]
    retrieved_chunks: list[RetrievedProcedure]
    draft: DraftEnvelope | None


MAX_CLARIFICATION_ROUNDS: Final = 2
MAX_QUESTIONS_PER_ROUND: Final = 2
MAX_CLARIFICATION_QUESTIONS: Final = 2


def _text(variables: dict[str, object], name: str) -> str:
    value = variables.get(name)
    return value if isinstance(value, str) else ""


def _string_list(variables: dict[str, object], name: str) -> list[str]:
    value = variables.get(name)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _answered_gaps(variables: dict[str, object]) -> set[str]:
    value = variables.get("prior_answers")
    if not isinstance(value, list):
        return set()
    answered: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        gap = item.get("gap")
        answer = item.get("answer")
        if isinstance(gap, str) and isinstance(answer, str) and answer.strip():
            answered.add(gap)
    return answered


_TRANSLATION_REPLACEMENTS = (
    ("六楼", "Level 6 "),
    ("七楼", "Level 7 "),
    ("模板", "formwork "),
    ("边缘没有护栏", "edge has no guardrail "),
    ("没有护栏", "has no guardrail "),
    ("边缘", "edge "),
    ("没有", "has no "),
    ("护栏", "guardrail"),
    ("工人", "worker "),
    ("正在", "is "),
    ("附近", "nearby "),
    ("搬运材料", "moving materials"),
    ("脚手架", "scaffold"),
    ("松动", "loose"),
)


def _stub_translate(source: str) -> str:
    translated = source
    for original, replacement in _TRANSLATION_REPLACEMENTS:
        translated = translated.replace(original, replacement)
    translated = translated.replace("，", ", ").replace("。", ".")
    translated = re.sub(r"\s+([,.])", r"\1", translated)
    return re.sub(r"\s+", " ", translated).strip()


_ASSUMPTION_MARKERS = (
    "careless",
    "reckless",
    "probably",
    "likely",
    "must have",
    "seems",
    "appears",
    "疏忽",
    "粗心",
)


def _split_observation(description: str) -> tuple[list[str], list[str]]:
    segments = [
        segment.strip(" .;。；,，")
        for segment in re.split(
            r"(?<=[.;。；])\s*|\s+(?:and|but)\s+|[,，]",
            description,
            flags=re.IGNORECASE,
        )
        if segment.strip(" .;。；,，")
    ]
    observed: list[str] = []
    assumptions: list[str] = []
    for segment in segments:
        destination = (
            assumptions
            if any(marker in segment.casefold() for marker in _ASSUMPTION_MARKERS)
            else observed
        )
        destination.append(segment)
    return observed, assumptions


_SPECIFIC_HAZARD_TERMS = (
    "guardrail",
    "edge",
    "scaffold",
    "blocked",
    "loose",
    "exposed",
    "leak",
    "cable",
    "fire exit",
    "护栏",
    "边缘",
    "脚手架",
)
_LOCATION_TERMS = ("level", "floor", "zone", "tower", "block", "楼", "层", "区")
_ACTIVITY_TERMS = (
    "formwork",
    "lifting",
    "welding",
    "scaffold",
    "moving materials",
    "模板",
    "吊装",
    "焊接",
    "脚手架",
    "搬运",
)


def _stub_gaps(
    description: str,
    location: str,
    activity: str,
    observed_facts: list[str],
    answered_gaps: set[str],
) -> list[str]:
    normalised = description.casefold().strip()
    gaps: list[str] = []
    if (
        not observed_facts
        or len(normalised.split()) < 3
        or not any(term in normalised for term in _SPECIFIC_HAZARD_TERMS)
    ):
        gaps.append("hazard_detail")
    if not location.strip() and not any(term in normalised for term in _LOCATION_TERMS):
        gaps.append("location")
    if not activity.strip() and not any(term in normalised for term in _ACTIVITY_TERMS):
        gaps.append("activity")
    return [gap for gap in gaps if gap not in answered_gaps]


_QUESTION_COPY: Final = {
    "en": {
        "hazard_detail": "What exactly is unsafe?",
        "location": "Where exactly is the hazard?",
        "activity": "What work is happening nearby?",
    },
    "zh-CN": {
        "hazard_detail": "具体有什么危险？",
        "location": "危险具体在哪里？",
        "activity": "附近正在进行什么工作？",
    },
}


def _stub_questions(
    gaps: list[str],
    preferred_lang: str,
    question_limit: int,
) -> list[dict[str, JsonValue]]:
    locale = preferred_lang if preferred_lang in _QUESTION_COPY else "en"
    catalogue = _QUESTION_COPY[locale]
    fallback = "Please add this detail: {gap}." if locale == "en" else "请补充这项信息：{gap}。"
    questions: list[dict[str, JsonValue]] = []
    for gap in gaps[:question_limit]:
        questions.append(
            {"gap": gap, "text": catalogue.get(gap, fallback.format(gap=gap))}
        )
    return questions


class TranslationResult(BaseModel):
    description_en: str

    @classmethod
    def stub_fixture(cls, variables: dict[str, object]) -> dict[str, JsonValue]:
        return {"description_en": _stub_translate(_text(variables, "description_original"))}


class FactExtractionResult(BaseModel):
    observed_facts: list[str]
    assumptions: list[str]

    @classmethod
    def stub_fixture(cls, variables: dict[str, object]) -> dict[str, JsonValue]:
        observed, assumptions = _split_observation(_text(variables, "description"))
        observed_json: list[JsonValue] = list(observed)
        assumptions_json: list[JsonValue] = list(assumptions)
        return {
            "observed_facts": observed_json,
            "assumptions": assumptions_json,
        }


class CompletenessResult(BaseModel):
    missing_information: list[str]

    @classmethod
    def stub_fixture(cls, variables: dict[str, object]) -> dict[str, JsonValue]:
        gaps: list[JsonValue] = list(
            _stub_gaps(
                _text(variables, "description"),
                _text(variables, "location"),
                _text(variables, "activity"),
                _string_list(variables, "observed_facts"),
                _answered_gaps(variables),
            )
        )
        return {
            "missing_information": gaps,
        }


class ComposedQuestion(BaseModel):
    gap: str = Field(min_length=1)
    text: str = Field(min_length=1)


class QuestionCompositionResult(BaseModel):
    questions: list[ComposedQuestion] = Field(
        min_length=1,
        max_length=MAX_QUESTIONS_PER_ROUND,
    )

    @classmethod
    def stub_fixture(cls, variables: dict[str, object]) -> dict[str, JsonValue]:
        questions: list[JsonValue] = list(
            _stub_questions(
                _string_list(variables, "missing_information"),
                _text(variables, "preferred_lang"),
                min(
                    MAX_QUESTIONS_PER_ROUND,
                    max(
                        0,
                        MAX_CLARIFICATION_QUESTIONS
                        - len(_answered_gaps(variables)),
                    ),
                ),
            )
        )
        return {"questions": questions}


class DraftCitationResult(BaseModel):
    document_id: str = Field(min_length=1)
    doc_ref: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    section: str | None
    page: int | None
    quote: str = Field(min_length=1)


def _retrieved_procedures(value: object) -> list[RetrievedProcedure]:
    if not isinstance(value, list):
        return []
    procedures: list[RetrievedProcedure] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        document_id = item.get("document_id")
        doc_ref = item.get("doc_ref")
        revision = item.get("revision")
        section = item.get("section")
        page = item.get("page")
        similarity = item.get("similarity")
        if (
            not isinstance(content, str)
            or not content.strip()
            or not isinstance(document_id, str)
            or not document_id.strip()
            or not isinstance(doc_ref, str)
            or not doc_ref.strip()
            or not isinstance(revision, str)
            or not revision.strip()
            or section is not None
            and not isinstance(section, str)
            or page is not None
            and (not isinstance(page, int) or isinstance(page, bool))
            or not isinstance(similarity, int | float)
            or isinstance(similarity, bool)
        ):
            continue
        procedures.append(
            {
                "content": content,
                "document_id": document_id,
                "doc_ref": doc_ref,
                "revision": revision,
                "section": section,
                "page": page,
                "similarity": float(similarity),
            }
        )
    return sorted(
        procedures,
        key=lambda procedure: procedure["similarity"],
        reverse=True,
    )[:6]


def _missing_procedure(description: str) -> str:
    if any(term in description for term in ("guardrail", "edge", "scaffold")):
        return "approved_work_at_height_procedure"
    if any(term in description for term in ("cable", "electrical")):
        return "approved_electrical_safety_procedure"
    return "approved_site_safety_procedure"


def _verbatim_excerpt(content: str) -> str:
    segments = [
        segment.strip()
        for segment in re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", content)
        if segment.strip()
    ]
    if not segments:
        raise ValueError("retrieved procedure content is empty")
    action_markers = (
        "must",
        "shall",
        "do not",
        "stop",
        "install",
        "必须",
        "不得",
        "应当",
        "应",
    )
    return next(
        (
            segment
            for segment in segments
            if any(marker in segment.casefold() for marker in action_markers)
        ),
        segments[0],
    )


class DraftResult(BaseModel):
    observed_facts: list[str]
    assumptions: list[str]
    missing_information: list[str]
    proposed_category: str | None
    proposed_urgency: Urgency | None
    suggested_owner_role: Role | None
    suggested_action: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    needs_escalation: bool
    escalation_reason: str | None
    citations: list[DraftCitationResult] = Field(max_length=6)

    @classmethod
    def stub_fixture(cls, variables: dict[str, object]) -> dict[str, JsonValue]:
        description = _text(variables, "description").casefold()
        observed_facts: list[JsonValue] = list(
            _string_list(variables, "observed_facts")
        )
        assumptions: list[JsonValue] = list(_string_list(variables, "assumptions"))
        intake_gaps = _string_list(variables, "missing_information")
        missing_information: list[JsonValue] = list(intake_gaps)
        retrieved_chunks = _retrieved_procedures(variables.get("retrieved_chunks"))
        if any(term in description for term in ("guardrail", "edge", "scaffold")):
            category = "work_at_height"
            urgency = Urgency.HIGH.value
        elif any(term in description for term in ("cable", "electrical")):
            category = "electrical"
            urgency = Urgency.HIGH.value
        else:
            category = "general_hazard"
            urgency = Urgency.MEDIUM.value
        needs_escalation = any(
            term in description
            for term in ("collapse", "electrocution", "uncontrolled fire")
        )
        citations: list[JsonValue] = []
        suggested_action: str | None = None
        if retrieved_chunks:
            source = retrieved_chunks[0]
            suggested_action = _verbatim_excerpt(source["content"])
            citations.append(
                {
                    "document_id": source["document_id"],
                    "doc_ref": source["doc_ref"],
                    "revision": source["revision"],
                    "section": source["section"],
                    "page": source["page"],
                    "quote": suggested_action,
                }
            )
        else:
            missing_information.append(_missing_procedure(description))
        return {
            "observed_facts": observed_facts,
            "assumptions": assumptions,
            "missing_information": missing_information,
            "proposed_category": category,
            "proposed_urgency": urgency,
            "suggested_owner_role": Role.RESPONSIBLE.value,
            "suggested_action": suggested_action,
            "confidence": 0.9 if not intake_gaps else 0.65,
            "needs_escalation": needs_escalation,
            "escalation_reason": (
                "Description contains an immediate escalation marker."
                if needs_escalation
                else None
            ),
            "citations": citations,
        }


def _result_string(data: dict[str, JsonValue], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        raise TypeError("provider result field is not a string")
    return value


def _result_strings(data: dict[str, JsonValue], name: str) -> list[str]:
    value = data.get(name)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError("provider result field is not a string list")
    return cast(list[str], value)


def _result_questions(data: dict[str, JsonValue]) -> list[IntakeQuestion]:
    value = data.get("questions")
    if not isinstance(value, list):
        raise TypeError("provider result questions field is not a list")
    questions: list[IntakeQuestion] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError("provider result question is not an object")
        gap = item.get("gap")
        text = item.get("text")
        if not isinstance(gap, str) or not isinstance(text, str):
            raise TypeError("provider result question fields are not strings")
        questions.append({"gap": gap, "text": text})
    return questions


def _gap_tokens(value: str) -> set[str]:
    """Reduce a model-authored gap label to stable matching terms."""
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 2
    }


def _questions_for_known_gaps(
    questions: list[IntakeQuestion],
    unresolved_gaps: list[str],
    preferred_lang: str,
) -> list[IntakeQuestion]:
    """Bind model-authored question text only to server-known unresolved gaps."""
    if len(questions) > MAX_QUESTIONS_PER_ROUND:
        raise ValueError("composed questions exceeded the question cap")

    remaining = list(dict.fromkeys(unresolved_gaps))
    normalised: list[IntakeQuestion] = []
    for question in questions:
        if not remaining:
            break
        if question["gap"] in remaining:
            selected = question["gap"]
            text = question["text"]
        else:
            question_tokens = _gap_tokens(
                f"{question['gap']} {question['text']}"
            )
            scores = [
                len(question_tokens & _gap_tokens(candidate))
                for candidate in remaining
            ]
            best_index = max(range(len(remaining)), key=scores.__getitem__)
            selected = remaining[best_index]
            text = question["text"]
            if scores[best_index] == 0:
                text = cast(
                    str,
                    _stub_questions([selected], preferred_lang, 1)[0]["text"],
                )
        remaining.remove(selected)
        normalised.append({"gap": selected, "text": text})
    return normalised


def _result_draft(data: dict[str, JsonValue]) -> DraftPayload:
    result = DraftResult.model_validate(data)
    citations: list[DraftCitation] = [
        {
            "document_id": citation.document_id,
            "doc_ref": citation.doc_ref,
            "revision": citation.revision,
            "section": citation.section,
            "page": citation.page,
            "quote": citation.quote,
        }
        for citation in result.citations
    ]
    return {
        "observed_facts": result.observed_facts,
        "assumptions": result.assumptions,
        "missing_information": result.missing_information,
        "proposed_category": result.proposed_category,
        "proposed_urgency": (
            result.proposed_urgency.value if result.proposed_urgency is not None else None
        ),
        "suggested_owner_role": (
            result.suggested_owner_role.value
            if result.suggested_owner_role is not None
            else None
        ),
        "suggested_action": result.suggested_action,
        "confidence": result.confidence,
        "needs_escalation": result.needs_escalation,
        "escalation_reason": result.escalation_reason,
        "citations": citations,
    }


async def translate(state: IntakeState) -> dict[str, object]:
    """Preserve English verbatim and derive a separate pivot for Mandarin input."""
    if state["lang_original"] == "en":
        return {"description_en": state["description_original"]}
    result = await get_provider().complete(
        "translate_intake",
        {
            "lang_original": state["lang_original"],
            "description_original": state["description_original"],
        },
        schema=TranslationResult,
    )
    return {"description_en": _result_string(result.data, "description_en")}


async def extract_facts(state: IntakeState) -> dict[str, object]:
    """Keep observations and interpretations in separate state channels."""
    description = state["description_en"] or state["description_original"]
    result = await get_provider().complete(
        "extract_facts",
        {
            "description": description,
            "location": state["location"],
            "activity": state["activity"],
            "prior_answers": state["prior_answers"],
        },
        schema=FactExtractionResult,
    )
    return {
        "observed_facts": _result_strings(result.data, "observed_facts"),
        "assumptions": _result_strings(result.data, "assumptions"),
    }


async def assess_completeness(state: IntakeState) -> dict[str, object]:
    """Identify only gaps material to a reviewer's next decision."""
    description = state["description_en"] or state["description_original"]
    result = await get_provider().complete(
        "assess_completeness",
        {
            "description": description,
            "location": state["location"],
            "activity": state["activity"],
            "observed_facts": state["observed_facts"],
            "assumptions": state["assumptions"],
            "prior_answers": state["prior_answers"],
        },
        schema=CompletenessResult,
    )
    return {
        "missing_information": _result_strings(
            result.data,
            "missing_information",
        )
    }


async def compose_questions(state: IntakeState) -> dict[str, object]:
    """Ask only about specific unresolved gaps in the reporter's language."""
    result = await get_provider().complete(
        "compose_questions",
        {
            "missing_information": state["missing_information"],
            "preferred_lang": state["preferred_lang"],
            "prior_answers": state["prior_answers"],
        },
        schema=QuestionCompositionResult,
    )
    questions = _result_questions(result.data)
    return {
        "questions": _questions_for_known_gaps(
            questions,
            state["missing_information"],
            state["preferred_lang"],
        )
    }


async def retrieve(state: IntakeState) -> dict[str, object]:
    """Validate and rank service-supplied corpus hits without database access."""
    return {"retrieved_chunks": _retrieved_procedures(state["retrieved_chunks"])}


async def draft(state: IntakeState) -> dict[str, object]:
    """Produce a provider-auditable draft without inventing uncited advice."""
    description = state["description_en"] or state["description_original"]
    result = await get_provider().complete(
        "draft_intake",
        {
            "description": description,
            "location": state["location"],
            "activity": state["activity"],
            "observed_facts": state["observed_facts"],
            "assumptions": state["assumptions"],
            "missing_information": state["missing_information"],
            "prior_answers": state["prior_answers"],
            "retrieved_chunks": state["retrieved_chunks"],
        },
        schema=DraftResult,
    )
    envelope: DraftEnvelope = {
        **_result_draft(result.data),
        "raw": result.raw,
        "provider": result.provider,
        "provider_ref": result.provider_ref,
        "latency_ms": result.latency_ms,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
    }
    return {"draft": envelope}


def route_after_assessment(state: IntakeState) -> Literal["clarify", "retrieve"]:
    """Stop asking at the durable two-round safety boundary."""
    if (
        state["missing_information"]
        and state["round"] < MAX_CLARIFICATION_ROUNDS
        and len(state["prior_answers"]) < MAX_CLARIFICATION_QUESTIONS
    ):
        return "clarify"
    return "retrieve"


def build_intake_graph() -> CompiledStateGraph[
    IntakeState,
    None,
    IntakeState,
    IntakeState,
]:
    """Compile the restartable intake stages through clarification routing."""
    builder: StateGraph[IntakeState, None, IntakeState, IntakeState] = StateGraph(
        IntakeState
    )
    builder.add_node("translate", translate)
    builder.add_node("extract_facts", extract_facts)
    builder.add_node("assess_completeness", assess_completeness)
    builder.add_node("compose_questions", compose_questions)
    builder.add_node("retrieve", retrieve)
    builder.add_node("draft", draft)
    builder.add_edge(START, "translate")
    builder.add_edge("translate", "extract_facts")
    builder.add_edge("extract_facts", "assess_completeness")
    builder.add_conditional_edges(
        "assess_completeness",
        route_after_assessment,
        {"clarify": "compose_questions", "retrieve": "retrieve"},
    )
    builder.add_edge("compose_questions", END)
    builder.add_edge("retrieve", "draft")
    builder.add_edge("draft", END)
    return builder.compile(name="safeloop_intake")


intake_graph = build_intake_graph()
