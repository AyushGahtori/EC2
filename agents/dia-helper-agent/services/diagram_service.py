from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import httpx

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
        return "\n.join(
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
        safe_label = re.sub(r'["{}\[\]]', "", label)[:42]
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


async def _ask_gemini(prompt: str) -> str | None:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    model = os.getenv("GEMINI_MODEL_DIAGRAM", os.getenv("GEMINI_MODEL_PRO", "gemini-2.5-pro"))
    if not api_key:
        return None

    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{endpoint}?key={api_key}", json=body)
            response.raise_for_status()
    except Exception:
        return None

    payload = response.json()
    candidates = payload.get("candidates") or []
    if not candidates:
        return None
    parts = (((candidates[0] or {}).get("content") or {}).get("parts")) or []
    texts = [str(part.get("text") or "").strip() for part in parts if isinstance(part, dict)]
    merged = "\n".join([text for text in texts if text])
    return merged or None


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
        "You help product and engineering teams turn ideas into Mermaid diagrams.",
        "Return STRICT JSON only. No markdown fences, no commentary.",
        "JSON shape:",
        "{",
        '  "title": "string",',
        '  "diagramType": "flowchart | sequenceDiagram | stateDiagram-v2 | gantt",',
        '  "summary": "short explanation",',
        '  "mermaid": "valid Mermaid code"',
        "}",
        "",
        f"- Prefer {resolved_type} unless the user clearly needs a different supported type.",
        "- Mermaid must be valid and concise.",
        "- Keep labels short and readable.",
        "- Use software, product, or workflow terminology from the supplied context.",
        "- Do not wrap JSON in markdown fences.",
    ]

    context_chunks: List[str] = []
    if project_context:
        context_chunks.append(f"Project brief:\n{_clean_text(project_context)}")
    if file_key:
        context_chunks.append(f"External reference key: {file_key}")
    if current_mermaid:
        context_chunks.append(
            "Existing Mermaid diagram that should be updated instead of recreated:\n"
            f"```mermaid\n{current_mermaid}\n```"
        )
    if edit_instruction:
        context_chunks.append(f"Update instruction:\n{_clean_text(edit_instruction)}")

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

