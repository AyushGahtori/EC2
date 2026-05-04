"""
TTS Routes — /api/tts
Text → speech audio (MP3)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from loguru import logger
from pydantic import BaseModel

from app.services.tts_service import synthesize_speech

router = APIRouter(prefix="/api/tts", tags=["tts"])


class TTSRequest(BaseModel):
    text: str
    voice: str = "en-IN-NeerjaNeural"


@router.post("/")
async def text_to_speech(req: TTSRequest):
    """Convert text to speech audio. Returns MP3 bytes."""
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    audio_bytes = await synthesize_speech(req.text.strip())

    if not audio_bytes:
        raise HTTPException(
            status_code=503,
            detail="TTS service unavailable. All providers failed.",
        )

    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": "attachment; filename=aria_response.mp3",
            "Cache-Control": "no-cache",
        },
    )


@router.get("/health")
async def tts_health():
    """Check TTS provider availability."""
    import os
    rapidapi_available = bool(os.getenv("RAPIDAPI_KEY"))
    return {
        "rapidapi": rapidapi_available,
        "gtts_fallback": True,
        "status": "ready",
    }
