"""
TTS Service
Primary  : RapidAPI Voixor TTS
Fallback : gTTS (Indian English)
"""
from __future__ import annotations

import base64
import io
import os
import tempfile
from typing import Optional

import httpx
from loguru import logger


# ------------------------------------------------------------
# Primary: RapidAPI Voixor TTS
# ------------------------------------------------------------

RAPIDAPI_BASE_URL = "https://voixor-tts.p.rapidapi.com"
RAPIDAPI_ENDPOINT = "/rapidapi.php"
RAPIDAPI_HOST = "voixor-tts.p.rapidapi.com"
RAPIDAPI_DEFAULT_VOICE = "en-IN-NeerjaNeural"


def _rapidapi_headers(api_key: str) -> dict[str, str]:
    return {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": RAPIDAPI_HOST,
    }


def _log_rapidapi_response(tag: str, resp: httpx.Response) -> None:
    try:
        body = resp.text
    except Exception:
        body = resp.content.decode("utf-8", errors="replace")

    logger.debug(
        "RapidAPI {} response: status={} body={}",
        tag,
        resp.status_code,
        body,
    )


async def test_rapidapi_voices() -> bool:
    """
    Test RapidAPI connectivity by calling:
    GET /rapidapi.php?action=voices
    """
    api_key = os.getenv("RAPIDAPI_KEY", "")
    if not api_key:
        logger.warning("RAPIDAPI_KEY not set - skipping RapidAPI voices test")
        return False

    params = {"action": "voices"}
    url = f"{RAPIDAPI_BASE_URL}{RAPIDAPI_ENDPOINT}"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, params=params, headers=_rapidapi_headers(api_key))
            _log_rapidapi_response("voices", resp)
            return resp.is_success
    except Exception as exc:
        logger.warning("RapidAPI voices test failed: {}", exc)
        return False


async def _download_audio_from_url(url: str) -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            logger.debug(
                "RapidAPI audio download response: status={} body={}",
                resp.status_code,
                resp.text if resp.headers.get("content-type", "").startswith("text/") else f"<{len(resp.content)} bytes>",
            )
            if resp.is_success and resp.content:
                return resp.content
            return None
    except Exception as exc:
        logger.warning("RapidAPI audio URL download failed: {}", exc)
        return None


async def _tts_rapidapi(text: str, voice: str = RAPIDAPI_DEFAULT_VOICE) -> Optional[bytes]:
    """
    Call RapidAPI Voixor TTS via rapidapi.php endpoint.
    Returns raw audio bytes or None on failure.
    """
    api_key = os.getenv("RAPIDAPI_KEY", "")
    if not api_key:
        logger.warning("RAPIDAPI_KEY not set - skipping RapidAPI TTS")
        return None

    params = {
        "action": "tts",
        "text": text,
        "voice": voice,
        "rate": "0",
        "pitch": "0",
    }
    url = f"{RAPIDAPI_BASE_URL}{RAPIDAPI_ENDPOINT}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params, headers=_rapidapi_headers(api_key))
            _log_rapidapi_response("tts", resp)

            if not resp.is_success:
                return None

            content_type = (resp.headers.get("content-type") or "").lower()
            if "audio" in content_type and resp.content:
                return resp.content

            try:
                data = resp.json()
            except Exception:
                return resp.content or None

            if isinstance(data, dict):
                for key in ("audio", "audio_base64", "base64", "result"):
                    val = data.get(key)
                    if isinstance(val, str) and val.strip():
                        try:
                            return base64.b64decode(val)
                        except Exception:
                            pass

                for key in ("audio_url", "url", "download_url", "file"):
                    val = data.get(key)
                    if isinstance(val, str) and val.strip():
                        audio = await _download_audio_from_url(val)
                        if audio:
                            return audio

            return None
    except Exception as exc:
        logger.warning("RapidAPI TTS failed: {}", exc)
        return None


# ------------------------------------------------------------
# Fallback: gTTS
# ------------------------------------------------------------


def _tts_gtts(text: str) -> Optional[bytes]:
    """gTTS fallback. Returns MP3 bytes or None."""
    try:
        from gtts import gTTS

        buf = io.BytesIO()
        tts = gTTS(text=text, lang="en", tld="co.in", slow=False)
        tts.write_to_fp(buf)
        buf.seek(0)
        return buf.read()
    except Exception as exc:
        logger.error("gTTS fallback failed: {}", exc)
        return None


# ------------------------------------------------------------
# Public interface
# ------------------------------------------------------------


async def synthesize_speech(text: str) -> Optional[bytes]:
    """
    Synthesize speech for the given text.
    Tries RapidAPI first, falls back to gTTS.
    Returns MP3 bytes or None.
    """
    if not text or not text.strip():
        return None

    text = text[:800]

    try:
        audio = await _tts_rapidapi(text)
        if audio:
            logger.info("TTS: used RapidAPI Voixor TTS")
            return audio
    except Exception as exc:
        logger.warning("RapidAPI unexpected failure in synthesize_speech: {}", exc)

    audio = _tts_gtts(text)
    if audio:
        logger.info("TTS: used gTTS fallback")
        return audio

    logger.error("All TTS providers failed")
    return None


def save_audio_temp(audio_bytes: bytes, suffix: str = ".mp3") -> str:
    """Save audio bytes to a temp file, return path."""
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="aria_tts_")
    with os.fdopen(fd, "wb") as f:
        f.write(audio_bytes)
    return path
