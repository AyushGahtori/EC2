"""
Marketing AI Agent — LangGraph ReAct implementation.

The agent follows this cycle autonomously — NO hardcoded logic:
  THINK → PLAN → ACT (tool call) → OBSERVE → REFLECT → PERFORM

The LLM drives every decision:
  - Which tools to call
  - In what order
  - What to do when a tool fails
  - When the task is done
  - How to format the final response
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from agents.state import AgentState
from agents.tools import ALL_TOOLS
from config.settings import get_settings
from services.llm_service import get_llm

logger = logging.getLogger(__name__)

# ── System Prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are MAIA (Marketing AI Agent) — an autonomous marketing specialist.
Your role is to help create exceptional marketing content for products based on photos and user requests.

## Your ReAct Behaviour
You think and act in cycles until the user's goal is fully achieved:

1. **THINK** — Understand the user's intent deeply. What exactly do they want?
   Read between the lines. "make a poster" = generate an HTML poster. "post for ig" = Instagram post.
2. **PLAN** — Decide which tools to use and in what order.
   Always analyse the product image first if one exists and analysis isn't in context.
3. **ACT** — Call the appropriate tool with well-crafted arguments.
4. **OBSERVE** — Read the tool output carefully. Is it what the user needed?
5. **REFLECT** — Is the task complete? If not, what's the next step?
6. **PERFORM** — Deliver a polished, helpful final response.

## Intent Understanding (Examples)
- "create a poster" → ask_clarifying_questions(content_type="poster") FIRST, then research_poster_inspiration + generate_html_poster on the follow-up turn
- "change/make/update/tweak the poster's X" (when [LATEST POSTER HTML] is present) → edit_html_poster
  e.g., "make the background pink", "change headline to X", "use a serif font", "make the CTA smaller" → edit_html_poster
- "describe my product" / "write a product description" → ask_clarifying_questions(content_type="description") FIRST, then generate_marketing_copy on the follow-up turn
- "write something for Instagram" → generate_social_media_post (platform=instagram)
- "give me hashtags" → generate_hashtags
- "make a campaign" → generate_campaign_brief
- "write an ad" → generate_marketing_copy (copy_type=ad_copy)
- "LinkedIn post" → generate_social_media_post (platform=linkedin)
- "tagline" → generate_marketing_copy (copy_type=tagline)

## Clarifying-question flow (CRITICAL)
When the user's FIRST request in the session is for a poster or a product description, and they have NOT already specified a tone/style/audience/palette, you MUST:
  1. Call ask_clarifying_questions with the matching content_type ("poster" or "description").
  2. Send a one-sentence text reply like "Pick what fits and I'll get designing." Do NOT generate any content yet.
  3. STOP this turn — do not call any more tools.
When the user replies with their selections (their message will look like "Selections: direction=..., palette=..., audience=...") proceed to research_poster_inspiration + generate_html_poster / generate_marketing_copy, baking their picks into the tool arguments (poster_type/color_scheme/tone/target_audience/additional_text).
Exceptions — do NOT ask clarifying questions if:
  - The user explicitly says "just do it" / "use your best judgement" / "skip the questions".
  - Their message already includes concrete preferences (e.g., "a minimalist black-and-white poster for young adults").
  - They are EDITING an existing poster (call edit_html_poster instead).
  - You've already asked for this content type in this session.

## Poster excellence flow (CRITICAL)
For every NEW poster generation after any needed clarifying questions:
  1. Make sure you have product details. Prefer [PRODUCT CONTEXT]. If the user described the product in text (e.g., "burger poster"), create a conservative product_analysis JSON from only their words.
  2. If there is NO [PRODUCT IMAGE URL], call find_product_image with a specific product-photo query. Use `primary_url` as image_url only if it succeeds. If it fails and no image exists, generate a strong typographic/abstract poster instead or ask for an upload if the user explicitly needs the exact product photo.
  3. Always call research_poster_inspiration before generate_html_poster. Include product_analysis, product_keywords from the user's words, and the chosen poster_type.
  4. Pass the raw research_poster_inspiration JSON into generate_html_poster as design_references.
  5. Pass the uploaded [PRODUCT IMAGE URL] as image_url whenever it exists. Uploaded product photos beat web photos.
  6. After generate_html_poster, call save_generated_content(content_type="poster").

## Safe web/template inspiration
- The user may ask to "surf Google", "scrape templates", or "use internet designs". Interpret this as inspiration research, not copying.
- You may use research_poster_inspiration to find public web/template/moodboard references, but final HTML must be original.
- Never copy an exact template, protected artwork, logo, watermark, or layout one-to-one.
- Never include third-party template/reference images in the HTML poster. Use only the product image URL or CSS-created shapes.

## Editing existing content (IMPORTANT)
- When the user asks to modify a poster that already exists, you MUST call edit_html_poster — do NOT just describe the change in text, and do NOT call generate_html_poster (that starts over).
- If you see [LATEST POSTER HTML] in system context and the user asked for any visual/textual tweak, treat that as an edit request and invoke edit_html_poster with previous_html = the full HTML shown in that context block.
- After edit_html_poster runs, call save_generated_content with content_type="poster" so the next edit builds on the new version.

## Rules
- The product image is analysed for you before you run; the resulting JSON is provided under [PRODUCT CONTEXT]. Treat that as the source of truth when present.
- NEVER fabricate product details, and NEVER apologise for a "technical glitch" or "momentary issue" or say things like "the image URL is not a valid base64-encoded string" — those are hallucinated excuses. If a tool genuinely errors, say so plainly and ask the user how to proceed.
- If [PRODUCT CONTEXT] is missing and no image was uploaded this turn, call get_product_context to look for a prior analysis. If that also yields nothing but the user clearly named/described a product in text, create a conservative product_analysis JSON from their words and proceed. If they did not identify the product, ask them to upload an image or describe it.
- DO NOT call analyze_product_image yourself unless the user explicitly uploads a *new* image mid-conversation. Even then: the `image_b64` argument is a BASE64 STRING, never a URL. If you don't have the raw base64 bytes (and you never do — they aren't exposed to you as text), do NOT call this tool. The [PRODUCT IMAGE URL] value is NOT a base64 string and must never be passed as `image_b64`.
- ALWAYS call save_generated_content after creating any marketing content.
- Be creative and professional — your outputs will be used directly in real marketing.
- Adapt tone/style to match the product's market positioning (budget vs luxury, casual vs formal).

## Your final text reply (CRITICAL)
- The UI renders every tool's output separately in its own rich preview card (poster iframe, social post card, hashtag groups, etc.). The user SEES those cards — you do not need to repeat their contents.
- In your text reply you MUST NOT paste the raw HTML, CSS, JSON, or markdown source of anything a content tool returned. No ```html blocks, no <!DOCTYPE> dumps, no "here's the code:" — ever.
- Keep the text reply to 1–4 short sentences: a warm confirmation of what you made, 1–2 design/creative notes about choices, and an invitation for edits. That's it.

## Tool Calling Strategy
Think carefully about tool arguments:
- For copy_type: match exactly what the user asked for
- For tone: infer from product positioning — luxury goods = sophisticated, streetwear = bold/casual
- For social posts: always match the platform's native style
- For posters: call research_poster_inspiration first, then choose poster_type that matches the product's aesthetic and pass the research JSON into generate_html_poster.design_references

You are autonomous. Make smart decisions. The user trusts you."""


