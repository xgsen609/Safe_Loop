from __future__ import annotations

import json
from pathlib import Path

from app.domain.enums import ReportStatus


ROOT = Path(__file__).resolve().parents[2]


def test_demo_seed_contract_is_self_verifying() -> None:
    source = (ROOT / "supabase" / "demo_seed.sql").read_text(encoding="utf-8")

    assert "demo_report_count <> 40" in source
    assert "demo_status_count <> 13" in source
    assert "demo_document_count <> 2" in source
    assert "demo_published_count <> 3" in source
    assert "demo_rework_count < 2" in source
    assert "demo_quiz_briefing_count <> 3" in source
    for status in ReportStatus:
        assert f"'{status.value}'" in source


def test_deployment_regions_are_singapore_only() -> None:
    vercel = json.loads(
        (ROOT / "frontend" / "vercel.json").read_text(encoding="utf-8")
    )
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )

    assert vercel["regions"] == ["sin1"]
    assert "GCP_REGION: asia-southeast1" in workflow
    assert "SUPABASE_REGION: ap-southeast-1" in workflow
    assert "VERCEL_REGION: sin1" in workflow
    assert "supabase db push" in workflow
    assert "LIVE_TRANSCRIPTION_ENABLED=true" in workflow
    assert "VERTEX_LIVE_TRANSCRIPTION_MODEL=gemini-3.5-transcribe-live-preview" in workflow
    assert "NEXT_PUBLIC_BACKEND_WS_URL" in workflow
    assert 'curl --fail --silent --show-error "$backend_url/health/deep"' in workflow
    assert "us-central1" not in workflow
    assert "iad1" not in workflow


def test_backend_container_runtime_is_pinned() -> None:
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.startswith("FROM python:3.12.8-slim-bookworm\n")
    assert '"--port", "8080"' in dockerfile
