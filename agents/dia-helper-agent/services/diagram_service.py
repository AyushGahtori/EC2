from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import httpx

logger = logging.getLogger("dia-helper")

SUPPORTED_DIAGRAM_TYPES = [
    "flowchart",
    "sequenceDiagram",
    "stateDiagram-v2",
    "gantt",
]


def _clean_text(value: str | None) -> str:
    return (value or "").replace("\r", "").strip()


def _to_title_case(value: str) -> str:
    parts = re.split(r"[\s_-]+", value)
    return " ".join(part[:1].upper() + part[1:] for part in parts if part)


def _infer_diagram_type(prompt: str, diagram_type: str | None) -> str:
    if diagram_type and diagram_type in SUPPORTED_DIAGRAM_TYPES:
        return diagram_type

    lower = prompt.lower()
    if "sequence" in lower:
        return "sequenceDiagram"
    if "state" in lower:
        return "stateDiagram-v2"
    if any(keyword in lower for keyword in ("timeline", "roadmap", "gantt")):
        return "gantt"
    return "flowchart"


def _build_fallback_mermaid(prompt: str, diagram_type: str, project_context: str | None) -> str:
    combined = "\n".join(filter(None, [prompt, project_context or ""]))
    steps = [
        _clean_text(part)
        for part in re.split(r"[\n,;\.]", combined)
        if _clean_text(part)
    ][:5]

    if diagram_type == "sequenceDiagram":
        actor_a = "User"
        actor_b = "System"
        actor_c = "Output"
        return "\n".join(
            [
                "sequenceDiagram",
                f"    participant {actor_a}",
                f"    participant {actor_b}",
                f"    participant {actor_c}",
                f"    {actor_a}->>{actor_b}: {steps[0] if steps else 'Request diagram'}",
                f"    {actor_b}->>{actor_b}: Analyze project context",
                f"    {actor_b}->>{actor_c}: Generate Mermaid diagram",
                f"    {actor_c}-->>{actor_a}: Return preview",
            ]
        )

    if diagram_type == "stateDiagram-v2":
        return "\n".join(
            [
                "stateDiagram-v2",
                "    [*] --> Discover",
                "    Discover --> Plan",
                "    Plan --> Render",
                "    Render --> Review",
                "    Review --> [*]",
            ]
        )

    if diagram_type == "gantt":
        return "\n".join(
            [
                "gantt",
                "    title Project Diagram Plan",
                "    dateFormat  YYYY-MM-DD",
                "    section Diagram",
                "    Gather context           :a1, 2026-04-01, 1d",
                "    Design flow              :a2, after a1, 1d",
                "    Review output            :a3, after a2, 1d",
            ]
        )

    labels = steps or [
        "Understand request",
        "Collect project context",
        "Draft structure",
        "Generate diagram",
        "Review output",
    ]

    nodes: List[Tuple[str, str]] = []
    for index, label in enumerate(labels):
        node_id = chr(ord("A") + index)
        safe_label = re.sub(r'["{}[\]]', "", label)[:42]
        nodes.append((node_id, safe_label))

    edges = [
        f"    {nodes[i][0]}[{nodes[i][1]}] --> {nodes[i + 1][0]}[{nodes[i + 1][1]}]"
        for i in range(len(nodes) - 1)
    ]

    return "\n".join(["flowchart TD", *edges])


def _extract_json_object(text: str) -> str | None:
    cleaned = (
        _clean_text(text)
        .removeprefix("```json")
        .removeprefix("```")
        .rstrip("`")
        .strip()
    )

    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first == -1 or last == -1 or last <= first:
        return None
    return cleaned[first : last + 1]


