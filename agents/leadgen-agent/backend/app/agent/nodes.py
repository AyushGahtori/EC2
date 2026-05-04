"""
LangGraph node implementations.
Each node is a pure async function: AgentState → AgentState.
The LLM decides everything — nodes contain zero domain-specific if/else logic.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agent.prompts import PLANNER_PROMPT, REFLECTION_PROMPT, SYSTEM_PROMPT
from app.agent.state import AgentState
from app.services.llm_provider import get_llm
from app.tools import ALL_TOOLS, TOOL_MAP

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Shared LLM instance with tools bound
# ─────────────────────────────────────────────

def _get_agent_llm():
    """Return LLM with all tools bound for tool-calling."""
    llm = get_llm(streaming=False)
    return llm.bind_tools(ALL_TOOLS)


def _clean_text(value: Any) -> str:
    """Normalize text-like values used throughout lead merging."""
    if value is None:
        return ""
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.lower() in {"", "-", "--", "—", "n/a", "none", "null", "unknown"}:
            return ""
        return cleaned
    return str(value).strip()


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(_clean_text(value))
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _extract_domain(url: str) -> str:
    cleaned = _clean_text(url).lower()
    if not cleaned:
        return ""
    cleaned = re.sub(r"^https?://", "", cleaned)
    cleaned = cleaned.split("/")[0]
    return cleaned.removeprefix("www.")


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            for item in value:
                if _has_value(item):
                    return _clean_text(item)
            continue
        if _has_value(value):
            return _clean_text(value)
    return ""


def _coerce_payload(payload: Any) -> Optional[Any]:
    """Parse JSON tool arguments/results when possible."""
    if payload is None:
        return None
    if isinstance(payload, (dict, list)):
        return payload
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None
    return None


def _find_matching_lead(
    leads: list[dict[str, Any]],
    *,
    name: str = "",
    company: str = "",
    linkedin_url: str = "",
    website: str = "",
) -> Optional[dict[str, Any]]:
    """Match a lead using stable identifiers before fuzzy company/name fallbacks."""
    name_key = _clean_text(name).lower()
    company_key = _clean_text(company).lower()
    linkedin_key = _clean_text(linkedin_url).lower()
    website_key = _extract_domain(website)

    if linkedin_key:
        for lead in leads:
            if _clean_text(lead.get("linkedin_url", "")).lower() == linkedin_key:
                return lead

    if name_key and company_key:
        for lead in leads:
            if (
                _clean_text(lead.get("name", "")).lower() == name_key
                and _clean_text(lead.get("company", "")).lower() == company_key
            ):
                return lead

    if name_key:
        for lead in leads:
            if _clean_text(lead.get("name", "")).lower() == name_key:
                return lead

    if website_key:
        for lead in leads:
            if _extract_domain(str(lead.get("website", ""))) == website_key:
                return lead

    if company_key:
        for lead in leads:
            if _clean_text(lead.get("company", "")).lower() == company_key:
                return lead

    return None


def _merge_lead(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    """Merge new lead facts into an existing record without clobbering better data."""
    for key, raw_value in incoming.items():
        if not _has_value(raw_value):
            continue

        if key == "score":
            target[key] = max(_safe_int(target.get(key), 0), _safe_int(raw_value, 0))
            continue

        if key == "description":
            if len(_clean_text(raw_value)) > len(_clean_text(target.get(key, ""))):
                target[key] = _clean_text(raw_value)
            continue

        if key == "score_reasoning":
            if not _clean_text(target.get(key, "")):
                target[key] = _clean_text(raw_value)
            continue

        if key == "source":
            existing = [part.strip() for part in _clean_text(target.get("source", "")).split(",") if part.strip()]
            incoming_value = _clean_text(raw_value)
            if incoming_value and incoming_value not in existing:
                target["source"] = ", ".join(existing + [incoming_value]) if existing else incoming_value
            continue

        if not _has_value(target.get(key)):
            target[key] = _clean_text(raw_value) if isinstance(raw_value, str) else raw_value


def _upsert_lead_snapshot(leads: list[dict[str, Any]], snapshot: dict[str, Any]) -> None:
    if not _has_value(snapshot.get("name")) and not _has_value(snapshot.get("company")):
        return

    existing = _find_matching_lead(
        leads,
        name=str(snapshot.get("name", "")),
        company=str(snapshot.get("company", "")),
        linkedin_url=str(snapshot.get("linkedin_url", "")),
        website=str(snapshot.get("website", "")),
    )

    if existing:
        _merge_lead(existing, snapshot)
        return

    clean_snapshot = {
        key: (_clean_text(value) if isinstance(value, str) else value)
        for key, value in snapshot.items()
        if _has_value(value)
    }
    leads.append(clean_snapshot)


def _hydrate_lead_payload(payload: Any, leads: list[dict[str, Any]]) -> Any:
    """Merge partial lead payloads with the best known state before scoring/storage."""
    if isinstance(payload, list):
        return [_hydrate_lead_payload(item, leads) for item in payload]

    if not isinstance(payload, dict):
        return payload

    hydrated = {}
    match = _find_matching_lead(
        leads,
        name=str(payload.get("name", "") or payload.get("person_name", "")),
        company=str(payload.get("company", "") or payload.get("company_name", "")),
        linkedin_url=str(payload.get("linkedin_url", "")),
        website=str(payload.get("website", "")),
    )
    if match:
        hydrated.update(match)

    for key, value in payload.items():
        if _has_value(value):
            hydrated[key] = value

    if not _has_value(hydrated.get("email")):
        hydrated["email"] = _first_non_empty(
            hydrated.get("best_email"),
            hydrated.get("emails_found"),
            hydrated.get("email_patterns"),
        )
    if not _has_value(hydrated.get("phone")):
        hydrated["phone"] = _first_non_empty(
            hydrated.get("best_phone"),
            hydrated.get("phone_numbers"),
            hydrated.get("contact_number"),
        )
    if not _has_value(hydrated.get("company")):
        hydrated["company"] = _first_non_empty(hydrated.get("company_name"))
    if not _has_value(hydrated.get("title")):
        hydrated["title"] = _first_non_empty(hydrated.get("headline"))
    if not _has_value(hydrated.get("industry")):
        hydrated["industry"] = _first_non_empty(hydrated.get("category"))

    return hydrated


def _prepare_tool_args(
    tool_name: str,
    tool_args: dict[str, Any],
    leads: list[dict[str, Any]],
    original_query: str,
    session_id: str,
) -> dict[str, Any]:
    """Backfill missing tool arguments from the current state when the model omits them."""
    prepared = dict(tool_args)

    if tool_name == "lead_scoring":
        prepared["original_query"] = original_query
        payload = _coerce_payload(prepared.get("lead_data"))
        if payload is not None:
            prepared["lead_data"] = json.dumps(
                _hydrate_lead_payload(payload, leads),
                ensure_ascii=False,
            )

    elif tool_name == "email_finder":
        match = _find_matching_lead(
            leads,
            name=str(prepared.get("person_name", "")),
            company=str(prepared.get("company_name", "")),
            website=str(prepared.get("website", "")),
        )
        if match:
            if not _has_value(prepared.get("company_name")):
                prepared["company_name"] = match.get("company", "")
            if not _has_value(prepared.get("website")):
                prepared["website"] = match.get("website", "")

    elif tool_name == "company_enrichment":
        match = _find_matching_lead(leads, company=str(prepared.get("company_name", "")))
        if match and not _has_value(prepared.get("website")):
            prepared["website"] = match.get("website", "")

    elif tool_name == "storage":
        prepared["session_id"] = session_id
        prepared["original_query"] = original_query
        payload = _coerce_payload(prepared.get("leads"))
        if payload is not None:
            prepared["leads"] = json.dumps(
                _hydrate_lead_payload(payload, leads),
                ensure_ascii=False,
            )

    return prepared


def _update_leads_from_result(
    leads: list[dict[str, Any]],
    tool_name: str,
    tool_args: dict[str, Any],
    result_content: str,
) -> None:
    """
    Opportunistically merge tool results into state so later steps can score/store
    richer records even if the model only passes partial payloads around.
    """
    data = _coerce_payload(result_content)
    if not isinstance(data, dict):
        return

    if tool_name == "linkedin_search":
        for profile in data.get("profiles", []):
            _upsert_lead_snapshot(leads, {
                "name": profile.get("name", ""),
                "title": profile.get("title", ""),
                "company": profile.get("company", ""),
                "linkedin_url": profile.get("linkedin_url", ""),
                "source": "linkedin_search",
            })

    elif tool_name == "google_maps":
        for place in data.get("places", []):
            _upsert_lead_snapshot(leads, {
                "name": place.get("name", ""),
                "company": place.get("name", ""),
                "website": place.get("website", ""),
                "phone": place.get("phone", ""),
                "industry": place.get("category", ""),
                "source": "google_maps",
            })

    elif tool_name == "company_enrichment":
        company_name = _first_non_empty(tool_args.get("company_name"), data.get("company_name"))
        matching_company_leads = [
            lead for lead in leads
            if _clean_text(lead.get("company", "")).lower() == company_name.lower()
        ]
        enrichment = {
            "company": company_name,
            "website": data.get("website", ""),
            "industry": data.get("industry", ""),
            "company_size": data.get("company_size", ""),
            "description": data.get("description", ""),
            "source": "company_enrichment",
        }
        if matching_company_leads:
            for lead in matching_company_leads:
                _merge_lead(lead, enrichment)
        else:
            _upsert_lead_snapshot(leads, enrichment)

    elif tool_name == "email_finder":
        _upsert_lead_snapshot(leads, {
            "name": _first_non_empty(tool_args.get("person_name"), data.get("person_name")),
            "company": _first_non_empty(tool_args.get("company_name"), data.get("company")),
            "website": tool_args.get("website", ""),
            "email": _first_non_empty(data.get("best_email"), data.get("emails_found")),
            "phone": _first_non_empty(data.get("best_phone"), data.get("phone_numbers")),
            "source": "email_finder",
        })

    elif tool_name == "lead_scoring":
        payload = _coerce_payload(tool_args.get("lead_data")) or {}
        _upsert_lead_snapshot(leads, {
            "name": _first_non_empty(data.get("lead_name"), payload.get("name")),
            "company": payload.get("company", ""),
            "score": _safe_int(data.get("score"), 0),
            "score_reasoning": data.get("reasoning", ""),
            "source": "lead_scoring",
        })

    elif tool_name == "storage":
        payload = _coerce_payload(tool_args.get("leads"))
        if isinstance(payload, dict):
            payload = [payload]
        if isinstance(payload, list):
            for lead in payload:
                if isinstance(lead, dict):
                    _upsert_lead_snapshot(leads, _hydrate_lead_payload(lead, leads))


# ─────────────────────────────────────────────
# Planner Node
# ─────────────────────────────────────────────

async def planner_node(state: AgentState) -> AgentState:
    """
    The planning brain. Receives the current state, reasons about the next action,
    and emits either a tool call or a final answer message.
    LLM decides everything — no hardcoded routing logic here.
    """
    logger.info(f"[planner_node] iter={state['iteration_count']} leads={len(state['leads'])}")

    llm_with_tools = _get_agent_llm()

    # Build contextual planner prompt
    planner_context = PLANNER_PROMPT.format(
        original_query=state["original_query"],
        lead_count=len(state["leads"]),
        target_count=state["target_lead_count"],
        tools_called=", ".join(state["tools_called"][-10:]) if state["tools_called"] else "none",
        reflection=state.get("reflection") or "No reflection yet",
        iteration=state["iteration_count"],
        max_iterations=state["max_iterations"],
    )

    # Compose messages: system + conversation history + planner context injection
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]

    # Inject planner context as a hidden system nudge
    messages.append(
        HumanMessage(content=f"[AGENT CONTEXT]\n{planner_context}\n\nContinue executing the task.")
    )

    response = await llm_with_tools.ainvoke(messages)

    # Update iteration count
    new_iteration = state["iteration_count"] + 1

    return {
        **state,
        "messages": state["messages"] + [response],
        "iteration_count": new_iteration,
    }


# ─────────────────────────────────────────────
# Tool Execution Node
# ─────────────────────────────────────────────

async def tool_node(state: AgentState) -> AgentState:
    """
    Executes whatever tool the LLM called in the previous planner step.
    Uses LangGraph's ToolNode under the hood, then post-processes results
    to update leads list and tools_called tracker.
    """
    last_message = state["messages"][-1]

    if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
        logger.warning("[tool_node] No tool calls found in last message")
        return state

    tool_results = []
    new_leads = list(state["leads"])
    tools_called = list(state["tools_called"])

    for tc in last_message.tool_calls:
        tool_name = tc["name"]
        tool_args = _prepare_tool_args(
            tool_name=tool_name,
            tool_args=tc["args"],
            leads=new_leads,
            original_query=state["original_query"],
            session_id=state["session_id"],
        )
        tool_id = tc["id"]

        logger.info(f"[tool_node] Executing tool: {tool_name} args={list(tool_args.keys())}")
        tools_called.append(tool_name)

        tool_fn = TOOL_MAP.get(tool_name)
        if not tool_fn:
            result_content = json.dumps({"error": f"Unknown tool: {tool_name}"})
        else:
            try:
                # Pass session_id to storage tool automatically
                if tool_name == "storage" and "session_id" not in tool_args:
                    tool_args["session_id"] = state["session_id"]

                result_content = await tool_fn.ainvoke(tool_args)
            except Exception as e:
                logger.error(f"[tool_node] Tool {tool_name} failed: {e}")
                result_content = json.dumps({"error": str(e)})

        tool_results.append(
            ToolMessage(content=result_content, tool_call_id=tool_id, name=tool_name)
        )

        # Fold tool output back into state so later steps can build on it.
        _update_leads_from_result(new_leads, tool_name, tool_args, result_content)

    return {
        **state,
        "messages": state["messages"] + tool_results,
        "tools_called": tools_called,
        "leads": new_leads,
    }


# ─────────────────────────────────────────────
# Reflection Node
# ─────────────────────────────────────────────

async def reflection_node(state: AgentState) -> AgentState:
    """
    Self-reflection: the LLM evaluates progress and writes guidance
    for the next planning step. Returns updated state with reflection text.
    """
    logger.info(f"[reflection_node] evaluating progress...")

    llm = get_llm(streaming=False)

    # Summarize last tool result
    last_result_summary = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, ToolMessage):
            try:
                data = json.loads(msg.content)
                last_result_summary = f"Tool '{msg.name}' returned: {json.dumps(data)[:500]}"
            except Exception:
                last_result_summary = f"Tool '{msg.name}' returned: {str(msg.content)[:300]}"
            break

    prompt = REFLECTION_PROMPT.format(
        original_query=state["original_query"],
        target_count=state["target_lead_count"],
        lead_count=len(state["leads"]),
        tools_called=", ".join(state["tools_called"][-15:]),
        last_result=last_result_summary,
        iteration=state["iteration_count"],
        max_iterations=state["max_iterations"],
    )

    response = await llm.ainvoke([HumanMessage(content=prompt)])
    reflection_text = response.content if hasattr(response, "content") else str(response)

    logger.info(f"[reflection_node] reflection={reflection_text[:200]}")

    return {
        **state,
        "reflection": reflection_text,
        "messages": state["messages"] + [
            AIMessage(content=f"[REFLECTION]\n{reflection_text}")
        ],
    }


# ─────────────────────────────────────────────
# Decision Node
# ─────────────────────────────────────────────

def decision_node(state: AgentState) -> str:
    """
    Routing function: decides whether to continue the loop or end.
    Returns: "continue" | "end"

    Checks:
    1. Did reflection say COMPLETE?
    2. Have we hit max iterations?
    3. Did the last LLM message have NO tool calls (i.e., final answer)?
    """
    # Hard stop on max iterations
    if state["iteration_count"] >= state["max_iterations"]:
        logger.info("[decision_node] Max iterations reached — ending")
        return "end"

    # Check reflection text for COMPLETE signal
    reflection = state.get("reflection", "") or ""
    if "COMPLETE" in reflection.upper() and "CONTINUE" not in reflection.upper().split("COMPLETE")[0][-20:]:
        logger.info("[decision_node] Reflection says COMPLETE — ending")
        return "end"

    # Check if target lead count reached
    target = state.get("target_lead_count", 0)
    if target > 0 and len(state["leads"]) >= target:
        logger.info(f"[decision_node] Target {target} leads reached — ending")
        return "end"

    # Check if last AI message had no tool calls (LLM gave final answer)
    for msg in reversed(state["messages"]):
        if isinstance(msg, AIMessage):
            if not getattr(msg, "tool_calls", None):
                logger.info("[decision_node] No more tool calls — ending")
                return "end"
            break

    return "continue"
