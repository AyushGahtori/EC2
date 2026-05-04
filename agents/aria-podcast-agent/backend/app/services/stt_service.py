"""
STT Service — faster-whisper (local, offline)
"""
from __future__ import annotations

import os
import tempfile
from typing import Optional, Tuple

from loguru import logger

_whisper_model = None


def _get_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        # Use tiny/base for speed on CPU; upgrade to medium for GPU
        model_size = os.getenv("WHISPER_MODEL", "base")
        device = os.getenv("WHISPER_DEVICE", "cpu")
        compute_type = "int8" if device == "cpu" else "float16"
        logger.info(f"Loading Whisper model: {model_size} on {device}")
        _whisper_model = WhisperModel(model_size, device=device, compute_type=compute_type)
    return _whisper_model


async def transcribe_audio(audio_bytes: bytes, file_ext: str = ".webm") -> Tuple[str, float]:
    """
    Transcribe audio bytes using faster-whisper.
    Returns (transcript_text, avg_confidence).
    """
    if not audio_bytes:
        return "", 0.0

    tmp_path = None
    try:
        # Write to temp file
        suffix = file_ext if file_ext.startswith(".") else f".{file_ext}"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="aria_stt_")
        with os.fdopen(fd, "wb") as f:
            f.write(audio_bytes)

        model = _get_model()
        segments, info = model.transcribe(
            tmp_path,
            language="en",
            task="transcribe",
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )

        texts = []
        confidences = []
        for seg in segments:
            texts.append(seg.text.strip())
            # avg_logprob is negative; convert to 0-1
            conf = max(0.0, min(1.0, (seg.avg_logprob + 5) / 5))
            confidences.append(conf)

        transcript = " ".join(texts).strip()
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        logger.info(f"STT: '{transcript[:80]}...' | conf={avg_conf:.2f}")
        return transcript, avg_conf

    except Exception as exc:
        logger.error(f"Whisper transcription failed: {exc}")
        return "", 0.0

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