def _ensure_mermaid(
    raw_text: str | None, fallback_type: str, prompt: str, project_context: str | None
) -> str:
    cleaned = _clean_text(raw_text)
    if not cleaned:
        return _build_fallback_mermaid(prompt, fallback_type, project_context)

    # Strip markdown fences
    cleaned = re.sub(r"^```mermaid\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    if any(cleaned.startswith(prefix) for prefix in SUPPORTED_DIAGRAM_TYPES):
        return cleaned

    fenced = re.search(r"```mermaid\s*([\s\S]*?)```", cleaned, flags=re.IGNORECASE)
    if fenced and fenced.group(1):
        return _clean_text(fenced.group(1))

    return _build_fallback_mermaid(prompt, fallback_type, project_context)


def _build_figma_prompt(title: str, mermaid: str) -> str:
    return "\n".join(
        [
            f'Create or update a FigJam diagram titled "{title}" using the Mermaid graph below.',
            "Use the Figma MCP generate_diagram workflow so the result is editable in FigJam.",
            "",
            "```mermaid",
            mermaid,
            "```",
        ]
    )


def _build_title(prompt: str, diagram_type: str) -> str:
    cleaned = _clean_text(prompt).rstrip(".?!")
    if not cleaned:
        return "Project Diagram"
    short = f"{cleaned[:57]}..." if len(cleaned) > 60 else cleaned
    suffix = "Flowchart" if diagram_type == "flowchart" else _to_title_case(
        diagram_type.replace("-v2", "")
    )
    return f"{short} - {suffix}"


@dataclass
class DiaDiagram:
    title: str
    summary: str
    diagram_type: str
    mermaid: str
    figma_prompt: str
    sources: List[Dict[str, Any]]


GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]


async def _ask_gemini(prompt: str) -> str | None:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        logger.warning("GEMINI_API_KEY is not set — skipping LLM call")
        return None

    # Use configured model first, then try fallbacks
    configured_model = os.getenv("GEMINI_MODEL_DIAGRAM", os.getenv("GEMINI_MODEL", ""))
    models_to_try = []
    if configured_model and configured_model.strip():
        models_to_try.append(configured_model.strip())
    for m in GEMINI_MODELS:
        if m not in models_to_try:
            models_to_try.append(m)

    body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ]
    }

    last_error = None
    for model in models_to_try:
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            print(f"[DIA_HELPER] Trying Gemini model {model} ...")
            logger.info("Trying Gemini model %s ...", model)
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(f"{endpoint}?key={api_key}", json=body)
                print(f"[DIA_HELPER] Model {model} response status: {response.status_code}")
                if response.status_code == 503 or response.status_code == 429:
                    print(f"[DIA_HELPER] Model {model} returned {response.status_code}, trying next...")
                    logger.warning("Model %s returned %s, trying next...", model, response.status_code)
                    last_error = f"HTTP {response.status_code}"
                    continue
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("Model %s HTTP error: %s", model, exc)
            last_error = str(exc)
            continue
        except Exception as exc:
            logger.warning("Model %s network error: %s", model, exc)
            last_error = str(exc)
            continue

        payload = response.json()
        candidates = payload.get("candidates") or []
        if not candidates:
            logger.warning("Model %s returned no candidates", model)
            last_error = "no candidates"
            continue

        parts = (((candidates[0] or {}).get("content") or {}).get("parts")) or []
        texts = [str(part.get("text") or "").strip() for part in parts if isinstance(part, dict)]
        merged = "\n".join([text for text in texts if text])
        if merged:
            logger.info("Gemini model %s succeeded (%d chars)", model, len(merged))
            return merged
        else:
            logger.warning("Model %s returned empty text", model)
            last_error = "empty text"
            continue

    logger.error("All Gemini models failed. Last error: %s", last_error)
    return None