# ── Agent Graph ───────────────────────────────────────────────────────────────

def create_agent_graph():
    """Build and compile the LangGraph ReAct agent."""
    settings = get_settings()

    # LLM with tools bound — this is what makes the agent autonomous
    llm = get_llm(vision=False)
    llm_with_tools = llm.bind_tools(ALL_TOOLS)

    def agent_node(state: AgentState) -> dict:
        """
        The brain of the agent.
        Receives current state, reasons about what to do next, returns action.
        """
        messages = state["messages"]

        # Inject product context into messages if available and not already there
        product_context = state.get("product_context")

        # Build the full message list with system prompt
        full_messages = [SystemMessage(content=SYSTEM_PROMPT)]

        # Inject product context as a system note if available
        if product_context:
            full_messages.append(
                SystemMessage(content=f"[PRODUCT CONTEXT]\n{product_context}")
            )

        # Surface the uploaded product image's URL so the agent can pass it
        # to generate_html_poster (without this, the poster has a blank hero).
        product_image_url = state.get("product_image_url")
        if product_image_url:
            full_messages.append(
                SystemMessage(content=(
                    f"[PRODUCT IMAGE URL]\n{product_image_url}\n"
                    "When calling generate_html_poster, pass this exact URL as the image_url argument "
                    "so the product photo appears as the poster's hero visual."
                ))
            )

        # If there's an existing poster, make it available for surgical edits.
        latest_poster_html = state.get("latest_poster_html")
        if latest_poster_html:
            full_messages.append(
                SystemMessage(content=(
                    "[LATEST POSTER HTML — user may ask to tweak this]\n"
                    f"{latest_poster_html}\n\n"
                    "If the user asks to modify this poster (change colors, text, layout, fonts, etc.), "
                    "call edit_html_poster with previous_html set to the exact HTML above and "
                    "edit_instructions set to the user's request. Do NOT call generate_html_poster for edits "
                    "— that would throw away the existing design and start from scratch."
                ))
            )

        # Inject brand guidelines if set
        brand_guidelines = state.get("brand_guidelines")
        if brand_guidelines:
            full_messages.append(
                SystemMessage(
                    content=f"[BRAND GUIDELINES — Apply to ALL content]\n{brand_guidelines}"
                )
            )

        full_messages.extend(messages)

        # Enforce iteration limit
        iteration = state.get("iteration", 0)
        max_iterations = state.get("max_iterations", settings.agent_max_iterations)
        if iteration >= max_iterations:
            logger.warning(f"Agent hit max iterations ({max_iterations}) for session {state['session_id']}")
            # Force a final response
            from langchain_core.messages import AIMessage
            return {
                "messages": [AIMessage(content="I've completed the analysis and generated the requested content. Is there anything else you'd like me to create or modify?")],
                "iteration": iteration + 1,
                "current_phase": "responding",
            }

        # Call the LLM
        response = llm_with_tools.invoke(full_messages)

        # Determine phase based on response
        phase = "acting" if response.tool_calls else "responding"

        return {
            "messages": [response],
            "iteration": iteration + 1,
            "current_phase": phase,
        }

    def should_continue(state: AgentState) -> str:
        """Route: after agent responds, should we call tools or end?"""
        messages = state["messages"]
        last_message = messages[-1]

        # If the last message has tool calls → route to tools
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"

        # Otherwise → end (agent is done)
        return END

    # Build the graph
    tool_node = ToolNode(ALL_TOOLS)

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_node)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")

    # Compile without checkpointer — we manage state persistence ourselves via Redis/MongoDB
    return builder.compile()


