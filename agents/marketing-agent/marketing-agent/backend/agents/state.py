"""
LangGraph agent state schema.
The state is the single source of truth for the entire agent conversation.
"""
from __future__ import annotations

from typing import Annotated, Any, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """
    Complete state carried through the LangGraph ReAct loop.

    messages       : Full conversation history (LangGraph manages additions)
    session_id     : Links to MongoDB/Redis context
    product_image_b64  : Raw base64 image for vision LLM calls
    product_image_url  : URL of uploaded image (served by FastAPI)
    product_context    : Rich text summary of the analysed product
    brand_guidelines   : Optional user-provided brand/style rules
    current_phase  : Current ReAct phase (think/plan/act/observe/respond)
    plan           : LLM-generated action plan as list of steps
    observations   : Running list of tool observations this turn
    iteration      : How many tool calls have been made this turn
    max_iterations : Safety ceiling to prevent infinite loops
    stream_queue   : Async queue used to stream events to frontend (injected per-request)
    """
    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
    product_image_b64: Optional[str]
    product_image_url: Optional[str]
    product_context: Optional[str]
    brand_guidelines: Optional[str]
    latest_poster_html: Optional[str]
    current_phase: Optional[str]
    plan: Optional[list[str]]
    observations: list[str]
    iteration: int
    max_iterations: int
    # Injected at runtime — not serialised
    stream_queue: Optional[Any]
