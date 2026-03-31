import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from notion_agent import NotionAgent, NotionAgentError

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = FastAPI(
    title="SnitchX Notion Agent",
    description="Search, read, create, and append Notion pages.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class NotionActionRequest(BaseModel):
    taskId: str | None = None
    userId: str | None = None
    agentId: str | None = None
    action: str
    query: str | None = None
    pageId: str | None = None
    title: str | None = None
    content: str | None = None
    parentPageId: str | None = None
    limit: int | None = None
    access_token: str | None = None
    refresh_token: str | None = None


class NotionActionResponse(BaseModel):
    status: str
    type: str | None = None
    message: str | None = None
    summary: str | None = None
    page: dict | None = None
    pages: list[dict] | None = None
    error: str | None = None
    displayName: str | None = None


def _resolve_page_id(agent: NotionAgent, page_id: str | None, query: str | None) -> str:
    if page_id:
        return page_id

    search_query = (query or "").strip()
    if not search_query:
        raise NotionAgentError("Please specify which Notion page to use.")

    results = agent.search_pages(search_query, limit=1).get("pages", [])
    if not results:
        raise NotionAgentError(f"No Notion page matched '{search_query}'.")
    return results[0]["id"]


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "notion-agent"}


@app.post("/notion/action", response_model=NotionActionResponse)
def notion_action(req: NotionActionRequest) -> NotionActionResponse:
    try:
        agent = NotionAgent(req.access_token or "")
        action = req.action.strip().lower()

        if action == "search_pages":
            query = (req.query or "").strip()
            if not query:
                return NotionActionResponse(status="failed", error="query is required")
            result = agent.search_pages(query, req.limit or 10)
            pages = result["pages"]
            return NotionActionResponse(
                status="success",
                type="notion_pages",
                message=f"Found {len(pages)} Notion page(s) for '{query}'.",
                summary="\n".join(f"- {page['title']}" for page in pages[:10]) or "No pages found.",
                pages=pages,
                displayName="Notion Search",
            )

        if action == "get_page":
            page_id = _resolve_page_id(agent, req.pageId, req.query)
            result = agent.get_page(page_id)["page"]
            return NotionActionResponse(
                status="success",
                type="notion_page",
                message=f"Opened Notion page '{result['title']}'.",
                summary=result.get("content", ""),
                page=result,
                displayName=result["title"],
            )

        if action == "create_page":
            title = (req.title or "").strip()
            content = (req.content or "").strip()
            if not title or not content:
                return NotionActionResponse(
                    status="failed",
                    error="title and content are required",
                )
            result = agent.create_page(title, content, req.parentPageId)["page"]
            return NotionActionResponse(
                status="success",
                type="notion_action",
                message=f"Created Notion page '{title}'.",
                page=result,
                displayName=title,
            )

        if action == "append_to_page":
            content = (req.content or "").strip()
            if not content:
                return NotionActionResponse(status="failed", error="content is required")
            page_id = _resolve_page_id(agent, req.pageId, req.query)
            agent.append_to_page(page_id, content)
            return NotionActionResponse(
                status="success",
                type="notion_action",
                message="Appended content to the selected Notion page.",
                displayName="Updated Notion Page",
            )

        return NotionActionResponse(status="failed", error=f"Unknown action: {req.action}")
    except NotionAgentError as exc:
        return NotionActionResponse(status="failed", error=str(exc))
    except Exception as exc:
        return NotionActionResponse(status="failed", error=f"Notion agent failed: {exc}")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8400"))
    uvicorn.run(app, host="0.0.0.0", port=port)
