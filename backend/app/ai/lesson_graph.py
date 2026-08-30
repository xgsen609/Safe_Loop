"""Build a traceable bilingual lesson from one human-verified case snapshot."""

from __future__ import annotations

import re
from typing import Final, Literal, NotRequired, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field

from app.ai.provider import JsonValue, get_provider

MAX_BRIEFING_EN_WORDS: Final = 650
MAX_BRIEFING_ZH_CHARACTERS: Final = 1800
QUIZ_QUESTION_COUNT: Final = 3
QUIZ_OPTION_COUNT: Final = 4

CaseSource = Literal[
    "corrective_action",
    "completed_note",
    "verification_notes",
    "target_activity",
    "target_location",
    "evidence_caption",
    "evidence_count",
]


class VerifiedCase(TypedDict):
    """Contain only material accepted by the closing human verification."""

    corrective_action: str
    completed_note: str | None
    verification_notes: str
    verification_checklist: JsonValue
    target_activity: str | None
    target_location: str | None
    evidence_captions: list[str]
    evidence_count: int


class LessonProcedure(TypedDict):
    """Carry an approved procedure chunk as plain graph data."""

    content: str
    document_id: str
    doc_ref: str
    revision: str
    section: str | None
    page: int | None
    similarity: float


class CasePoint(TypedDict):
    """Tie one case statement to the verified field that supports it."""

    text: str
    source_ref: str
    quote: str


class BriefingSection(TypedDict):
    """Keep each briefing section linked to its case or procedure sources."""

    heading: str
    text: str
    source_refs: list[str]


class LocalisedText(TypedDict):
    """Match the locale-map shape persisted in Postgres."""

    en: str
    zh_cn: str


class QuizQuestion(TypedDict):
    """Keep one bilingual question ready for locale-map persistence."""

    question: LocalisedText
    options: list[LocalisedText]
    correct_option: int
    explanation: LocalisedText
    source_refs: list[str]


class LessonState(TypedDict):
    """Carry one JSON-serialisable lesson run from verified facts to quiz."""

    report_id: str
    request_id: NotRequired[str]
    verified_case: VerifiedCase
    retrieved_chunks: list[LessonProcedure]
    case_summary: list[CasePoint]
    procedure_sources: list[LessonProcedure]
    briefing_en_sections: list[BriefingSection]
    briefing_zh_cn_sections: list[BriefingSection]
    briefing_en: str
    briefing_zh_cn: str
    quiz_questions: list[QuizQuestion]


_PERSON_REFERENCE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Mdm|Dr)\.?\s+[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)*\b"
)
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_CJK = re.compile(r"[\u3400-\u9fff]")
_CJK_SAFETY_TERMS: Final = (
    "护栏",
    "防护栏",
    "安全带",
    "生命线",
    "防坠落",
    "坠落制动",
    "脚手架",
    "高处作业",
    "工作许可证",
)


def _anonymous(value: str) -> str:
    redacted = _PERSON_REFERENCE.sub("a worker", value)
    redacted = _EMAIL.sub("a worker", redacted)
    return re.sub(r"\s+", " ", redacted).strip()


