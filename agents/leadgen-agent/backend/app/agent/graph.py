"""
LangGraph agent graph.
Flow: planner → tool_node → reflection → decision → (continue | end)
The LLM drives all decisions. The graph provides structure only.
"""
from __future__ import annotations

import json
import logging
import re
from typing import AsyncGenerator

from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

from app.agent.nodes import (
    decision_node,
    planner_node,
    reflection_node,
    tool_node,
)
from app.agent.state import AgentState
from app.config.settings import settings
from app.tools.storage import storage

logger = logging.getLogger(__name__)


def _has_tool_calls(state: AgentState) -> str:
    """Route after planner: if LLM made tool calls → tool_node, else → end."""
    for msg in reversed(state["messages"]):
        from langchain_core.messages import AIMessage
        if isinstance(msg, AIMessage):
            if getattr(msg, "tool_calls", None):
                return "tool_node"
            return "end"
    return "end"


def build_graph() -> StateGraph:
    """Build and compile the LangGraph agent."""
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("planner", planner_node)
    graph.add_node("tool_node", tool_node)
    graph.add_node("reflect", reflection_node)

    # Entry point
    graph.set_entry_point("planner")

    # After planner: route to tool_node if tool calls present, else end
    graph.add_conditional_edges(
        "planner",
        _has_tool_calls,
        {"tool_node": "tool_node", "end": END},
    )

    # After tool execution: always go to reflection
    graph.add_edge("tool_node", "reflect")

    # After reflection: route via decision_node
    graph.add_conditional_edges(
        "reflect",
        decision_node,
        {"continue": "planner", "end": END},
    )

    return graph.compile()


# Singleton compiled graph
_compiled_graph = None


def get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def _parse_target_count(query: str) -> int:
    """Extract requested lead count from query (e.g. '100 leads' → 100)."""
    m = re.search(r"\b(\d+)\b", query)
    return int(m.group(1)) if m else 0


async def _persist_final_leads(final_state: dict) -> int:
    """Best-effort persistence for the leads accumulated in graph state."""
    leads = final_state.get("leads") or []
    session_id = final_state.get("session_id", "default")
    original_query = final_state.get("original_query", "")

    if not leads:
        return 0

    try:
        result = await storage.ainvoke({
            "leads": json.dumps(leads, ensure_ascii=False),
            "session_id": session_id,
            "original_query": original_query,
        })
        parsed = json.loads(result) if isinstance(result, str) else result
        return int(parsed.get("saved_count", 0))
    except Exception as exc:
        logger.warning(f"Final lead persistence failed: {exc}")
        return 0


async def run_agent(
    query: str,
    session_id: str,
    conversation_history: list[dict],
) -> AsyncGenerator[str, None]:
    """
    Run the agent and yield streamed text chunks.

    Args:
        query: User's natural language request
        session_id: Session ID for memory/storage
        conversation_history: Previous messages from Redis

    Yields:
        Text chunks as the agent thinks and acts
    """
    graph = get_compiled_graph()

    # Convert Redis history to LangChain messages
    lc_history = []
    for msg in conversation_history[-10:]:  # Last 10 messages for context window
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            lc_history.append(HumanMessage(content=content))

    initial_state: AgentState = {
        "messages": lc_history + [HumanMessage(content=query)],
        "session_id": session_id,
        "leads": [],
        "tools_called": [],
        "reflection": None,
        "task_complete": False,
        "iteration_count": 0,
        "max_iterations": settings.MAX_ITERATIONS,
        "original_query": query,
        "error": None,
        "target_lead_count": _parse_target_count(query),
    }

    final_state = None

    # Stream events from the graph
    async for event in graph.astream_events(initial_state, version="v2"):
        kind = event.get("event", "")
        name = event.get("name", "")

        if kind == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                yield chunk.content

        elif kind == "on_tool_start":
            tool_name = name
            inputs = event.get("data", {}).get("input", {})
            yield f"\n\n🔧 **Using tool**: `{tool_name}`\n"
            if inputs:
                # Show brief summary of what we're searching
                for k, v in inputs.items():
                    if isinstance(v, str) and len(v) < 100:
                        yield f"   → {k}: {v}\n"

        elif kind == "on_tool_end":
            tool_name = name
            output = event.get("data", {}).get("output", "")
            try:
                data = __import__("json").loads(str(output))
                # Summarize key metrics
                if "total_found" in data:
                    yield f"   ✓ Found {data['total_found']} results\n"
                elif "saved_count" in data:
                    yield f"   ✓ Saved {data['saved_count']} leads to database\n"
                elif "score" in data:
                    yield f"   ✓ Score: {data['score']}/100\n"
            except Exception:
                pass

        elif kind == "on_chain_end" and name == "LangGraph":
            final_state = event.get("data", {}).get("output")

    # Final summary
    if final_state:
        await _persist_final_leads(final_state)
        lead_count = len(final_state.get("leads", []))
        yield f"\n\n---\n✅ **Task Complete**\n"
        yield f"- **Leads found**: {lead_count}\n"
        yield f"- **Iterations**: {final_state.get('iteration_count', 0)}\n"
        yield f"- **Tools used**: {len(set(final_state.get('tools_called', [])))}\n"
