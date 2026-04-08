from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from schemas import ShopGenieActionRequest, ShopGenieActionResponse
from services.shopgenie_service import run_shopgenie

app = FastAPI(
    title="ShopGenie Agent API",
    description="Shopping recommendation agent with product comparison, YouTube review lookup, and optional Google email send.",
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
def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "agent": "shopgenie-agent",
        "version": "1.0.0",
    }


@app.post("/shopgenie/action", response_model=ShopGenieActionResponse)
async def shopgenie_action(req: ShopGenieActionRequest) -> ShopGenieActionResponse:
    action = (req.action or "").strip().lower()

    if action not in {"recommend_product", "shop_search", "run_shopgenie"}:
        return ShopGenieActionResponse(
            status="failed",
            type="shopgenie_result",
            error=f"Unknown action: {req.action}",
            message="shopgenie-agent supports recommend_product, shop_search, or run_shopgenie.",
            displayName="ShopGenie",
        )

    query = (req.query or req.prompt or "").strip()
    if not query:
        return ShopGenieActionResponse(
            status="failed",
            type="shopgenie_result",
            error="query is required",
            message="Please provide what product you want to research.",
            displayName="ShopGenie",
        )

    try:
        payload = await run_shopgenie(
            query=query,
            user_id=(req.userId or "").strip() or None,
            budget=(req.budget or "").strip() or None,
            recipient_email=(req.recipientEmail or "").strip() or None,
            send_email=bool(req.sendEmail),
        )
        return ShopGenieActionResponse(**payload)
    except ValueError as exc:
        return ShopGenieActionResponse(
            status="failed",
            type="shopgenie_result",
            error=str(exc),
            message="ShopGenie could not process this request.",
            displayName="ShopGenie",
        )
    except Exception as exc:  # pragma: no cover
        return ShopGenieActionResponse(
            status="failed",
            type="shopgenie_result",
            error=f"shopgenie-agent failed: {exc}",
            message="ShopGenie ran into a problem while generating your recommendation.",
            displayName="ShopGenie",
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8021"))
    uvicorn.run(app, host="0.0.0.0", port=port)
