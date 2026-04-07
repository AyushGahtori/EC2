from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from schemas import DiaHelperActionRequest, DiaHelperActionResponse
from services.diagram_service import generate_project_diagram

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")

app = FastAPI(
    title="Dia Helper Agent API (EC2)",
    description=(
        "Transforms natural language project briefs into Mermaid data-flow diagrams and "
        "Figma-ready AI prompts. Deployed in the detached EC2 runtime."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    return {
        "status": "healthy",
        "agent": "dia-helper-agent",
        "version": "1.0.0",
        "gemini_key_set": bool(api_key),
        "gemini_key_prefix": api_key[:8] + "..." if api_key else "NOT SET",
    }


@app.post("/diahelper/action", response_model=DiaHelperActionResponse)
async def dia_helper_action(req: DiaHelperActionRequest) -> DiaHelperActionResponse:
    action = (req.action or "").strip().lower()

    try:
        if action not in {"generate_diagram", "update_diagram"}:
            return DiaHelperActionResponse(
                status="failed",
                type="dia_diagram",
                error=f"Unknown action: {req.action}",
                message="dia-helper-agent supports generate_diagram or update_diagram.",
                displayName="Diagram",
            )

        prompt = (req.prompt or "").strip()
        edit_instruction = (req.editInstruction or "").strip()

        if not prompt and not edit_instruction:
            return DiaHelperActionResponse(
                status="failed",
                type="dia_diagram",
                error="prompt or editInstruction is required.",
                message="Please describe the flow you want, or provide an update instruction.",
                displayName="Diagram",
            )

        diagram = await generate_project_diagram(
            prompt=prompt or edit_instruction,
            project_context=req.projectContext or "",
            file_key=req.fileKey or "",
            diagram_type=req.diagramType or None,
            current_mermaid=req.currentMermaid or None,
            edit_instruction=edit_instruction or None,
        )

        result_payload = {
            "artifactType": "diagram",
            "title": diagram.title,
            "summary": diagram.summary,
            "diagramType": diagram.diagram_type,
            "mermaid": diagram.mermaid,
            "figmaPrompt": diagram.figma_prompt,
            "sources": diagram.sources,
        }

        return DiaHelperActionResponse(
            status="success",
            type="dia_diagram",
            message="Diagram generated successfully.",
            displayName="Dia Helper",
            result=result_payload,
        )
    except ValueError as exc:
        return DiaHelperActionResponse(
            status="failed",
            type="dia_diagram",
            error=str(exc),
            message="dia-helper-agent could not understand this request.",
            displayName="Diagram",
        )
    except Exception as exc:  # pragma: no cover
        return DiaHelperActionResponse(
            status="failed",
            type="dia_diagram",
            error=f"dia-helper-agent failed: {exc}",
            message="The diagram agent ran into a problem while generating your diagram.",
            displayName="Diagram",
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8020"))
    uvicorn.run(app, host="0.0.0.0", port=port)

