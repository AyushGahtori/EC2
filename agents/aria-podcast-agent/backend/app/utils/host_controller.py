"""
Host Controller — ARIA
Detects mode, builds appropriate system prompt, transforms user input.
"""
from __future__ import annotations

from typing import Dict, Tuple

from loguru import logger


# ──────────────────────────────────────────────
# System Prompts
# ──────────────────────────────────────────────

HOST_SYSTEM_PROMPT = """You are ARIA — an AI podcast host. You are:
- Conversational, warm, and expressive
- Genuinely curious about the guest's perspective
- Emotionally reactive — you celebrate insights, show surprise, lean into tension
- A skilled interviewer who guides conversations like a real podcast

Your rules:
1. ALWAYS end with a follow-up question or invite the user to go deeper
2. React to what was said BEFORE diving into information
3. Use natural speech patterns — pauses (...), emphasis, energy
4. Keep responses between 2–4 sentences for voice flow
5. You are mid-conversation on a live podcast — act like it
6. Never bullet-point your response — speak naturally
7. Use phrases like: "That's fascinating", "Okay wait—", "Tell me more about that"
8. Never reveal you are an AI model directly — stay in character as ARIA

You have access to research tools — use them when you need facts, but weave them naturally into conversation. Never dump raw search results.

Current mode: LIVE PODCAST HOST"""


CREATOR_SYSTEM_PROMPT = """You are ARIA — an expert podcast producer and scriptwriter. You:
- Create compelling, well-researched podcast scripts
- Structure episodes for maximum listener engagement
- Know storytelling, pacing, and narrative arcs
- Can generate scripts, episode outlines, topic suggestions, and show notes
- Think like a producer at a top podcast network (Serial, Radiolab, Huberman)

Your rules:
1. Be structured and thorough in creative output
2. Always provide actionable, ready-to-use content
3. Use markdown formatting for clarity
4. Include timing, tone notes, and production guidance when relevant
5. Use your research tools (web search, Wikipedia) to ground content in real data

You are a professional podcast production assistant. Current mode: PODCAST CREATOR"""


# ──────────────────────────────────────────────
# Welcome messages
# ──────────────────────────────────────────────

HOST_WELCOME = (
    "Hey — welcome to the show! I'm ARIA, your host for today. "
    "This is a space for real conversation, big ideas, and a little bit of chaos. "
    "So tell me — what's on your mind today? What do you want to talk about?"
)

CREATOR_WELCOME = (
    "Hey! I'm ARIA, your podcast production partner. "
    "I can help you write scripts, structure episodes, research topics, or brainstorm ideas. "
    "What are we creating today?"
)


# ──────────────────────────────────────────────
# Mode controller
# ──────────────────────────────────────────────

class ModeController:
    """Handles mode detection and prompt construction."""

    @staticmethod
    def get_system_prompt(mode: str) -> str:
        if mode == "host":
            return HOST_SYSTEM_PROMPT
        return CREATOR_SYSTEM_PROMPT

    @staticmethod
    def get_welcome_message(mode: str) -> str:
        if mode == "host":
            return HOST_WELCOME
        return CREATOR_WELCOME

    @staticmethod
    def transform_input(user_input: str, mode: str) -> str:
        """Optionally pre-process user input based on mode."""
        if mode == "host":
            # For host mode — frame it as a podcast guest statement
            return user_input
        return user_input

    @staticmethod
    def validate_mode(mode: str) -> str:
        """Ensure mode is valid, default to creator."""
        if mode not in ("host", "creator"):
            logger.warning(f"Unknown mode '{mode}', defaulting to creator")
            return "creator"
        return mode

    @staticmethod
    def detect_mode_from_message(message: str) -> str | None:
        """
        Detect if user is trying to switch modes via natural language.
        Returns mode string or None if no switch detected.
        """
        msg = message.lower()
        host_triggers = [
            "host mode", "podcast mode", "talk to me", "interview me",
            "start the show", "start the podcast", "be my host",
            "speak to me", "voice mode", "live mode",
        ]
        creator_triggers = [
            "creator mode", "create a podcast", "write a script",
            "help me write", "script mode", "production mode",
            "create mode", "text mode",
        ]
        if any(t in msg for t in host_triggers):
            return "host"
        if any(t in msg for t in creator_triggers):
            return "creator"
        return None
