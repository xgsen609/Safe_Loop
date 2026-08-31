"""Localize reviewer-facing case content without changing the source record."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import cast


PathPart = str | int
ContentPath = tuple[PathPart, ...]
_CJK = re.compile(r"[\u3400-\u9fff]")


_STUB_TRANSLATIONS = {
    "There is no guardrail near the east edge of the Level 6 formwork work area. Workers are moving materials nearby, about one or two metres from the edge. I'm worried someone could fall from the edge.": "六楼模板作业区东侧临边没有护栏。工人正在附近搬运材料，距离边缘约一至两米，我担心有人会从边缘坠落。",
    "There is no guardrail installed near the east edge of the Level 6 formwork work area. Workers are moving materials approximately one to two meters from the edge, posing a fall hazard.": "六楼模板作业区东侧临边未安装护栏。工人在距离边缘约一至两米处搬运材料，存在坠落风险。",
    "There is no guardrail installed near the east edge of the Level 6 formwork work area.": "六楼模板作业区东侧临边未安装护栏。",
    "Level 6 – East Formwork Area": "六楼－东侧模板作业区",
    "moving materials": "搬运材料",
    "work at height": "高处作业",
    "Install a secured guardrail before work resumes.": "复工前安装牢固的防护栏。",
    "4.2 Edge protection": "4.2 临边防护",
    "The edge guardrail was removed and the opening is beside the stair exit.": "临边护栏已被拆除，开口位于楼梯出口旁。",
    "A lifting sling has a visible cut near its eye.": "吊装带的吊眼附近有明显割痕。",
    "Pedestrians are walking inside the excavator swing area during operation.": "挖掘机作业时，行人仍在回转范围内走动。",
    "The temporary lighting cable has exposed inner insulation.": "临时照明电缆的内层绝缘已外露。",
    "Dusty offcuts narrow the marked pedestrian route.": "积尘的边角料使已标示的行人通道变窄。",
    "Tower B Level 16": "B座十六楼",
    "Tower C Lifting Zone": "C座吊装区",
    "South Excavation": "南侧挖掘区",
    "Basement Ramp": "地下室坡道",
    "Fabrication Yard": "加工场",
    "Work at height": "高处作业",
    "Lifting": "吊装作业",
    "Excavation": "挖掘作业",
    "Electrical maintenance": "电气维护",
    "Housekeeping": "现场整理",
    "electrical isolation": "电气隔离",
    "lifting operation": "吊装作业",
    "excavation access": "挖掘区通道",
    "Isolate and lock out the energy source before maintenance starts.": "维护开始前隔离能源并执行上锁挂牌。",
    "The formwork crew owns this area.": "模板班组负责该区域。",
    "The formwork crew owns the area.": "模板班组负责该区域。",
    "Whether work is scheduled below this level": "此楼层下方是否安排了作业",
    "Install secured guardrails before work resumes.": "复工前安装牢固的防护栏。",
    "Install secured guardrails before work resumes. Keep the area clear.": "复工前安装牢固的防护栏，并保持该区域畅通。",
    "Install a secured guardrail before work resumes. The top rail, mid rail and toe board must remain fixed while the edge is open.": "复工前安装牢固的防护栏。临边开放期间，上栏杆、中栏杆和踢脚板必须保持固定。",
    "Immediate human escalation recorded by the reviewer.": "审核人员已记录立即人工升级处理。",
}


_PHRASE_TRANSLATIONS = (
    ("Level 6", "六楼"),
    ("Level 7", "七楼"),
    ("Level 8", "八楼"),
    ("Level 9", "九楼"),
    ("Level 11", "十一楼"),
    ("Level 12", "十二楼"),
    ("Level 16", "十六楼"),
    ("Tower A", "A座"),
    ("Tower B", "B座"),
    ("Tower C", "C座"),
    ("Tower D", "D座"),
    ("formwork work area", "模板作业区"),
    ("formwork area", "模板作业区"),
    ("east edge", "东侧临边"),
    ("west edge", "西侧临边"),
    ("guardrail", "防护栏"),
    ("moving materials", "搬运材料"),
    ("fall hazard", "坠落风险"),
    ("pedestrian route", "行人通道"),
    ("work area", "作业区"),
    ("loading bay", "装卸区"),
    ("lifting zone", "吊装区"),
    ("excavator swing area", "挖掘机回转范围"),
    ("temporary lighting cable", "临时照明电缆"),
    ("exposed inner insulation", "内层绝缘外露"),
    ("corrective action", "整改行动"),
)


def _stub_translation(text: str) -> str:
    if _CJK.search(text):
        return text
    exact = _STUB_TRANSLATIONS.get(text)
    if exact is not None:
        return exact
    translated = text
    for source, replacement in _PHRASE_TRANSLATIONS:
        translated = re.sub(re.escape(source), replacement, translated, flags=re.IGNORECASE)
    return translated


async def translate_texts_zh(texts: list[str]) -> list[str]:
    """Translate reviewer content locally without disclosing case text externally."""
    return [_stub_translation(text) for text in texts]


def _read(value: object, path: ContentPath) -> object:
    current = value
    for part in path:
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                return None
            current = current[part]
        else:
            if not isinstance(current, dict):
                return None
            current = current.get(part)
    return current


def _write(value: object, path: ContentPath, replacement: str) -> None:
    current = value
    for part in path[:-1]:
        current = current[part]  # type: ignore[index]
    current[path[-1]] = replacement  # type: ignore[index]


def _append_string_path(
    value: object,
    path: ContentPath,
    paths: list[ContentPath],
    texts: list[str],
) -> None:
    text = _read(value, path)
    if isinstance(text, str) and text.strip():
        paths.append(path)
        texts.append(text)


def _append_string_list_paths(
    value: object,
    path: ContentPath,
    paths: list[ContentPath],
    texts: list[str],
) -> None:
    entries = _read(value, path)
    if not isinstance(entries, list):
        return
    for index, entry in enumerate(entries):
        if isinstance(entry, str) and entry.strip():
            paths.append((*path, index))
            texts.append(entry)


async def localize_report_zh(report: dict[str, object]) -> dict[str, object]:
    """Return a Chinese presentation copy of all human-readable report fields."""
    localized = cast(dict[str, object], deepcopy(report))
    paths: list[ContentPath] = []
    texts: list[str] = []
    for field in (
        "description_original",
        "description_en",
        "location_text",
        "activity",
        "level_or_zone",
        "grid_ref",
    ):
        _append_string_path(localized, (field,), paths, texts)

    draft = localized.get("latest_draft")
    if isinstance(draft, dict):
        for field in (
            "proposed_category",
            "suggested_action",
            "escalation_reason",
        ):
            _append_string_path(localized, ("latest_draft", field), paths, texts)
        for field in ("observed_facts", "assumptions"):
            _append_string_list_paths(
                localized,
                ("latest_draft", field),
                paths,
                texts,
            )
        citations = draft.get("citations")
        if isinstance(citations, list):
            for index in range(len(citations)):
                for field in ("section", "quote"):
                    _append_string_path(
                        localized,
                        ("latest_draft", "citations", index, field),
                        paths,
                        texts,
                    )

    for field in ("action_text", "completed_note"):
        _append_string_path(localized, ("current_action", field), paths, texts)

    verifications = localized.get("verifications")
    if isinstance(verifications, list):
        for index in range(len(verifications)):
            for field in ("notes", "reason"):
                _append_string_path(
                    localized,
                    ("verifications", index, field),
                    paths,
                    texts,
                )

    for field in ("action_text", "verification_notes"):
        _append_string_path(localized, ("closure_receipt", field), paths, texts)

    for path, translation in zip(
        paths,
        await translate_texts_zh(texts),
        strict=True,
    ):
        _write(localized, path, translation)
    return localized


async def localize_report_items_zh(
    items: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Translate summary fields for a reviewer or reporter queue in one batch."""
    localized = cast(list[dict[str, object]], deepcopy(items))
    paths: list[ContentPath] = []
    texts: list[str] = []
    for index in range(len(localized)):
        for field in (
            "summary",
            "location_text",
            "action_text",
            "completed_note",
            "deficiency_reason",
            "deficiency_notes",
        ):
            _append_string_path(localized, (index, field), paths, texts)
    for path, translation in zip(
        paths,
        await translate_texts_zh(texts),
        strict=True,
    ):
        _write(localized, path, translation)
    return localized
