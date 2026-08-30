from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.ai.live_transcription import (
    MANDARIN_CONSTRUCTION_VOCABULARY,
    LiveTranscriptionConfig,
    detected_locale_or_infer,
    live_connect_config,
)
from app.ai.transcription import Transcript
from app.api import transcription_live
from app.api.transcription_live import LiveTicketRequest
from app.domain.enums import ActorType, Role
from app.services.live_transcription_store import LiveTicket
from app.services.report_service import Actor


def test_live_config_requires_global_preview_model() -> None:
    valid = LiveTranscriptionConfig(
        project_id="safe-loop",
        location="global",
        model="gemini-3.5-transcribe-live-preview",
    )
    valid.validate()
    with pytest.raises(ValueError):
        LiveTranscriptionConfig(
            project_id="safe-loop",
            location="asia-southeast1",
            model=valid.model,
        ).validate()


def test_live_config_keeps_mandarin_and_english_available_together() -> None:
    config = live_connect_config("zh-CN")
    audio = config.input_audio_transcription
    assert audio is not None
    assert audio.language_codes == ["cmn-Hans-CN", "en-GB"]
    assert audio.custom_vocabulary == MANDARIN_CONSTRUCTION_VOCABULARY


def test_live_transcript_does_not_invent_confidence() -> None:
    transcript = Transcript(
        text="楼板开口没有盖板",
        detected_locale="cmn-Hans-CN",
        confidence=None,
        duration_ms=3000,
        provider="vertex-gemini-live",
        model="gemini-3.5-transcribe-live-preview",
        provider_ref="session-1",
        latency_ms=500,
    )
    assert transcript.confidence is None


def test_live_locale_is_inferred_when_preview_omits_it() -> None:
    assert detected_locale_or_infer(None, "楼板开口没有盖板") == "cmn-Hans-CN"
    assert detected_locale_or_infer(None, "Level six guardrail") == "en"
    assert detected_locale_or_infer(None, "六楼 formwork") == "mul"


def test_live_ticket_is_short_lived_and_single_use(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = Actor(ActorType.HUMAN, uuid4(), Role.REPORTER)
    settings = SimpleNamespace(
        live_transcription_enabled=True,
        transcription_rate_limit_per_minute=10,
        live_transcription_ticket_ttl_seconds=45,
    )

    async def no_rate_limit(**_: object) -> None:
        return None

    issued_tokens: dict[str, LiveTicket] = {}

    async def issue_ticket(*, actor_id, hint_locale, ttl_seconds):  # type: ignore[no-untyped-def]
        assert ttl_seconds == 45
        issued_tokens["ticket"] = LiveTicket(actor_id, Role.REPORTER, hint_locale)
        return "ticket"

    async def consume_ticket(token: str) -> LiveTicket | None:
        return issued_tokens.pop(token, None)

    monkeypatch.setattr(transcription_live, "get_settings", lambda: settings)
    monkeypatch.setattr(transcription_live, "enforce_rate_limit", no_rate_limit)
    monkeypatch.setattr(transcription_live, "issue_live_ticket", issue_ticket)
    monkeypatch.setattr(transcription_live, "consume_live_ticket", consume_ticket)
    async def exercise() -> None:
        response = await transcription_live.create_live_ticket(
            LiveTicketRequest(hint_locale="zh-CN"), actor
        )
        ticket = str(response["ticket"])
        issued = await transcription_live.consume_live_ticket(ticket)
        assert issued is not None
        assert issued.actor_id == actor.profile_id
        assert issued.role == actor.role
        assert issued.hint_locale == "zh-CN"
        assert await transcription_live.consume_live_ticket(ticket) is None

    asyncio.run(exercise())


def test_live_websocket_streams_final_text_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = Actor(ActorType.HUMAN, uuid4(), Role.REPORTER)
    token = "one-time-ticket"

    async def consume_ticket(value: str) -> LiveTicket | None:
        if value != token or actor.profile_id is None:
            return None
        return LiveTicket(actor.profile_id, Role.REPORTER, "zh-CN")

    async def store_pending(**payload: object) -> str:
        assert payload["actor_id"] == actor.profile_id
        assert payload["text"] == "六楼模板边缘没有护栏"
        return "shared-session"

    class FakeSession:
        def __init__(self) -> None:
            self.ended = asyncio.Event()

        async def send_realtime_input(self, **payload: object) -> None:
            if payload.get("audio_stream_end"):
                self.ended.set()

        async def receive(self):  # type: ignore[no-untyped-def]
            await self.ended.wait()
            final = SimpleNamespace(
                text="六楼模板边缘没有护栏",
                language_code=None,
                finished=True,
            )
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    interim_input_transcription=None,
                    input_transcription=final,
                    turn_complete=True,
                )
            )

    session = FakeSession()

    class FakeConnect:
        async def __aenter__(self) -> FakeSession:
            return session

        async def __aexit__(self, *_: object) -> None:
            return None

    fake_client = SimpleNamespace(
        aio=SimpleNamespace(live=SimpleNamespace(connect=lambda **_: FakeConnect()))
    )
    settings = SimpleNamespace(
        vertex_project_id="safe-loop",
        vertex_live_transcription_location="global",
        vertex_live_transcription_model="gemini-3.5-transcribe-live-preview",
    )
    monkeypatch.setattr(transcription_live, "get_settings", lambda: settings)
    monkeypatch.setattr(transcription_live, "make_live_client", lambda _: fake_client)
    monkeypatch.setattr(transcription_live, "consume_live_ticket", consume_ticket)
    monkeypatch.setattr(transcription_live, "store_pending_live_transcript", store_pending)
    app = FastAPI()
    app.include_router(transcription_live.router)

    with TestClient(app) as client:
        with client.websocket_connect(f"/transcribe/live?ticket={token}") as socket:
            assert socket.receive_json() == {"type": "ready"}
            socket.send_bytes(b"\0\0" * 1600)
            socket.send_text("end")
            final = socket.receive_json()
            complete = socket.receive_json()

    assert final["type"] == "final"
    assert final["detected_locale"] == "cmn-Hans-CN"
    assert complete["type"] == "complete"
    assert complete["text"] == "六楼模板边缘没有护栏"
    assert complete["session_id"] == "shared-session"
