"""
LangGraph agent state — carries all context across graph nodes.
"""
from __future__ import annotations

from typing import Annotated, Any, Optional

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    # Core conversation messages (LangChain format)
    messages: Annotated[list, add_messages]

    # Session tracking
    session_id: str

    # Accumulated leads found so far
    leads: list[dict[str, Any]]

    # Names of tools called (for reflection)
    tools_called: list[str]

    # Latest self-reflection text produced by reflection_node
    reflection: Optional[str]

    # Whether the agent has decided the task is complete
    task_complete: bool

    # Guard against infinite loops
    iteration_count: int
    max_iterations: int

    # Original user query (preserved for context)
    original_query: str

    # Any error message
    error: Optional[str]

    # Target lead count parsed from query (0 = not specified)
    target_lead_count: int
