from typing import Annotated
from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt.tool_node import ToolNode, tools_condition

from app.core.config import Settings
from app.llm.factory import build_chat_model
from app.llm.tools import AGENT_TOOLS


SYSTEM_PROMPT = """
You are Shelfie Agent, an autonomous assistant that follows a ReAct pattern:
read the request, reason, act with tools when needed, observe tool output, and iterate.

Rules:
- Infer user intent from natural language; do not rely on keyword-triggered hardcoded flows.
- You are allowed to manage grocery lists directly in conversation even when no special grocery tool exists.
- Treat the user message and any injected structured context as the source of truth for grocery planning, list edits, purchased state, finished state, buying dates, and end dates.
- Use tools only when they help with arithmetic or date/time accuracy. Do not claim that you cannot manage a grocery list just because a dedicated list tool is unavailable.
- Keep answers concise, practical, and action-oriented.
- If required information is missing, ask a focused follow-up question.
- When the user asks to add, remove, update, buy, finish, or review grocery items, complete that task directly from the provided context.
"""


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def build_react_graph(settings: Settings):
    model = build_chat_model(settings).bind_tools(AGENT_TOOLS)
    tool_node = ToolNode(AGENT_TOOLS)
    system_prompt = SYSTEM_PROMPT.strip()

    def call_model(state: AgentState):
        messages = state["messages"]
        has_system_message = any(isinstance(m, SystemMessage) for m in messages)
        if not has_system_message:
            messages = [SystemMessage(content=system_prompt), *messages]
        response = model.invoke(messages)
        return {"messages": [response]}

    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile()
