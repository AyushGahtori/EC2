"""
ARIA ReAct Agent — LangGraph-based Think → Act → Observe loop.

Rules:
  - Max 5 steps
  - No tool used twice in same run
  - Returns clean final answer (no internal JSON leaked)
"""
from __future__ import annotations

import os
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from loguru import logger
from typing_extensions import Annotated, TypedDict

from app.agents.tools import ALL_TOOLS
from app.services.llm_service import get_llm

MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "5"))


# ──────────────────────────────────────────────
# Graph state
# ──────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    steps: int
    used_tools: List[str]
    mode: str            # "host" | "creator"
    session_id: str
    final_answer: Optional[str]


# ──────────────────────────────────────────────
# Nodes
# ──────────────────────────────────────────────

def build_agent_node(llm_with_tools):
    async def agent_node(state: AgentState) -> Dict:
        steps = state.get("steps", 0)
        if steps >= MAX_STEPS:
            # Force final answer
            last_ai = next(
                (m for m in reversed(state["messages"]) if isinstance(m, AIMessage)),
                None,
            )
            content = (
                last_ai.content if last_ai and last_ai.content
                else "I have gathered enough information to answer your question."
            )
            return {
                "messages": [AIMessage(content=content)],
                "final_answer": content,
                "steps": steps,
            }

        response = await llm_with_tools.ainvoke(state["messages"])
        return {
            "messages": [response],
            "steps": steps + 1,
        }

    return agent_node


def build_tool_node(used_tools_guard: bool = True):
    tool_node = ToolNode(ALL_TOOLS)

    async def guarded_tool_node(state: AgentState) -> Dict:
        used = state.get("used_tools", [])
        last_ai = state["messages"][-1]

        if hasattr(last_ai, "tool_calls") and last_ai.tool_calls:
            # Filter out already-used tools
            filtered_calls = [
                tc for tc in last_ai.tool_calls
                if tc["name"] not in used
            ]
            new_used = used + [tc["name"] for tc in filtered_calls]

            if not filtered_calls:
                # All tools already used — skip
                return {
                    "messages": [
                        ToolMessage(
                            content="Tool already used. Using existing information to answer.",
                            tool_call_id=last_ai.tool_calls[0]["id"],
                        )
                    ],
                    "used_tools": used,
                }

            # Replace tool_calls with filtered ones
            last_ai.tool_calls = filtered_calls

        result = await tool_node.ainvoke(state)
        return {**result, "used_tools": new_used if "new_used" in dir() else used}

    return guarded_tool_node


# ──────────────────────────────────────────────
# Routing
# ──────────────────────────────────────────────

def should_continue(state: AgentState) -> Literal["tools", "end"]:
    last = state["messages"][-1]
    steps = state.get("steps", 0)

    if steps >= MAX_STEPS:
        return "end"
    if state.get("final_answer"):
        return "end"
    if isinstance(last, AIMessage) and hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return "end"


# ──────────────────────────────────────────────
# Graph builder
# ──────────────────────────────────────────────

def build_agent_graph():
    llm = get_llm(streaming=False)
    llm_with_tools = llm.bind_tools(ALL_TOOLS)

    graph = StateGraph(AgentState)
    graph.add_node("agent", build_agent_node(llm_with_tools))
    graph.add_node("tools", build_tool_node())

    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    graph.add_edge("tools", "agent")

    return graph.compile()


_graph = None


def get_agent_graph():
    global _graph
    if _graph is None:
        _graph = build_agent_graph()
    return _graph


# ──────────────────────────────────────────────
# Public run interface
# ──────────────────────────────────────────────

async def run_agent(
    user_message: str,
    system_prompt: str,
    history: List[Dict],
    session_id: str = "default",
    mode: str = "creator",
) -> str:
    """Run the ReAct agent and return the final clean text response."""
    graph = get_agent_graph()

    messages: List[BaseMessage] = [SystemMessage(content=system_prompt)]

    # Add history (last 10 turns to stay within context)
    for h in history[-10:]:
        if h["role"] == "user":
            messages.append(HumanMessage(content=h["content"]))
        elif h["role"] == "assistant":
            messages.append(AIMessage(content=h["content"]))

    messages.append(HumanMessage(content=user_message))

    initial_state: AgentState = {
        "messages": messages,
        "steps": 0,
        "used_tools": [],
        "mode": mode,
        "session_id": session_id,
        "final_answer": None,
    }

    try:
        final_state = await graph.ainvoke(initial_state)
        last_ai = next(
            (m for m in reversed(final_state["messages"]) if isinstance(m, AIMessage)),
            None,
        )
        if last_ai and last_ai.content:
            response = last_ai.content
            if isinstance(response, list):
                # Handle content blocks (Anthropic style)
                response = " ".join(
                    b["text"] for b in response if isinstance(b, dict) and b.get("type") == "text"
                )
            return str(response).strip()
        return "I wasn't able to generate a response. Please try again."
    except Exception as exc:
        logger.error(f"Agent run failed: {exc}")
        return f"I encountered an issue processing your request. Please try again."


async def run_agent_stream(
    user_message: str,
    system_prompt: str,
    history: List[Dict],
    session_id: str = "default",
    mode: str = "creator",
) -> AsyncGenerator[str, None]:
    """Stream agent response token by token."""
    llm = get_llm(streaming=True)

    messages: List[BaseMessage] = [SystemMessage(content=system_prompt)]
    for h in history[-10:]:
        if h["role"] == "user":
            messages.append(HumanMessage(content=h["content"]))
        elif h["role"] == "assistant":
            messages.append(AIMessage(content=h["content"]))
    messages.append(HumanMessage(content=user_message))

    try:
        async for chunk in llm.astream(messages):
            if hasattr(chunk, "content") and chunk.content:
                content = chunk.content
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            yield block["text"]
                elif isinstance(content, str):
                    yield content
    except Exception as exc:
        logger.error(f"Agent stream failed: {exc}")
        yield "I encountered an issue. Please try again."
