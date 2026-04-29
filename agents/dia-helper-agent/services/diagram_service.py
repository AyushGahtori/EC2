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


def _normalize_flowchart_keyword(mermaid: str) -> str:
    cleaned = _clean_text(mermaid)
    if not cleaned:
        return cleaned
    lines = cleaned.splitlines()
    first = lines[0].strip()
    if first.startswith("graph "):
        lines[0] = first.replace("graph", "flowchart", 1)
    return "\n".join(lines)


def _extract_embedded_mermaid(text: str | None) -> str | None:
    cleaned = _clean_text(text)
    if not cleaned:
        return None

    fenced = re.search(r"```mermaid\s*([\s\S]*?)```", cleaned, flags=re.IGNORECASE)
    if fenced and fenced.group(1):
        return _normalize_flowchart_keyword(fenced.group(1))

    bare_start = re.search(
        r"(?im)^(flowchart\s+\w+|graph\s+\w+|sequenceDiagram|stateDiagram-v2|gantt)\b",
        cleaned,
    )
    if bare_start:
        return _normalize_flowchart_keyword(cleaned[bare_start.start() :])

    return None


def _is_mermaid_syntaxish_line(line: str) -> bool:
    l = line.strip().lower()
    if not l:
        return True

    prefixes = (
        "flowchart ",
        "graph ",
        "sequenceDiagram".lower(),
        "statediagram-v2",
        "gantt",
        "classdef ",
        "class ",
        "style ",
        "linkstyle ",
        "subgraph ",
        "end",
        "direction ",
    )
    if l.startswith(prefixes):
        return True
    if "-->" in l or "---" in l or "->>" in l or "-->>" in l:
        return True
    return False


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

    embedded_mermaid = _extract_embedded_mermaid(combined)
    if embedded_mermaid and not _is_structurally_empty_mermaid(embedded_mermaid):
        return embedded_mermaid

    steps = [
        _clean_text(part)
        for part in re.split(r"[\n,;\.]", combined)
        if _clean_text(part) and not _is_mermaid_syntaxish_line(part)
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

    lower_combined = combined.lower()
    labels = steps
    if len(labels) < 2:
        if "youtube" in lower_combined:
            labels = [
                "User",
                "YouTube App/Web",
                "API Gateway",
                "Metadata Service",
                "Video Storage + CDN",
            ]
        elif "netflix" in lower_combined:
            labels = [
                "User",
                "Netflix App",
                "API Gateway",
                "Recommendation Service",
                "Video CDN",
            ]
        elif any(word in lower_combined for word in ("data flow", "workflow", "diagram", "architecture")):
            labels = [
                "User Input",
                "Client App",
                "Backend API",
                "Core Services",
                "Storage/CDN",
            ]
        else:
            labels = steps or [
                "Understand request",
                "Collect project context",
                "Draft structure",
                "Generate diagram",
                "Review output",
            ]

    # Never emit an empty/blank flowchart (e.g., only "flowchart TD").
    if len(labels) < 2:
        labels = [labels[0], "Process", "Result"] if labels else ["Start", "Process", "Result"]

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


def _is_structurally_empty_mermaid(mermaid: str) -> bool:
    cleaned = _normalize_flowchart_keyword(mermaid)
    if not cleaned:
        return True

    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return True

    header = lines[0]
    normalized = "flowchart" if (header.startswith("flowchart") or header.startswith("graph")) else header
    if normalized not in SUPPORTED_DIAGRAM_TYPES:
        return False

    # At minimum, flowchart-like diagrams should have at least one edge/transition.
    if normalized == "flowchart":
        return all("-->" not in line and "---" not in line for line in lines[1:])

    if normalized == "sequenceDiagram":
        return all("->" not in line for line in lines[1:])

    if normalized == "stateDiagram-v2":
        return all("-->" not in line for line in lines[1:])

    if normalized == "gantt":
        return len(lines) <= 2

    return False


def _extract_last_flowchart_node_id(mermaid: str) -> str | None:
    edge_pairs = re.findall(
        r"([A-Za-z][A-Za-z0-9_]*)\s*--+>\s*([A-Za-z][A-Za-z0-9_]*)",
        mermaid,
    )
    if edge_pairs:
        return edge_pairs[-1][1]

    node_ids = re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)\s*[\[\(\{]", mermaid)
    return node_ids[-1] if node_ids else None


def _build_fallback_update_mermaid(
    *,
    current_mermaid: str,
    edit_instruction: str,
    diagram_type: str,
    prompt: str,
    project_context: str | None,
) -> str:
    if not current_mermaid.strip():
        return _build_fallback_mermaid(prompt, diagram_type, project_context)

    if diagram_type != "flowchart":
        # For non-flowchart diagrams, preserve old output on fallback instead of resetting.
        return current_mermaid

    current = current_mermaid.rstrip()
    instruction = _clean_text(edit_instruction).lower()
    additions: List[str] = []
    last_node = _extract_last_flowchart_node_id(current) or "A"

    has_db = "database" in current.lower() or re.search(r"\bdb\b", current.lower()) is not None
    if any(word in instruction for word in ("database", "db")) and not has_db:
        additions.extend(
            [
                "    DB[(Database)]",
                f"    {last_node} --> DB",
            ]
        )

    if any(word in instruction for word in ("detail", "detailed", "more detail", "expand")):
        additions.extend(
            [
                "    Validate[Validate Request]",
                "    Process[Process Data]",
                "    Respond[Return Response]",
                f"    {last_node} --> Validate",
                "    Validate --> Process",
                "    Process --> Respond",
            ]
        )

    if not additions:
        safe_label = re.sub(r'["{}\[\]]', "", _clean_text(edit_instruction) or "Update step")[:42]
        additions.extend(
            [
                f"    U1[{safe_label}]",
                f"    {last_node} --> U1",
            ]
        )

    return "\n".join([current, *additions])


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
    raw_text: str | None,
    fallback_type: str,
    prompt: str,
    project_context: str | None,
    current_mermaid: str | None = None,
    edit_instruction: str | None = None,
) -> str:
    cleaned = _normalize_flowchart_keyword(raw_text or "")
    if not cleaned:
        if current_mermaid and edit_instruction:
            return _build_fallback_update_mermaid(
                current_mermaid=current_mermaid,
                edit_instruction=edit_instruction,
                diagram_type=fallback_type,
                prompt=prompt,
                project_context=project_context,
            )
        return _build_fallback_mermaid(prompt, fallback_type, project_context)

    if (
        any(cleaned.startswith(prefix) for prefix in SUPPORTED_DIAGRAM_TYPES)
        or cleaned.startswith("flowchart ")
        or cleaned.startswith("graph ")
    ):
        if _is_structurally_empty_mermaid(cleaned):
            if current_mermaid and edit_instruction:
                return _build_fallback_update_mermaid(
                    current_mermaid=current_mermaid,
                    edit_instruction=edit_instruction,
                    diagram_type=fallback_type,
                    prompt=prompt,
                    project_context=project_context,
                )
            return _build_fallback_mermaid(prompt, fallback_type, project_context)
        return cleaned

    extracted_mermaid = _extract_embedded_mermaid(cleaned)
    if extracted_mermaid:
        extracted = _normalize_flowchart_keyword(extracted_mermaid)
        if _is_structurally_empty_mermaid(extracted):
            if current_mermaid and edit_instruction:
                return _build_fallback_update_mermaid(
                    current_mermaid=current_mermaid,
                    edit_instruction=edit_instruction,
                    diagram_type=fallback_type,
                    prompt=prompt,
                    project_context=project_context,
                )
            return _build_fallback_mermaid(prompt, fallback_type, project_context)
        return extracted

    if current_mermaid and edit_instruction:
        return _build_fallback_update_mermaid(
            current_mermaid=current_mermaid,
            edit_instruction=edit_instruction,
            diagram_type=fallback_type,
            prompt=prompt,
            project_context=project_context,
        )
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
    """Call Gemini for JSON-structured diagram definition. Falls back silently."""

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
    """Core business logic for dia-helper-agent.

    This mirrors the Node diagramService behavior but is self-contained for Python.
    """

    trimmed_prompt = _clean_text(prompt)
    if not trimmed_prompt and not edit_instruction:
        raise ValueError("prompt or editInstruction is required")

    resolved_type = _infer_diagram_type(trimmed_prompt or (edit_instruction or ""), diagram_type)

    # Build a rich system prompt for Gemini describing the JSON contract.
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
        # Deterministic fallback based purely on prompt/context.
        if current_mermaid and edit_instruction:
            mermaid = _build_fallback_update_mermaid(
                current_mermaid=current_mermaid,
                edit_instruction=edit_instruction,
                diagram_type=resolved_type,
                prompt=trimmed_prompt or (edit_instruction or ""),
                project_context=project_context,
            )
        else:
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
        current_mermaid=current_mermaid,
        edit_instruction=edit_instruction,
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
