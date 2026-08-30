"""Authenticate, stream, and commit Gemini Live transcripts."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from app.ai.live_transcription import (
    LIVE_AUDIO_MIME_TYPE,
    LiveTranscriptionConfig,
    detected_locale_or_infer,
    live_connect_config,
    make_live_client,
)
from app.ai.transcription import Transcript
from app.api.deps import current_actor
from app.api.rate_limits import enforce_rate_limit
from app.config import get_settings
from app.observability import log_event, track_exception
from app.services.report_service import Actor
from app.services.live_transcription_store import (
    consume_live_ticket,
    consume_pending_live_transcript,
    issue_live_ticket,
    store_pending_live_transcript,
)
from app.services.transcription_service import (
    TranscriptionServiceError,
    get_audio_media,
    persist_transcript,
    persist_transcription_attempt,
)

router = APIRouter(tags=["transcription"])
logger = logging.getLogger(__name__)
_MAX_PCM_BYTES = 16000 * 2 * 120
_FINAL_WAIT_SECONDS = 10.0


class LiveTicketRequest(BaseModel):
    hint_locale: Literal["zh-CN", "cmn-Hans-CN", "en-SG"] = "en-SG"


class LiveCommitRequest(BaseModel):
    session_id: str
    media_id: UUID


def _now() -> float:
    return time.monotonic()


@router.post("/transcribe/live/ticket")
async def create_live_ticket(
    payload: LiveTicketRequest,
    actor: Actor = Depends(current_actor),
) -> dict[str, object]:
    settings = get_settings()
    if not settings.live_transcription_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {"code": "live_transcription_disabled", "message": "live transcription is disabled"},
        )
    if actor.profile_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, {"code": "profile_required"})
    await enforce_rate_limit(
        scope="transcription_live",
        subject=str(actor.profile_id),
        limit=settings.transcription_rate_limit_per_minute,
        error_code="transcription_rate_limited",
    )
    ticket = await issue_live_ticket(
        actor_id=actor.profile_id,
        hint_locale=payload.hint_locale,
        ttl_seconds=settings.live_transcription_ticket_ttl_seconds,
    )
    return {"ticket": ticket, "expires_in": settings.live_transcription_ticket_ttl_seconds}


@router.websocket("/transcribe/live")
async def transcribe_live(websocket: WebSocket, ticket: str = Query()) -> None:
    issued = await consume_live_ticket(ticket)
    if issued is None:
        log_event(
            logger,
            logging.WARNING,
            "live_transcription_ticket_rejected",
            code="invalid_or_expired_ticket",
        )
        await websocket.close(code=4401, reason="invalid live transcription ticket")
        return
    settings = get_settings()
    config = LiveTranscriptionConfig.from_settings(settings)
    started = _now()
    received_bytes = 0
    final_parts: list[str] = []
    detected_locale = "und"
    provider_ref = secrets.token_urlsafe(18)
    final_ready = asyncio.Event()
    await websocket.accept()
    try:
        client = make_live_client(config)
        async with client.aio.live.connect(
            model=config.model,
            config=live_connect_config(issued.hint_locale),
        ) as session:
            # google-genai consumes setup_complete before yielding AsyncSession.
            await websocket.send_json({"type": "ready"})

            async def forward_results() -> None:
                nonlocal detected_locale
                async for message in session.receive():
                    content = message.server_content
                    if content is None:
                        continue
                    interim = content.interim_input_transcription
                    if interim is not None and interim.text:
                        await websocket.send_json({"type": "interim", "text": interim.text})
                    final = content.input_transcription
                    if final is not None and final.text:
                        if not final_parts or final_parts[-1] != final.text:
                            final_parts.append(final.text)
                        detected_locale = detected_locale_or_infer(
                            final.language_code,
                            " ".join(final_parts),
                        )
                        await websocket.send_json(
                            {"type": "final", "text": " ".join(final_parts), "detected_locale": detected_locale}
                        )
                        final_ready.set()
                    if content.turn_complete:
                        final_ready.set()

            receiver = asyncio.create_task(forward_results())
            try:
                while True:
                    message = await websocket.receive()
                    if chunk := message.get("bytes"):
                        received_bytes += len(chunk)
                        if received_bytes > _MAX_PCM_BYTES:
                            await websocket.send_json({"type": "failure", "code": "audio_too_large"})
                            return
                        await session.send_realtime_input(
                            audio={"data": chunk, "mime_type": LIVE_AUDIO_MIME_TYPE}
                        )
                        continue
                    if message.get("type") == "websocket.disconnect":
                        return
                    text = message.get("text")
                    if text == "end":
                        await session.send_realtime_input(audio_stream_end=True)
                        try:
                            await asyncio.wait_for(final_ready.wait(), timeout=_FINAL_WAIT_SECONDS)
                        except TimeoutError:
                            pass
                        await asyncio.sleep(0.75)
                        break
            finally:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)
    except WebSocketDisconnect:
        return
    except Exception as error:
        track_exception(
            logger,
            "live_transcription_provider_failed",
            error,
            code="provider_unavailable",
        )
        await websocket.send_json({"type": "failure", "code": "provider_unavailable"})
        await websocket.close(code=1011)
        return

    text = " ".join(part.strip() for part in final_parts if part.strip()).strip()
    if not text:
        await websocket.send_json({"type": "failure", "code": "empty_transcript"})
        await websocket.close(code=1000)
        return
    elapsed_ms = max(0, round((_now() - started) * 1000))
    try:
        session_id = await store_pending_live_transcript(
            actor_id=issued.actor_id,
            hint_locale=issued.hint_locale,
            text=text,
            detected_locale=detected_locale,
            duration_ms=round(received_bytes / (16000 * 2) * 1000),
            provider_ref=provider_ref,
            latency_ms=elapsed_ms,
        )
    except Exception as error:
        track_exception(
            logger,
            "live_transcription_state_store_failed",
            error,
            code="state_store_unavailable",
        )
        await websocket.send_json({"type": "failure", "code": "state_store_unavailable"})
        await websocket.close(code=1011)
        return
    await websocket.send_json(
        {"type": "complete", "session_id": session_id, "text": text, "detected_locale": detected_locale}
    )
    await websocket.close(code=1000)


@router.post("/transcribe/live/commit")
async def commit_live_transcript(
    payload: LiveCommitRequest,
    actor: Actor = Depends(current_actor),
) -> dict[str, object]:
    if actor.profile_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, {"code": "profile_required"})
    pending = await consume_pending_live_transcript(payload.session_id, actor.profile_id)
    if pending is None:
        log_event(
            logger,
            logging.WARNING,
            "live_transcription_commit_rejected",
            code="live_transcript_not_found",
        )
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            {"code": "live_transcript_not_found", "message": "live transcript expired or is unavailable"},
        )
    try:
        media = await get_audio_media(payload.media_id, actor)
    except TranscriptionServiceError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"code": error.code}) from error
    transcript = Transcript(
        text=pending.text,
        detected_locale=pending.detected_locale,
        confidence=None,
        duration_ms=pending.duration_ms,
        provider="vertex-gemini-live",
        model=get_settings().vertex_live_transcription_model,
        provider_ref=pending.provider_ref,
        latency_ms=pending.latency_ms,
    )
    stored = await persist_transcript(
        payload.media_id,
        report_id=media.report_id,
        hint_locale=pending.hint_locale,
        transcript=transcript,
    )
    await persist_transcription_attempt(
        media,
        hint_locale=pending.hint_locale,
        result=transcript,
        transcript_id=stored["id"],
        usable=True,
    )
    return cast(
        dict[str, object],
        jsonable_encoder(
            {
                **transcript.model_dump(mode="json"),
                "transcript_id": stored["id"],
                "meets_confidence_threshold": True,
            }
        ),
    )
