"""Keep Chinese reviewer content complete, ordered, and source preserving."""

from __future__ import annotations

import asyncio

from app.services.content_localization_service import (
    localize_report_items_zh,
    localize_report_zh,
)


def test_localizes_reviewer_detail_without_mutating_source() -> None:
    source: dict[str, object] = {
        "description_original": (
            "There is no guardrail near the east edge of the Level 6 formwork "
            "work area. Workers are moving materials nearby, about one or two "
            "metres from the edge. I'm worried someone could fall from the edge."
        ),
        "description_en": (
            "There is no guardrail installed near the east edge of the Level 6 "
            "formwork work area. Workers are moving materials approximately one "
            "to two meters from the edge, posing a fall hazard."
        ),
        "location_text": "Level 6 – East Formwork Area",
        "activity": "moving materials",
        "latest_draft": {
            "observed_facts": [
                "There is no guardrail installed near the east edge of the Level 6 formwork work area."
            ],
            "assumptions": [],
            "proposed_category": "work at height",
            "suggested_action": "Install a secured guardrail before work resumes.",
            "citations": [
                {
                    "section": "4.2 Edge protection",
                    "quote": "Install a secured guardrail before work resumes.",
                }
            ],
        },
        "current_action": None,
        "verifications": [],
        "closure_receipt": None,
    }

    localized = asyncio.run(localize_report_zh(source))

    assert localized["description_original"].startswith("六楼模板作业区")
    assert localized["location_text"] == "六楼－东侧模板作业区"
    assert localized["activity"] == "搬运材料"
    draft = localized["latest_draft"]
    assert isinstance(draft, dict)
    assert draft["proposed_category"] == "高处作业"
    assert draft["suggested_action"] == "复工前安装牢固的防护栏。"
    assert source["location_text"] == "Level 6 – East Formwork Area"


def test_localizes_queue_summary_and_location() -> None:
    source = [
        {
            "summary": "There is no guardrail installed near the east edge of the Level 6 formwork work area.",
            "location_text": "Level 6 – East Formwork Area",
        }
    ]

    localized = asyncio.run(localize_report_items_zh(source))

    assert localized[0]["summary"].startswith("六楼模板作业区")
    assert localized[0]["location_text"] == "六楼－东侧模板作业区"
    assert source[0]["location_text"] == "Level 6 – East Formwork Area"
