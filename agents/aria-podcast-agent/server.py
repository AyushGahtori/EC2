from __future__ import annotations

import base64
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
try:
    from pydantic import ConfigDict
except Exception:  # pragma: no cover
    ConfigDict = None  # type: ignore

ROOT = Path(__file__).resolve().parent
EC2_ROOT = ROOT.parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(EC2_ROOT))

from ec2_shared.agent_response import as_text, card, failed, needs_input, require_fields, success
from ec2_shared.ui import render_agent_window

AGENT_ID = "aria-podcast-agent"
AGENT_NAME = "ARIA Podcast Agent"


class ActionRequest(BaseModel):
    action: str | None = None
    if ConfigDict:
        model_config = ConfigDict(extra="allow")
    else:
        class Config:
            extra = "allow"


def _payload(model: ActionRequest) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _decode_base64(value: str) -> bytes:
    if "," in value and value.startswith("data:"):
        value = value.split(",", 1)[1]
    return base64.b64decode(value)


async def _resolve_mode(payload: dict[str, Any], message: str, session_id: str) -> str:
    from app.services.memory_service import get_session_mode, set_session_mode
    from app.utils.host_controller import ModeController

    explicit_mode = as_text(payload.get("mode"))
    if explicit_mode:
        mode = ModeController.validate_mode(explicit_mode)
        await set_session_mode(session_id, mode)
        return mode
    detected = ModeController.detect_mode_from_message(message)
    if detected:
        await set_session_mode(session_id, detected)
        return detected
    return await get_session_mode(session_id)


async def _chat(payload: dict[str, Any], action: str) -> dict[str, Any]:
    prompt = as_text(payload.get("prompt") or payload.get("message") or payload.get("query") or payload.get("parameters"))
    if not prompt:
        return needs_input(agent=AGENT_ID, action=action, message="Tell ARIA what to host, script, research, or brainstorm.", missing_fields=["prompt"])

    from app.agents.react_agent import run_agent
    from app.services.memory_service import append_to_session, get_session_history
    from app.utils.host_controller import ModeController

    session_id = as_text(payload.get("session_id") or payload.get("sessionId") or payload.get("chatId")) or str(uuid.uuid4())
    mode = await _resolve_mode(payload, prompt, session_id)
    system_prompt = ModeController.get_system_prompt(mode)
    history = await get_session_history(session_id)
    transformed = ModeController.transform_input(prompt, mode)
    response = await run_agent(
        user_message=transformed,
        system_prompt=system_prompt,
        history=history,
        session_id=session_id,
        mode=mode,
    )
    await append_to_session(session_id, "user", prompt)
    await append_to_session(session_id, "assistant", response)
    return success(
        agent=AGENT_ID,
        action=action,
        summary=response,
        result={"session_id": session_id, "mode": mode, "response": response},
        cards=[
            card("ARIA response", response, {"mode": mode, "session_id": session_id}),
            card("Mode", "Host mode is conversational; creator mode is structured for scripts and production work.", {"current_mode": mode}),
        ],
        logs=["Ran the copied ARIA ReAct agent.", "Session mode and history were preserved through the source memory service."],
        next_actions=["Switch host/creator mode", "Generate TTS audio", "Continue this podcast session"],
    )


async def _mode(payload: dict[str, Any], action: str) -> dict[str, Any]:
    missing = require_fields(AGENT_ID, action, payload, ["mode"])
    if missing:
        return missing
    from app.services.memory_service import set_session_mode
    from app.utils.host_controller import ModeController

    session_id = as_text(payload.get("session_id") or payload.get("sessionId") or payload.get("chatId")) or str(uuid.uuid4())
    mode = ModeController.validate_mode(as_text(payload.get("mode")))
    await set_session_mode(session_id, mode)
    welcome = ModeController.get_welcome_message(mode)
    return success(
        agent=AGENT_ID,
        action=action,
        summary=welcome,
        result={"session_id": session_id, "mode": mode, "welcome": welcome},
        cards=[card("Mode switched", welcome, {"mode": mode, "session_id": session_id})],
    )


