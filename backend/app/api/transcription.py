"""Expose authenticated transcription without creating or modifying reports."""

from __future__ import annotations

from typing import Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.ai.provider import ProviderConfigurationError
from app.ai.transcription import (
    TranscriptionFailure,
    get_transcription_provider,
)
from app.api.deps import current_actor
from app.api.rate_limits import enforce_rate_limit
from app.config import get_settings
from app.services.report_service import Actor
from app.services.transcription_service import (
    TranscriptionServiceError,
    download_audio,
    get_audio_media,
    persist_transcript,
    persist_transcription_attempt,
)

router = APIRouter(tags=["transcription"])


class TranscribeRequest(BaseModel):
    media_id: UUID
    hint_locale: Literal["zh-CN", "cmn-Hans-CN", "en-SG"] = "en-SG"


def transcription_error(error: TranscriptionServiceError) -> HTTPException:
    code_status = {
        "transcription_forbidden": status.HTTP_403_FORBIDDEN,
        "transcription_media_not_found": status.HTTP_404_NOT_FOUND,
        "transcription_media_type_invalid": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        "transcription_audio_empty": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "transcription_audio_too_large": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        "transcription_storage_not_configured": status.HTTP_503_SERVICE_UNAVAILABLE,
        "transcription_storage_failed": status.HTTP_502_BAD_GATEWAY,
    }
    return HTTPException(
        code_status.get(error.code, status.HTTP_422_UNPROCESSABLE_ENTITY),
        {"code": error.code, "message": error.message},
    )


def _provider_failure(
    failure: TranscriptionFailure,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=jsonable_encoder(
            {
                "detail": {
                    "code": failure.code,
                    "message": "transcription is temporarily unavailable",
                },
                "failure": failure.model_dump(mode="json"),
            }
        ),
    )


@router.post("/transcribe", response_model=None)
async def post_transcribe(
    payload: TranscribeRequest,
    actor: Actor = Depends(current_actor),
) -> dict[str, object] | JSONResponse:
    """Transcribe existing media and append an audit row; never mutate a report."""
    if actor.profile_id is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {"code": "profile_required", "message": "human profile is required"},
        )
    await enforce_rate_limit(
        scope="transcription",
        subject=str(actor.profile_id),
        limit=get_settings().transcription_rate_limit_per_minute,
        error_code="transcription_rate_limited",
    )
    try:
        media = await get_audio_media(payload.media_id, actor)
        audio_bytes = await download_audio(media)
    except TranscriptionServiceError as error:
        raise transcription_error(error) from error

    try:
        provider = get_transcription_provider()
    except ProviderConfigurationError:
        failure = TranscriptionFailure(
            code="provider_misconfigured",
            provider="unavailable",
            model="unavailable",
            retryable=False,
            latency_ms=0,
        )
        await persist_transcription_attempt(
            media,
            hint_locale=payload.hint_locale,
            result=failure,
            transcript_id=None,
            usable=False,
        )
        return _provider_failure(failure)
    result = await provider.transcribe(
        audio_bytes,
        media.mime_type,
        payload.hint_locale,
    )
    if isinstance(result, TranscriptionFailure):
        await persist_transcription_attempt(
            media,
            hint_locale=payload.hint_locale,
            result=result,
            transcript_id=None,
            usable=False,
        )
        return _provider_failure(result)

    stored = await persist_transcript(
        payload.media_id,
        report_id=media.report_id,
        hint_locale=payload.hint_locale,
        transcript=result,
    )
    usable = (
        result.confidence is not None
        and result.confidence >= get_settings().transcription_confidence_threshold
    )
    await persist_transcription_attempt(
        media,
        hint_locale=payload.hint_locale,
        result=result,
        transcript_id=stored["id"],
        usable=usable,
    )
    return cast(
        dict[str, object],
        jsonable_encoder(
            {
                **result.model_dump(mode="json"),
                "transcript_id": stored["id"],
                "meets_confidence_threshold": usable,
            }
        ),
    )