async def generate_project_diagram(
    *,
    prompt: str,
    project_context: str | None = None,
    file_key: str | None = None,
    diagram_type: str | None = None,
    current_mermaid: str | None = None,
    edit_instruction: str | None = None,
) -> DiaDiagram:
    trimmed_prompt = _clean_text(prompt)
    if not trimmed_prompt and not edit_instruction:
        raise ValueError("prompt or editInstruction is required")

    resolved_type = _infer_diagram_type(trimmed_prompt or (edit_instruction or ""), diagram_type)

    system_prompt_lines = [
        "You are an expert software architect and diagram designer.",
        "Turn the user's description into a detailed, real-world Mermaid diagram.",
        "Return STRICT JSON only. No markdown fences, no commentary.",
        "JSON shape:",
        "{",
        '  "title": "string - descriptive title for the diagram",',
        '  "diagramType": "flowchart | sequenceDiagram | stateDiagram-v2 | gantt",',
        '  "summary": "2-3 sentence explanation of what the diagram shows",',
        '  "mermaid": "valid Mermaid code (no markdown fences, just raw mermaid syntax)"',
        "}",
        "",
        f"- Prefer {resolved_type} unless the user clearly needs a different supported type.",
        "- The Mermaid code MUST be valid and render correctly.",
        "- Do NOT wrap the mermaid value in ```mermaid fences. Just raw mermaid syntax.",
        "- Use REAL domain-specific nodes and labels, not generic placeholders.",
        "- For example, if the user asks about YouTube, use nodes like 'User', 'CDN', 'Video Server', 'Recommendation Engine', etc.",
        "- If the user asks about Netflix, use 'Client App', 'API Gateway', 'Content Delivery', 'Transcoding Service', etc.",
        "- Make the diagram detailed with at least 6-10 nodes for a good data flow.",
        "- Keep labels short but meaningful.",
        "- Do not wrap JSON in markdown fences.",
    ]

    context_chunks: List[str] = []
    if project_context:
        context_chunks.append(f"Project brief:\n{_clean_text(project_context)}")
    if file_key:
        context_chunks.append(f"External reference key: {file_key}")
    if current_mermaid:
        context_chunks.append(
            "IMPORTANT: Here is the EXISTING Mermaid diagram that you must UPDATE (do not start from scratch):\n"
            f"```mermaid\n{current_mermaid}\n```"
        )
    if edit_instruction:
        context_chunks.append(f"Update instruction from the user:\n{_clean_text(edit_instruction)}")

    user_prompt = "\n\n".join(
        [
            f"User prompt:\n{trimmed_prompt or '(update only)'}",
            context_chunks[0] if context_chunks else "No extra project context was supplied.",
            *context_chunks[1:],
        ]
    )

    raw = await _ask_gemini(
        prompt="\n".join(
            [
                "\n".join(system_prompt_lines),
                "",
                user_prompt,
            ]
        )
    )

    if not raw:
        mermaid = _build_fallback_mermaid(
            trimmed_prompt or (edit_instruction or ""), resolved_type, project_context
        )
        title = _build_title(trimmed_prompt or "Diagram", resolved_type)
        summary = "Generated a basic diagram using deterministic fallback (Gemini unavailable)."
        return DiaDiagram(
            title=title,
            summary=summary,
            diagram_type=resolved_type,
            mermaid=mermaid,
            figma_prompt=_build_figma_prompt(title, mermaid),
            sources=[
                {"type": "project_context", "hasContext": bool(project_context)},
                {"type": "external_reference", "fileKey": file_key} if file_key else {},
            ],
        )

    parsed: Dict[str, Any] | None = None
    json_candidate = _extract_json_object(raw)
    if json_candidate:
        try:
            parsed = json.loads(json_candidate)
        except Exception:
            parsed = None

    final_type = _infer_diagram_type(
        trimmed_prompt or (edit_instruction or ""), (parsed or {}).get("diagramType")
    )
    mermaid = _ensure_mermaid(
        (parsed or {}).get("mermaid") or raw,
        final_type,
        trimmed_prompt or (edit_instruction or ""),
        project_context,
    )
    title = _clean_text((parsed or {}).get("title")) or _build_title(
        trimmed_prompt or "Diagram", final_type
    )
    summary = _clean_text((parsed or {}).get("summary")) or (
        f"Generated a {final_type} from your prompt"
        + (" and project context." if project_context else ".")
    )

    sources: List[Dict[str, Any]] = []
    if project_context:
        sources.append({"type": "project_context"})
    if file_key:
        sources.append({"type": "figma_file", "fileKey": file_key})

    return DiaDiagram(
        title=title,
        summary=summary,
        diagram_type=final_type,
        mermaid=mermaid,
        figma_prompt=_build_figma_prompt(title, mermaid),
        sources=sources,
    )