async def _history(payload: dict[str, Any], action: str) -> dict[str, Any]:
    from app.services.memory_service import get_session_history, get_session_mode, list_conversations, clear_session

    if action == "list_sessions":
        sessions = await list_conversations(limit=int(payload.get("limit") or 20))
        for item in sessions:
            if isinstance(item, dict):
                item.pop("_id", None)
        return success(agent=AGENT_ID, action=action, summary=f"Loaded {len(sessions)} ARIA sessions.", result={"sessions": sessions}, cards=[card("ARIA sessions", f"{len(sessions)} sessions returned.")])

    missing = require_fields(AGENT_ID, action, payload, ["session_id"])
    if missing:
        return missing
    session_id = as_text(payload.get("session_id"))
    if action == "clear_history":
        await clear_session(session_id)
        return success(agent=AGENT_ID, action=action, summary="ARIA session history cleared.", result={"session_id": session_id, "cleared": True}, cards=[card("History cleared", "Session memory was cleared.", {"session_id": session_id})])
    history = await get_session_history(session_id)
    mode = await get_session_mode(session_id)
    return success(agent=AGENT_ID, action=action, summary=f"Loaded {len(history)} ARIA messages.", result={"session_id": session_id, "mode": mode, "history": history}, cards=[card("ARIA history", f"{len(history)} messages found.", {"mode": mode})])


async def _tts(payload: dict[str, Any], action: str) -> dict[str, Any]:
    text = as_text(payload.get("text") or payload.get("prompt") or payload.get("message"))
    if not text:
        return needs_input(agent=AGENT_ID, action=action, message="Provide text for ARIA to synthesize.", missing_fields=["text"])
    from app.services.tts_service import synthesize_speech

    audio_bytes = await synthesize_speech(text)
    if not audio_bytes:
        return failed(agent=AGENT_ID, action=action, public_message="ARIA could not synthesize audio with the configured providers.", error="empty_audio", code="TTS_UNAVAILABLE")
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
    return success(
        agent=AGENT_ID,
        action=action,
        summary="ARIA generated podcast audio.",
        result={"audio_base64": audio_b64, "mime_type": "audio/mpeg", "size": len(audio_bytes)},
        cards=[card("Audio ready", "The audio payload is available to the UI as base64 MP3.", {"mime_type": "audio/mpeg", "bytes": len(audio_bytes)})],
        next_actions=["Play or download the audio", "Continue script editing"],
    )


async def _voice(payload: dict[str, Any], action: str) -> dict[str, Any]:
    audio = as_text(payload.get("audio_base64") or payload.get("file_data_url"))
    if not audio:
        return needs_input(agent=AGENT_ID, action=action, message="Attach or provide an audio recording for voice input.", missing_fields=["audio_base64"])
    from app.services.stt_service import transcribe_audio

    file_name = as_text(payload.get("file_name")) or "audio.webm"
    ext = "." + file_name.rsplit(".", 1)[-1] if "." in file_name else ".webm"
    audio_bytes = _decode_base64(audio)
    transcript, confidence = await transcribe_audio(audio_bytes, file_ext=ext)
    if not transcript:
        return success(
            agent=AGENT_ID,
            action=action,
            summary="ARIA could not clearly transcribe the audio.",
            result={"transcript": "", "confidence": confidence},
            cards=[card("Voice input", "I could not catch that clearly. Please try a cleaner recording.", {"confidence": round(confidence, 3)})],
        )
    next_payload = {**payload, "prompt": transcript}
    response = await _chat(next_payload, "chat")
    response["action"] = action
    response["result"] = {**response.get("result", {}), "transcript": transcript, "confidence": round(confidence, 3)}
    response["ui_payload"]["cards"].insert(0, card("Transcript", transcript, {"confidence": round(confidence, 3)}))
    return response


