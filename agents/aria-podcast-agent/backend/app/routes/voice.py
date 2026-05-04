"""
Voice Routes — /api/voice
Handles microphone input → STT → Agent → response text
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from loguru import logger

from app.agents.react_agent import run_agent
from app.services.memory_service import (
    append_to_session,
    get_session_history,
    get_session_mode,
)
from app.services.stt_service import transcribe_audio
from app.utils.host_controller import ModeController

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.post("/input")
async def voice_input(
    audio: UploadFile = File(...),
    session_id: Optional[str] = Form(default=None),
    mode: Optional[str] = Form(default=None),
):
    """
    Receive audio file → transcribe → run agent → return text response.
    Frontend then calls /api/tts to get audio back.
    """
    session_id = session_id or str(uuid.uuid4())

    # Read audio bytes
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    # Get file extension
    filename = audio.filename or "audio.webm"
    ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ".webm"

    # Transcribe
    transcript, confidence = await transcribe_audio(audio_bytes, file_ext=ext)

    if not transcript:
        return {
            "transcript": "",
            "response": "I couldn't catch that. Could you speak a bit more clearly?",
            "session_id": session_id,
            "mode": mode or "host",
            "confidence": confidence,
        }

    logger.info(f"Voice input: '{transcript[:80]}' | confidence={confidence:.2f}")

    # Resolve mode
    if mode:
        resolved_mode = ModeController.validate_mode(mode)
    else:
        resolved_mode = await get_session_mode(session_id)

    system_prompt = ModeController.get_system_prompt(resolved_mode)
    history = await get_session_history(session_id)
    transformed = ModeController.transform_input(transcript, resolved_mode)

    # Run agent
    response = await run_agent(
        user_message=transformed,
        system_prompt=system_prompt,
        history=history,
        session_id=session_id,
        mode=resolved_mode,
    )

    # Save to memory
    await append_to_session(session_id, "user", transcript)
    await append_to_session(session_id, "assistant", response)

    return {
        "transcript": transcript,
        "response": response,
        "session_id": session_id,
        "mode": resolved_mode,
        "confidence": round(confidence, 3),
    }
