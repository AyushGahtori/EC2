from __future__ import annotations

import datetime
import operator
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from tools.flights_finder import flights_finder
from tools.hotels_finder import hotels_finder

_ = load_dotenv()

CURRENT_YEAR = datetime.datetime.now().year


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]


TOOLS_SYSTEM_PROMPT = f"""You are a smart travel agency assistant.
Use tools to look up flights and hotels whenever needed.
The current year is {CURRENT_YEAR}.

Rules for responses:
- Include flight and hotel options with clear booking links.
- For each flight, include Google Flights, MakeMyTrip, Skyscanner, and Kayak links when available.
- Include airline and hotel logos when available in tool output.
- Show all prices in INR with the Rs. prefix.
- Keep output concise but practical for booking decisions.
"""

EMAILS_SYSTEM_PROMPT = """Convert the travel plan text into valid HTML email body.
Output HTML only, no markdown fences.
"""

TOOLS = [flights_finder, hotels_finder]


class TravelGraphAgent:
    def __init__(self):
        self._tools = {tool.name: tool for tool in TOOLS}
        self._tools_model = (
            os.getenv("TRAVEL_HALPER_MODEL")
            or os.getenv("OLLAMA_MODEL_CLOUD")
            or "qwen3.5:397b-cloud"
        ).strip()
        self._email_model = (
            os.getenv("TRAVEL_HALPER_EMAIL_MODEL")
            or self._tools_model
        ).strip()

        self._tools_llm = ChatOllama(model=self._tools_model).bind_tools(TOOLS)

        builder = StateGraph(AgentState)
        builder.add_node("call_tools_llm", self.call_tools_llm)
        builder.add_node("invoke_tools", self.invoke_tools)
        builder.add_node("email_sender", self.email_sender)
        builder.set_entry_point("call_tools_llm")
        builder.add_conditional_edges(
            "call_tools_llm",
            TravelGraphAgent.exists_action,
            {"more_tools": "invoke_tools", "email_sender": "email_sender"},
        )
        builder.add_edge("invoke_tools", "call_tools_llm")
        builder.add_edge("email_sender", END)

        memory = MemorySaver()
        self.graph = builder.compile(checkpointer=memory, interrupt_before=["email_sender"])

    @staticmethod
    def exists_action(state: AgentState) -> str:
        result = state["messages"][-1]
        tool_calls = getattr(result, "tool_calls", [])
        return "more_tools" if len(tool_calls) > 0 else "email_sender"

    def call_tools_llm(self, state: AgentState):
        messages = [SystemMessage(content=TOOLS_SYSTEM_PROMPT)] + state["messages"]
        message = self._tools_llm.invoke(messages)
        return {"messages": [message]}

    def invoke_tools(self, state: AgentState):
        tool_calls = getattr(state["messages"][-1], "tool_calls", [])
        results = []
        for tool_call in tool_calls:
            tool_name = tool_call.get("name")
            if tool_name not in self._tools:
                result = "Invalid tool name from model. Retry with a valid tool call."
            else:
                result = self._tools[tool_name].invoke(tool_call.get("args", {}))
            results.append(
                ToolMessage(
                    tool_call_id=tool_call.get("id", "unknown"),
                    name=tool_name or "unknown",
                    content=str(result),
                )
            )
        return {"messages": results}

    def email_sender(self, state: AgentState):
        if not state.get("messages"):
            raise ValueError("Cannot send email because no travel plan is available.")

        email_llm = ChatOllama(model=self._email_model, temperature=0.1)
        source_text = str(state["messages"][-1].content)
        email_message = [
            SystemMessage(content=EMAILS_SYSTEM_PROMPT),
            HumanMessage(content=source_text),
        ]
        email_response = email_llm.invoke(email_message)
        html_body = str(email_response.content)

        smtp_host = (os.environ.get("SMTP_HOST") or "").strip()
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        smtp_user = (os.environ.get("SMTP_USER") or "").strip()
        smtp_password = (os.environ.get("SMTP_PASSWORD") or "").strip()
        from_email = (os.environ.get("FROM_EMAIL") or "").strip()
        to_email = (os.environ.get("TO_EMAIL") or "").strip()
        subject = (os.environ.get("EMAIL_SUBJECT") or "Travel Plan").strip()

        missing = [
            name
            for name, value in {
                "SMTP_HOST": smtp_host,
                "SMTP_USER": smtp_user,
                "SMTP_PASSWORD": smtp_password,
                "FROM_EMAIL": from_email,
                "TO_EMAIL": to_email,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(f"Missing email configuration: {', '.join(missing)}")

        msg = EmailMessage()
        msg["From"] = from_email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content("Your travel plan is attached as HTML. View in an HTML-capable mail client.")
        msg.add_alternative(html_body, subtype="html")

        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ssl.create_default_context()) as server:
                server.login(smtp_user, smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls(context=ssl.create_default_context())
                server.login(smtp_user, smtp_password)
                server.send_message(msg)

    def plan_trip(self, prompt: str, thread_id: str) -> str:
        result = self.graph.invoke(
            {"messages": [HumanMessage(content=prompt)]},
            config={"configurable": {"thread_id": thread_id}},
        )
        return str(result["messages"][-1].content)

    def send_plan_email(
        self,
        thread_id: str,
        *,
        from_email: str,
        to_email: str,
        subject: str,
        prompt: str | None = None,
    ) -> None:
        os.environ["FROM_EMAIL"] = from_email
        os.environ["TO_EMAIL"] = to_email
        os.environ["EMAIL_SUBJECT"] = subject

        config = {"configurable": {"thread_id": thread_id}}

        if prompt and prompt.strip():
            self.graph.invoke({"messages": [HumanMessage(content=prompt.strip())]}, config=config)

        self.graph.invoke(None, config=config)
