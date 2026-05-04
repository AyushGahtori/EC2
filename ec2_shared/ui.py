"""Small self-contained HTML UI for EC2 agent services.

The product UI lives in the main Next app. This page is still useful when an
agent service is opened directly on EC2 for smoke tests or operations.
"""
from __future__ import annotations

import html
import json
from typing import Any


def render_agent_window(spec: dict[str, Any]) -> str:
    actions = spec.get("actions", [])
    examples = spec.get("examples", [])
    scope = spec.get("scope", [])
    usage = spec.get("usage", [])
    endpoint = spec.get("endpoint", "action")
    safe_spec = html.escape(json.dumps(spec, ensure_ascii=False))
    first_action = actions[0]["name"] if actions else "list_capabilities"

    action_cards = "\n".join(
        f"""
        <button class="action-card" data-action="{html.escape(action['name'])}">
          <span>{html.escape(action['label'])}</span>
          <small>{html.escape(action.get('description', ''))}</small>
        </button>
        """
        for action in actions
    )
    example_buttons = "\n".join(
        f"<button class=\"example\" data-prompt=\"{html.escape(example)}\">{html.escape(example)}</button>"
        for example in examples
    )
    scope_items = "\n".join(f"<li>{html.escape(item)}</li>" for item in scope)
    usage_items = "\n".join(f"<li>{html.escape(item)}</li>" for item in usage)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(spec.get('name', 'Agent'))}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #0b0d12; color: #f8fafc; }}
    main {{ display: grid; grid-template-columns: minmax(260px, 360px) minmax(0, 1fr); min-height: 100vh; }}
    aside {{ border-right: 1px solid rgba(255,255,255,.1); padding: 24px; background: #10131a; }}
    section {{ padding: 24px; }}
    h1 {{ margin: 0; font-size: 24px; }}
    h2 {{ margin: 24px 0 10px; font-size: 12px; letter-spacing: .14em; color: #9ca3af; text-transform: uppercase; }}
    p, li {{ color: #cbd5e1; line-height: 1.55; }}
    .action-card, .example, button[type=submit] {{ cursor: pointer; border: 1px solid rgba(255,255,255,.12); background: rgba(255,255,255,.06); color: #f8fafc; border-radius: 8px; padding: 10px 12px; text-align: left; }}
    .action-card {{ display: block; width: 100%; margin: 8px 0; }}
    .action-card.active {{ border-color: #8b5cf6; background: rgba(139,92,246,.18); }}
    .action-card span {{ display: block; font-weight: 700; }}
    .action-card small {{ display: block; margin-top: 4px; color: #94a3b8; line-height: 1.4; }}
    .example {{ margin: 0 8px 8px 0; }}
    form {{ display: grid; gap: 12px; max-width: 900px; }}
    label {{ display: grid; gap: 6px; color: #cbd5e1; font-size: 13px; }}
    input, textarea, select {{ width: 100%; box-sizing: border-box; border: 1px solid rgba(255,255,255,.12); border-radius: 8px; background: #090b10; color: #f8fafc; padding: 10px 12px; }}
    textarea {{ min-height: 120px; resize: vertical; }}
    .result {{ margin-top: 20px; display: grid; gap: 12px; }}
    .card {{ border: 1px solid rgba(255,255,255,.1); background: rgba(255,255,255,.05); border-radius: 8px; padding: 14px; }}
    .muted {{ color: #94a3b8; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; color: #dbeafe; }}
    @media (max-width: 820px) {{ main {{ grid-template-columns: 1fr; }} aside {{ border-right: 0; border-bottom: 1px solid rgba(255,255,255,.1); }} }}
  </style>
</head>
<body>
<main>
  <aside>
    <h1>{html.escape(spec.get('name', 'Agent'))}</h1>
    <p>{html.escape(spec.get('description', ''))}</p>
    <h2>Actions</h2>
    <div id="actions">{action_cards}</div>
    <h2>Usage</h2>
    <ul>{usage_items}</ul>
    <h2>Scope</h2>
    <ul>{scope_items}</ul>
  </aside>
  <section>
    <h2>Dedicated Agent Window</h2>
    <form id="agent-form">
      <label>Action<select id="action"></select></label>
      <div id="fields"></div>
      <button type="submit">Run Action</button>
    </form>
    <h2>Example Prompts</h2>
    <div>{example_buttons}</div>
    <div class="result" id="result"></div>
  </section>
</main>
<script id="agent-spec" type="application/json">{safe_spec}</script>
<script>
const spec = JSON.parse(document.getElementById("agent-spec").textContent);
const endpoint = {json.dumps(endpoint)};
const actionSelect = document.getElementById("action");
const fields = document.getElementById("fields");
const result = document.getElementById("result");

function activeAction() {{
  return spec.actions.find((item) => item.name === actionSelect.value) || spec.actions[0];
}}

function renderFields() {{
  const action = activeAction();
  document.querySelectorAll(".action-card").forEach((item) => item.classList.toggle("active", item.dataset.action === action.name));
  const required = action.required || [];
  const optional = action.optional || [];
  const names = [...required, ...optional];
  fields.innerHTML = names.map((name) => {{
    const isLong = ["prompt", "message", "description", "content", "transcript", "data", "log"].includes(name);
    const label = name.replaceAll("_", " ");
    return `<label>${{label}}${{required.includes(name) ? " *" : ""}}${{isLong ? `<textarea name="${{name}}"></textarea>` : `<input name="${{name}}" />`}}</label>`;
  }}).join("") || `<p class="muted">This action does not need extra fields.</p>`;
}}

function renderResult(payload) {{
  const cards = payload?.ui_payload?.cards || [];
  result.innerHTML = [
    `<div class="card"><strong>${{payload.status || "done"}}</strong><p>${{payload.summary || payload.message || ""}}</p></div>`,
    ...cards.map((card) => `<div class="card"><strong>${{card.title || "Result"}}</strong><p>${{card.body || ""}}</p><pre>${{Object.entries(card.fields || {{}}).map(([k,v]) => `${{k}}: ${{v}}`).join("\\n")}}</pre></div>`)
  ].join("");
}}

spec.actions.forEach((action) => actionSelect.add(new Option(action.label, action.name)));
actionSelect.value = {json.dumps(first_action)};
actionSelect.addEventListener("change", renderFields);
document.querySelectorAll(".action-card").forEach((button) => button.addEventListener("click", () => {{ actionSelect.value = button.dataset.action; renderFields(); }}));
document.querySelectorAll(".example").forEach((button) => button.addEventListener("click", () => {{
  const input = fields.querySelector("[name=prompt], [name=message], textarea, input");
  if (input) input.value = button.dataset.prompt;
}}));
document.getElementById("agent-form").addEventListener("submit", async (event) => {{
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const body = Object.fromEntries(form.entries());
  body.action = actionSelect.value;
  result.innerHTML = `<div class="card">Running...</div>`;
  const response = await fetch(endpoint, {{
    method: "POST",
    headers: {{ "Content-Type": "application/json" }},
    body: JSON.stringify(body),
  }});
  renderResult(await response.json());
}});
renderFields();
</script>
</body>
</html>"""