# Singleton graph (compiled once, reused for all requests)
_agent_graph = None


def get_agent_graph():
    global _agent_graph
    if _agent_graph is None:
        _agent_graph = create_agent_graph()
    return _agent_graph


def reset_agent_graph() -> None:
    """Force the next request to recompile with a fresh LLM binding."""
    global _agent_graph
    _agent_graph = None


# ── Streaming Runner ─────────────────────────────────────────────────────────

async def run_agent_streaming(
    session_id: str,
    user_message: str,
    message_history: list[dict],
    product_image_b64: Optional[str] = None,
    product_image_url: Optional[str] = None,
    product_context: Optional[str] = None,
    brand_guidelines: Optional[str] = None,
    latest_poster_html: Optional[str] = None,
) -> AsyncGenerator[dict, None]:
    """
    Run the agent and yield SSE-compatible events as they happen.

    Event types yielded:
      {"type": "phase", "phase": "...", "description": "..."}
      {"type": "token", "content": "..."}
      {"type": "tool_call", "tool": "...", "args": {...}}
      {"type": "tool_result", "tool": "...", "preview": "...", "success": true}
      {"type": "content", "content_type": "...", "content": "..."}
      {"type": "done"}
      {"type": "error", "message": "..."}
    """
    settings = get_settings()
    graph = get_agent_graph()

    # Build LangChain message history
    lc_messages = []
    for msg in message_history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))

    # The main agent LLM is text-only; it cannot extract base64 from an image
    # content block to pass to analyze_product_image. So when the user uploads
    # an image on this turn, analyse it up front with the vision model and
    # inject the structured result as product_context. The agent then reasons
    # over text only — no hallucinated "technical glitch" fallbacks.
    lc_messages.append(HumanMessage(content=user_message))

    try:
        yield {"type": "phase", "phase": "thinking", "description": "Analysing your request..."}

        if product_image_b64 and not product_context:
            from agents.tools import analyze_image_async
            from services.redis_service import cache_product_analysis

            yield {"type": "phase", "phase": "acting", "description": "Analysing product image with vision AI..."}
            yield {"type": "tool_call", "tool": "analyze_product_image", "args": {}}
            analysis = await analyze_image_async(product_image_b64)
            yield {
                "type": "tool_result",
                "tool": "analyze_product_image",
                "preview": analysis[:300],
                "success": '"error"' not in analysis[:80],
            }
            product_context = analysis
            # Persist so follow-up turns reuse it instead of re-analysing or
            # (worse) prompting the LLM to call analyze_product_image with
            # whatever URL it can find, which then fails and triggers the
            # "I apologise for the error" hallucination.
            try:
                await cache_product_analysis(session_id, analysis)
            except Exception as e:
                logger.warning(f"Failed to cache product analysis for {session_id}: {e}")

    except Exception as e:
        logger.exception(f"Pre-analysis failed for session {session_id}: {e}")
        yield {"type": "error", "message": f"Product image analysis failed: {e}"}
        yield {"type": "done"}
        return

    # Initial state (product_context now populated if we pre-analysed)
    initial_state: AgentState = {
        "messages": lc_messages,
        "session_id": session_id,
        "product_image_b64": product_image_b64,
        "product_image_url": product_image_url,
        "product_context": product_context,
        "brand_guidelines": brand_guidelines,
        "latest_poster_html": latest_poster_html,
        "current_phase": "thinking",
        "plan": None,
        "observations": [],
        "iteration": 0,
        "max_iterations": settings.agent_max_iterations,
        "stream_queue": None,
    }

    try:

        # Stream events from LangGraph
        async for event in graph.astream_events(initial_state, version="v2"):
            event_name = event.get("event", "")
            event_data = event.get("data", {})
            tags = event.get("tags", [])

            # ── Token streaming from LLM ──────────────────────────────────
            if event_name == "on_chat_model_stream":
                chunk = event_data.get("chunk")
                if chunk and hasattr(chunk, "content"):
                    content = chunk.content
                    if isinstance(content, str) and content:
                        yield {"type": "token", "content": content}
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "")
                                if text:
                                    yield {"type": "token", "content": text}

            # ── Tool call started ─────────────────────────────────────────
            elif event_name == "on_tool_start":
                tool_name = event.get("name", "unknown_tool")
                tool_input = event_data.get("input", {})

                # Emit phase change
                phase_descriptions = {
                    "analyze_product_image": "Analysing product image with vision AI...",
                    "ask_clarifying_questions": "Gathering your preferences...",
                    "find_product_image": "Searching the web for a product photo...",
                    "research_poster_inspiration": "Researching poster inspiration and template patterns...",
                    "generate_marketing_copy": "Writing marketing copy...",
                    "generate_social_media_post": "Crafting social media post...",
                    "generate_html_poster": "Designing promotional poster...",
                    "edit_html_poster": "Applying your edits to the poster...",
                    "generate_hashtags": "Generating hashtag strategy...",
                    "get_product_context": "Retrieving product context...",
                    "save_generated_content": "Saving generated content...",
                    "generate_campaign_brief": "Building campaign strategy...",
                }
                description = phase_descriptions.get(tool_name, f"Using {tool_name}...")

                yield {"type": "phase", "phase": "acting", "description": description}
                yield {
                    "type": "tool_call",
                    "tool": tool_name,
                    "args": {k: str(v)[:200] for k, v in (tool_input or {}).items() if k != "image_b64"},
                }

            # ── Tool call completed ───────────────────────────────────────
            elif event_name == "on_tool_end":
                tool_name = event.get("name", "unknown_tool")
                raw_output = event_data.get("output", "")

                # LangGraph emits a ToolMessage here — its str() produces the
                # repr (content='...' name='...' tool_call_id='...'). We want
                # just the payload that the tool returned.
                output_text = getattr(raw_output, "content", None)
                if output_text is None:
                    output_text = str(raw_output) if raw_output else ""

                preview = output_text[:300]
                success = "error" not in preview.lower()[:50]

                yield {
                    "type": "tool_result",
                    "tool": tool_name,
                    "preview": preview,
                    "success": success,
                }
                yield {"type": "phase", "phase": "observing", "description": "Processing results..."}

                content_tools = {
                    "generate_html_poster": "poster",
                    "edit_html_poster": "poster",
                    "generate_social_media_post": "social_post",
                    "generate_marketing_copy": "description",
                    "generate_hashtags": "hashtags",
                    "generate_campaign_brief": "campaign",
                    "ask_clarifying_questions": "clarification",
                }
                min_len = 10 if tool_name == "ask_clarifying_questions" else 100
                if tool_name in content_tools and output_text and len(output_text) > min_len:
                    emitted = output_text
                    # Safety net: if we know the product image URL and this is a
                    # poster, guarantee the URL is inside the HTML even if the
                    # LLM forgot to pass image_url to the tool.
                    if content_tools[tool_name] == "poster":
                        try:
                            from agents.tools import _ensure_image_in_poster
                            if product_image_url:
                                emitted = _ensure_image_in_poster(emitted, product_image_url)
                            else:
                                # No uploaded image. If the poster has an external
                                # <img src="...">, adopt it as the session's image
                                # so subsequent edits/posters keep it.
                                import re as _re
                                m = _re.search(
                                    r'<img[^>]+src=["\'](https?://[^"\']+)["\']',
                                    emitted,
                                    flags=_re.IGNORECASE,
                                )
                                if m:
                                    product_image_url = m.group(1)
                        except Exception as e:
                            logger.warning(f"Image enforcement failed: {e}")
                    yield {
                        "type": "content",
                        "content_type": content_tools[tool_name],
                        "content": emitted,
                    }

        yield {"type": "phase", "phase": "responding", "description": "Preparing final response..."}
        yield {"type": "done"}

    except Exception as e:
        logger.exception(f"Agent error for session {session_id}: {e}")
        yield {"type": "error", "message": f"Agent encountered an error: {str(e)}"}
        yield {"type": "done"}


async def get_final_response(
    session_id: str,
    user_message: str,
    message_history: list[dict],
    product_image_b64: Optional[str] = None,
    product_context: Optional[str] = None,
) -> str:
    """
    Run the agent to completion and return the final text response.
    Used for non-streaming contexts.
    """
    settings = get_settings()
    graph = get_agent_graph()

    lc_messages = []
    for msg in message_history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))

    lc_messages.append(HumanMessage(content=user_message))

    # Pre-analyse image so the text-only agent LLM doesn't have to shuffle base64.
    if product_image_b64 and not product_context:
        from agents.tools import analyze_image_async
        product_context = await analyze_image_async(product_image_b64)

    initial_state: AgentState = {
        "messages": lc_messages,
        "session_id": session_id,
        "product_image_b64": product_image_b64,
        "product_image_url": None,
        "product_context": product_context,
        "brand_guidelines": None,
        "current_phase": "thinking",
        "plan": None,
        "observations": [],
        "iteration": 0,
        "max_iterations": settings.agent_max_iterations,
        "stream_queue": None,
    }

    final_state = await graph.ainvoke(initial_state)
    messages = final_state["messages"]

    # Find the last AI message
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            return str(msg.content)

    return "I've completed the task. Is there anything else you'd like me to create?"