def _normalised(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _contains_text(container: str, source: str) -> bool:
    return _normalised(source) in _normalised(container)


def _case_quote(source: str) -> str:
    sentences = _sentences(source)
    candidate = sentences[0] if sentences else source.strip()
    return candidate[:400].rstrip()


def _case_sources(case: VerifiedCase) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = [
        {
            "source_ref": "case:corrective_action",
            "text": _anonymous(case["corrective_action"]),
        },
        {
            "source_ref": "case:verification_notes",
            "text": _anonymous(case["verification_notes"]),
        },
    ]
    optional_values = (
        ("completed_note", case["completed_note"]),
        ("target_activity", case["target_activity"]),
        ("target_location", case["target_location"]),
    )
    entries.extend(
        {
            "source_ref": f"case:{name}",
            "text": _anonymous(value),
        }
        for name, value in optional_values
        if value is not None and value.strip()
    )
    entries.extend(
        {
            "source_ref": f"case:evidence_caption:{index}",
            "text": _anonymous(caption),
        }
        for index, caption in enumerate(case["evidence_captions"])
        if caption.strip()
    )
    if case["evidence_count"] > 0:
        entries.append(
            {
                "source_ref": "case:evidence_count",
                "text": f"{case['evidence_count']} evidence image(s) were included in the accepted submission.",
            }
        )
    return entries


def _procedure_ref(source: LessonProcedure) -> str:
    section = source["section"] or "none"
    page = str(source["page"]) if source["page"] is not None else "none"
    return (
        f"procedure:{source['document_id']}:revision:{source['revision']}:"
        f"section:{section}:page:{page}"
    )


def _visible_source(reference: str, procedures: list[LessonProcedure]) -> str:
    if reference.startswith("case:"):
        return reference.removeprefix("case:").replace("_", " ")
    source = next(
        (item for item in procedures if _procedure_ref(item) == reference),
        None,
    )
    if source is None:
        raise ValueError("briefing source reference is unknown")
    parts = [source["doc_ref"], f"rev {source['revision']}"]
    if source["section"]:
        parts.append(f"section {source['section']}")
    if source["page"] is not None:
        parts.append(f"page {source['page']}")
    return ", ".join(parts)


def _sentences(content: str) -> list[str]:
    return [
        segment.strip()
        for segment in re.split(r"(?<=[.!?。！？])\s*|[\r\n]+", content)
        if segment.strip()
    ]


def _excerpt(content: str) -> str:
    sentences = _sentences(content)
    if not sentences:
        raise ValueError("procedure content is empty")
    markers = ("must", "shall", "do not", "必须", "不得", "应当", "应")
    return next(
        (
            sentence
            for sentence in sentences
            if any(marker in sentence.casefold() for marker in markers)
        ),
        sentences[0],
    )


def _contains_procedure_quote(text: str, content: str) -> bool:
    return any(
        len(_normalised(sentence)) >= 8 and _contains_text(text, sentence)
        for sentence in _sentences(content)
    )


def _normalise_procedures(value: object) -> list[LessonProcedure]:
    if not isinstance(value, list):
        return []
    procedures: list[LessonProcedure] = []
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


class CasePointResult(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    source_ref: str = Field(min_length=1, max_length=300)
    quote: str = Field(min_length=1, max_length=400)


class CaseSummaryResult(BaseModel):
    points: list[CasePointResult] = Field(min_length=2, max_length=10)

    @classmethod
    def stub_fixture(cls, variables: dict[str, object]) -> dict[str, JsonValue]:
        raw_sources = variables.get("case_sources")
        if not isinstance(raw_sources, list):
            return {"points": []}
        points: list[JsonValue] = []
        for source in raw_sources:
            if not isinstance(source, dict):
                continue
            reference = source.get("source_ref")
            text = source.get("text")
            if isinstance(reference, str) and isinstance(text, str) and text.strip():
                quote = _case_quote(_anonymous(text))
                points.append(
                    {
                        "text": quote,
                        "source_ref": reference,
                        "quote": quote,
                    }
                )
        return {"points": points[:10]}


class BriefingSectionResult(BaseModel):
    heading: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2500)
    source_refs: list[str] = Field(min_length=1, max_length=8)


def _first_source(
    sources: list[dict[str, str]],
    reference: str,
) -> dict[str, str]:
    return next(source for source in sources if source["source_ref"] == reference)


class EnglishBriefingResult(BaseModel):
    sections: list[BriefingSectionResult] = Field(min_length=3, max_length=5)

    @classmethod
    def stub_fixture(cls, variables: dict[str, object]) -> dict[str, JsonValue]:
        raw_points = variables.get("case_summary")
        points = [
            cast(dict[str, str], point)
            for point in raw_points
            if isinstance(point, dict)
        ] if isinstance(raw_points, list) else []
        action = _first_source(points, "case:corrective_action")
        verification = _first_source(points, "case:verification_notes")
        completed = next(
            (
                point
                for point in points
                if point["source_ref"] == "case:completed_note"
            ),
            action,
        )
        procedures = _normalise_procedures(variables.get("procedure_sources"))
        english_source = next(
            (
                source
                for source in procedures
                if len(_CJK.findall(source["content"])) < 4
            ),
            None,
        )
        guidance_text = verification["text"]
        guidance_refs: list[JsonValue] = [verification["source_ref"]]
        if english_source is not None:
            guidance_text = _excerpt(english_source["content"])
            guidance_refs = [_procedure_ref(english_source)]
        sections: list[JsonValue] = [
            {
                "heading": "What was corrected",
                "text": action["quote"],
                "source_refs": [action["source_ref"]],
            },
            {
                "heading": "What was verified",
                "text": f"{completed['quote']} {verification['quote']}",
                "source_refs": [
                    completed["source_ref"],
                    verification["source_ref"],
                ],
            },
            {
                "heading": "What the crew should use",
                "text": guidance_text,
                "source_refs": guidance_refs,
            },
        ]
        return {"sections": sections}


class ChineseBriefingResult(BaseModel):
    sections: list[BriefingSectionResult] = Field(min_length=3, max_length=5)

    @classmethod
    def stub_fixture(cls, variables: dict[str, object]) -> dict[str, JsonValue]:
        raw_sections = variables.get("briefing_en_sections")
        english_sections = [
            cast(dict[str, object], section)
            for section in raw_sections
            if isinstance(section, dict)
        ] if isinstance(raw_sections, list) else []
        procedures = _normalise_procedures(variables.get("procedure_sources"))
        chinese_source = next(
            (
                source
                for source in procedures
                if _CJK.search(source["content"]) is not None
            ),
            None,
        )
        headings = ("整改内容", "核查结果", "班组应采用的做法")
        fallback_text = (
            "这项安全隐患已按批准的整改要求处理。",
            "整改证据已经过人员核查。",
            "进行同类工作前，应按已批准的安全程序检查控制措施。",
        )
        sections: list[JsonValue] = []
        for index, english in enumerate(english_sections[:3]):
            raw_refs = english.get("source_refs")
            refs: list[JsonValue] = (
                [item for item in raw_refs if isinstance(item, str)]
                if isinstance(raw_refs, list)
                else []
            )
            text = fallback_text[index]
            if index == 2 and chinese_source is not None:
                text = _excerpt(chinese_source["content"])
                refs = [_procedure_ref(chinese_source)]
            sections.append(
                {
                    "heading": headings[index],
                    "text": text,
                    "source_refs": refs,
                }
            )
        return {"sections": sections}


class LocalisedTextResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    en: str = Field(min_length=1, max_length=1000)
    zh_cn: str = Field(alias="zh-CN", min_length=1, max_length=1000)


class QuizQuestionResult(BaseModel):
    question: LocalisedTextResult
    options: list[LocalisedTextResult] = Field(
        min_length=QUIZ_OPTION_COUNT,
        max_length=QUIZ_OPTION_COUNT,
    )
    # Vertex accepts inclusive numeric bounds in response schemas, but rejects
    # Pydantic's `exclusiveMaximum` generated by lt=QUIZ_OPTION_COUNT.
    correct_option: int = Field(ge=0, le=QUIZ_OPTION_COUNT - 1)
    explanation: LocalisedTextResult
    source_refs: list[str] = Field(min_length=1, max_length=8)


class QuizResult(BaseModel):
    questions: list[QuizQuestionResult] = Field(
        min_length=QUIZ_QUESTION_COUNT,
        max_length=QUIZ_QUESTION_COUNT,
    )

    @classmethod
    def stub_fixture(cls, variables: dict[str, object]) -> dict[str, JsonValue]:
        raw_refs = variables.get("source_refs")
        references = [item for item in raw_refs if isinstance(item, str)] if isinstance(raw_refs, list) else []
        case_action = next(
            (reference for reference in references if reference == "case:corrective_action"),
            references[0] if references else "case:corrective_action",
        )
        verification = next(
            (reference for reference in references if reference == "case:verification_notes"),
            case_action,
        )
        procedure = next(
            (reference for reference in references if reference.startswith("procedure:")),
            case_action,
        )
        questions: list[JsonValue] = [
            {
                "question": {
                    "en": "Which action does this lesson say was completed?",
                    "zh-CN": "这份学习材料说明完成了哪项整改？",
                },
                "options": [
                    {"en": "The verified corrective action", "zh-CN": "已核实的整改措施"},
                    {"en": "An unrelated future task", "zh-CN": "一项无关的后续工作"},
                    {"en": "A rejected proposal", "zh-CN": "一项已拒绝的建议"},
                    {"en": "No action", "zh-CN": "没有采取行动"},
                ],
                "correct_option": 0,
                "explanation": {
                    "en": "The lesson uses the corrective action accepted in this case.",
                    "zh-CN": "这份学习材料采用本个案中已接受的整改措施。",
                },
                "source_refs": [case_action],
            },
            {
                "question": {
                    "en": "What confirms that the corrective work was accepted?",
                    "zh-CN": "什么情况表示整改工作已被接受？",
                },
                "options": [
                    {"en": "A notification was read", "zh-CN": "有人读取了通知"},
                    {"en": "A human reviewer verified the evidence", "zh-CN": "审核人员核实了证据"},
                    {"en": "The due date passed", "zh-CN": "整改期限已过"},
                    {"en": "The report was drafted", "zh-CN": "报告已起草"},
                ],
                "correct_option": 1,
                "explanation": {
                    "en": "The final verification notes are the accepted closure evidence.",
                    "zh-CN": "最终核查记录是结案时接受的证据。",
                },
                "source_refs": [verification],
            },
            {
                "question": {
                    "en": "What should guide the same work in future?",
                    "zh-CN": "今后进行同类工作时应依据什么？",
                },
                "options": [
                    {"en": "A guess", "zh-CN": "个人猜测"},
                    {"en": "An unverified report", "zh-CN": "未经核实的报告"},
                    {"en": "The cited approved procedure or verified action", "zh-CN": "引用的已批准程序或已核实的整改措施"},
                    {"en": "A person's name", "zh-CN": "某个人的姓名"},
                ],
                "correct_option": 2,
                "explanation": {
                    "en": "The briefing traces its guidance to an approved source.",
                    "zh-CN": "简报中的做法可追溯至已批准的来源。",
                },
                "source_refs": [procedure],
            },
        ]
        return {"questions": questions}


def _result_points(data: dict[str, JsonValue], sources: list[dict[str, str]]) -> list[CasePoint]:
    result = CaseSummaryResult.model_validate(data)
    source_map = {source["source_ref"]: source["text"] for source in sources}
    points: list[CasePoint] = []
    for point in result.points:
        source_text = source_map.get(point.source_ref)
        if (
            source_text is None
            or not _contains_text(source_text, point.quote)
            or not _contains_text(point.text, point.quote)
        ):
            raise ValueError("case summary claim is not traceable to verified material")
        if _PERSON_REFERENCE.search(point.text) or _EMAIL.search(point.text):
            raise ValueError("case summary names an individual")
        points.append(
            {
                "text": point.text,
                "source_ref": point.source_ref,
                "quote": point.quote,
            }
        )
    required = {"case:corrective_action", "case:verification_notes"}
    if not required.issubset({point["source_ref"] for point in points}):
        raise ValueError("case summary omitted required verified material")
    return points


def _result_sections(data: dict[str, JsonValue], schema: type[BaseModel]) -> list[BriefingSection]:
    validated = schema.model_validate(data)
    raw_sections = getattr(validated, "sections", None)
    if not isinstance(raw_sections, list):
        raise TypeError("briefing sections are missing")
    return [
        {
            "heading": section.heading,
            "text": section.text,
            "source_refs": list(section.source_refs),
        }
        for section in cast(list[BriefingSectionResult], raw_sections)
    ]


def _validate_english_sections(
    sections: list[BriefingSection],
    case_summary: list[CasePoint],
    procedures: list[LessonProcedure],
) -> None:
    case_map = {point["source_ref"]: point["quote"] for point in case_summary}
    procedure_map = {_procedure_ref(source): source for source in procedures}
    allowed = set(case_map) | set(procedure_map)
    for section in sections:
        if not section["source_refs"] or any(
            reference not in allowed for reference in section["source_refs"]
        ):
            raise ValueError("English briefing contains an unknown source")
        for reference in section["source_refs"]:
            if reference in case_map and not _contains_text(section["text"], case_map[reference]):
                raise ValueError("English briefing case claim is not verbatim traceable")
            if reference in procedure_map and not _contains_procedure_quote(
                section["text"], procedure_map[reference]["content"]
            ):
                raise ValueError("English briefing procedure claim is not verbatim traceable")


def _validate_chinese_sections(
    sections: list[BriefingSection],
    english_sections: list[BriefingSection],
    case: VerifiedCase,
    procedures: list[LessonProcedure],
) -> None:
    case_refs = {source["source_ref"] for source in _case_sources(case)}
    procedure_map = {_procedure_ref(source): source for source in procedures}
    allowed = case_refs | set(procedure_map)
    if len(sections) != len(english_sections):
        raise ValueError("Chinese briefing must translate every English section")
    for section in sections:
        if not section["source_refs"] or any(
            reference not in allowed for reference in section["source_refs"]
        ):
            raise ValueError("Chinese briefing contains an unknown source")
        for reference in section["source_refs"]:
            source = procedure_map.get(reference)
            if source is not None and _CJK.search(source["content"]) and not _contains_procedure_quote(
                section["text"], source["content"]
            ):
                raise ValueError("Chinese procedure wording is not verbatim")
    terms = {
        term
        for source in procedures
        for term in _CJK_SAFETY_TERMS
        if term in source["content"]
    }
    if terms and not any(term in " ".join(section["text"] for section in sections) for term in terms):
        raise ValueError("Chinese briefing did not reuse procedure terminology")


def _render_sections(
    sections: list[BriefingSection],
    procedures: list[LessonProcedure],
    *,
    locale: Literal["en", "zh-CN"],
) -> str:
    source_label = "Source" if locale == "en" else "来源"
    blocks = [
        "\n".join(
            (
                f"## {section['heading']}",
                section["text"],
                f"{source_label}: "
                + "; ".join(
                    _visible_source(reference, procedures)
                    for reference in section["source_refs"]
                ),
            )
        )
        for section in sections
    ]
    body = "\n\n".join(blocks).strip()
    if _PERSON_REFERENCE.search(body) or _EMAIL.search(body):
        raise ValueError("briefing names an individual")
    if locale == "en" and len(body.split()) > MAX_BRIEFING_EN_WORDS:
        raise ValueError("English briefing exceeds one A4 page")
    if locale == "zh-CN" and len(body) > MAX_BRIEFING_ZH_CHARACTERS:
        raise ValueError("Chinese briefing exceeds one A4 page")
    return body


def _result_quiz(
    data: dict[str, JsonValue],
    allowed_refs: set[str],
    procedures: list[LessonProcedure],
) -> list[QuizQuestion]:
    result = QuizResult.model_validate(data)
    quiz: list[QuizQuestion] = []
    for item in result.questions:
        if any(reference not in allowed_refs for reference in item.source_refs):
            raise ValueError("quiz question contains an unknown source")
        english_options = [option.en.casefold().strip() for option in item.options]
        chinese_options = [option.zh_cn.strip() for option in item.options]
        if len(set(english_options)) != QUIZ_OPTION_COUNT or len(set(chinese_options)) != QUIZ_OPTION_COUNT:
            raise ValueError("quiz options must be distinct in both locales")
        en_sources = "; ".join(
            _visible_source(reference, procedures) for reference in item.source_refs
        )
        zh_sources = en_sources
        quiz.append(
            {
                "question": {"en": item.question.en, "zh_cn": item.question.zh_cn},
                "options": [
                    {"en": option.en, "zh_cn": option.zh_cn}
                    for option in item.options
                ],
                "correct_option": item.correct_option,
                "explanation": {
                    "en": f"{item.explanation.en}\nSource: {en_sources}",
                    "zh_cn": f"{item.explanation.zh_cn}\n来源: {zh_sources}",
                },
                "source_refs": list(item.source_refs),
            }
        )
    return quiz


async def summarise_case(state: LessonState) -> dict[str, object]:
    """Summarise only fields admitted by the verified closure snapshot."""
    sources = _case_sources(state["verified_case"])
    variables = {"case_sources": sources}
    try:
        result = await get_provider().complete(
            "summarise_case",
            variables,
            schema=CaseSummaryResult,
        )
        points = _result_points(result.data, sources)
    except (RuntimeError, TypeError, ValueError):
        # Model wording may paraphrase a quote even when the underlying claim is
        # correct. Keep the hard traceability guarantee by falling back to exact,
        # anonymous source wording instead of abandoning the whole workflow.
        points = _result_points(
            CaseSummaryResult.stub_fixture(variables),
            sources,
        )
    return {"case_summary": points}


async def retrieve_procedures(state: LessonState) -> dict[str, object]:
    """Validate and rank service-supplied approved chunks without database access."""
    return {
        "procedure_sources": _normalise_procedures(state["retrieved_chunks"])
    }


async def write_briefing_en(state: LessonState) -> dict[str, object]:
    """Write short English sections whose claims carry explicit source references."""
    variables = {
        "case_summary": state["case_summary"],
        "procedure_sources": state["procedure_sources"],
        "maximum_words": MAX_BRIEFING_EN_WORDS,
    }
    try:
        result = await get_provider().complete(
            "write_briefing_en",
            variables,
            schema=EnglishBriefingResult,
        )
        sections = _result_sections(result.data, EnglishBriefingResult)
        _validate_english_sections(
            sections,
            state["case_summary"],
            state["procedure_sources"],
        )
        body = _render_sections(
            sections,
            state["procedure_sources"],
            locale="en",
        )
    except (RuntimeError, TypeError, ValueError):
        sections = _result_sections(
            EnglishBriefingResult.stub_fixture(variables),
            EnglishBriefingResult,
        )
        _validate_english_sections(
            sections,
            state["case_summary"],
            state["procedure_sources"],
        )
        body = _render_sections(
            sections,
            state["procedure_sources"],
            locale="en",
        )
    return {
        "briefing_en_sections": sections,
        "briefing_en": body,
    }


async def translate_zh_cn(state: LessonState) -> dict[str, object]:
    """Translate once while preserving section lineage and procedure terminology."""
    variables = {
        "briefing_en_sections": state["briefing_en_sections"],
        "procedure_sources": state["procedure_sources"],
        "maximum_characters": MAX_BRIEFING_ZH_CHARACTERS,
    }
    try:
        result = await get_provider().complete(
            "translate_zh_cn",
            variables,
            schema=ChineseBriefingResult,
        )
        sections = _result_sections(result.data, ChineseBriefingResult)
        _validate_chinese_sections(
            sections,
            state["briefing_en_sections"],
            state["verified_case"],
            state["procedure_sources"],
        )
        body = _render_sections(
            sections,
            state["procedure_sources"],
            locale="zh-CN",
        )
    except (RuntimeError, TypeError, ValueError):
        sections = _result_sections(
            ChineseBriefingResult.stub_fixture(variables),
            ChineseBriefingResult,
        )
        _validate_chinese_sections(
            sections,
            state["briefing_en_sections"],
            state["verified_case"],
            state["procedure_sources"],
        )
        body = _render_sections(
            sections,
            state["procedure_sources"],
            locale="zh-CN",
        )
    return {
        "briefing_zh_cn_sections": sections,
        "briefing_zh_cn": body,
    }


async def write_quiz(state: LessonState) -> dict[str, object]:
    """Write exactly three bilingual questions from the completed briefing."""
    source_refs = {
        point["source_ref"] for point in state["case_summary"]
    } | {_procedure_ref(source) for source in state["procedure_sources"]}
    variables = {
        "briefing_en": state["briefing_en"],
        "briefing_zh_cn": state["briefing_zh_cn"],
        "source_refs": sorted(source_refs),
        "question_count": QUIZ_QUESTION_COUNT,
        "option_count": QUIZ_OPTION_COUNT,
    }
    try:
        result = await get_provider().complete(
            "write_quiz",
            variables,
            schema=QuizResult,
        )
        questions = _result_quiz(
            result.data,
            source_refs,
            state["procedure_sources"],
        )
    except (RuntimeError, TypeError, ValueError):
        questions = _result_quiz(
            QuizResult.stub_fixture(variables),
            source_refs,
            state["procedure_sources"],
        )
    return {"quiz_questions": questions}


def build_lesson_graph() -> CompiledStateGraph[
    LessonState,
    None,
    LessonState,
    LessonState,
]:
    """Compile the one-run bilingual lesson pipeline in the PRD's fixed order."""
    builder: StateGraph[LessonState, None, LessonState, LessonState] = StateGraph(
        LessonState
    )
    builder.add_node("summarise_case", summarise_case)
    builder.add_node("retrieve_procedures", retrieve_procedures)
    builder.add_node("write_briefing_en", write_briefing_en)
    builder.add_node("translate_zh_cn", translate_zh_cn)
    builder.add_node("write_quiz", write_quiz)
    builder.add_edge(START, "summarise_case")
    builder.add_edge("summarise_case", "retrieve_procedures")
    builder.add_edge("retrieve_procedures", "write_briefing_en")
    builder.add_edge("write_briefing_en", "translate_zh_cn")
    builder.add_edge("translate_zh_cn", "write_quiz")
    builder.add_edge("write_quiz", END)
    return builder.compile(name="safeloop_lesson")


lesson_graph = build_lesson_graph()