CAPABILITIES = [
    {"name": "chat", "label": "Chat With ARIA", "description": "Host a podcast conversation or create podcast assets.", "required": ["prompt"], "optional": ["session_id", "mode"]},
    {"name": "creator_script", "label": "Creator Script", "description": "Write scripts, outlines, show notes, topic ideas, and production guidance.", "required": ["prompt"], "optional": ["session_id"]},
    {"name": "host_conversation", "label": "Host Conversation", "description": "Run warm live-host interview mode.", "required": ["prompt"], "optional": ["session_id"]},
    {"name": "switch_mode", "label": "Switch Mode", "description": "Switch a session between host and creator modes.", "required": ["mode"], "optional": ["session_id"]},
    {"name": "voice_input", "label": "Voice Input", "description": "Transcribe audio, run ARIA, and return the response.", "required": ["audio_base64"], "optional": ["session_id", "mode", "file_name"]},
    {"name": "text_to_speech", "label": "Text To Speech", "description": "Generate MP3 audio from ARIA text.", "required": ["text"], "optional": ["voice"]},
    {"name": "get_history", "label": "Get History", "description": "Load ARIA session history.", "required": ["session_id"], "optional": []},
    {"name": "clear_history", "label": "Clear History", "description": "Clear one ARIA session.", "required": ["session_id"], "optional": []},
    {"name": "list_sessions", "label": "List Sessions", "description": "List recent ARIA sessions.", "required": [], "optional": ["limit"]},
    {"name": "list_capabilities", "label": "List Capabilities", "description": "Show ARIA capabilities.", "required": [], "optional": []},
]

UI_SPEC = {
    "name": AGENT_NAME,
    "description": "Podcast host and creator agent with host/creator modes, memory, voice input, transcription, and text-to-speech.",
    "endpoint": "/aria/action",
    "actions": CAPABILITIES,
    "examples": [
        "Host mode: interview me about how AI is changing software teams.",
        "Creator mode: write a 12-minute podcast script about climate tech investing.",
        "Turn this intro into natural podcast host banter and then create show notes.",
    ],
    "scope": [
        "Only podcast hosting, scripting, research, voice input, and audio output are in scope.",
        "Mode, history, STT, TTS, and ReAct tools are preserved from the copied source.",
    ],
    "usage": [
        "Use host_conversation for live podcast energy.",
        "Use creator_script for structured production output.",
        "Use voice_input with an audio attachment and text_to_speech for spoken output.",
    ],
}

app = FastAPI(title=AGENT_NAME, version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    return render_agent_window(UI_SPEC)


@app.get("/aria/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "agent": AGENT_ID, "actions": [item["name"] for item in CAPABILITIES]}


@app.post("/aria/action")
async def action(request: ActionRequest) -> dict[str, Any]:
    payload = _payload(request)
    selected = as_text(payload.get("action") or "chat")
    try:
        if selected in {"chat", "creator_script", "host_conversation"}:
            if selected == "creator_script":
                payload["mode"] = "creator"
            if selected == "host_conversation":
                payload["mode"] = "host"
            return await _chat(payload, selected)
        if selected == "switch_mode":
            return await _mode(payload, selected)
        if selected == "voice_input":
            return await _voice(payload, selected)
        if selected == "text_to_speech":
            return await _tts(payload, selected)
        if selected in {"get_history", "clear_history", "list_sessions"}:
            return await _history(payload, selected)
        if selected == "list_capabilities":
            return success(agent=AGENT_ID, action=selected, summary="ARIA capabilities loaded.", result={"actions": CAPABILITIES}, cards=[card("Capabilities", "ARIA supports podcast chat, creator scripts, host mode, voice input, session memory, and TTS.", {"actions": ", ".join(item["name"] for item in CAPABILITIES)})])
        return needs_input(agent=AGENT_ID, action=selected, message=f"ARIA does not expose the action '{selected}'.", missing_fields=["action"])
    except Exception as exc:
        return failed(agent=AGENT_ID, action=selected, public_message="ARIA could not complete this request yet.", error=exc)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8052)
