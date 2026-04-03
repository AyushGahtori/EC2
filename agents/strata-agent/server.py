"""FastAPI server for strata-agent."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from schemas import StrataActionRequest, StrataActionResponse
from services.ai_service import answer_query, generate_insight_block, summarize_attachments
from services.analytics import summarize_categories, summarize_dashboard, summarize_trends
from services.firebase_store import save_query, save_report, save_snapshot
from services.fmp_client import fetch_income_statements
from services.report_service import normalize_attachments

load_dotenv()

app = FastAPI(
    title="SnitchX Strata Agent",
    description="Financial dashboard and insight agent with Firestore-native persistence.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_user(req: StrataActionRequest) -> str:
    user_id = (req.userId or "").strip()
    if not user_id:
        raise ValueError("userId is required for strata-agent actions.")
    return user_id


def _resolve_symbol(req: StrataActionRequest) -> str:
    default_symbol = (os.getenv("STRATA_DEFAULT_SYMBOL") or "AAPL").strip().upper()
    return (req.symbol or default_symbol).strip().upper()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "agent": "strata-agent", "version": "1.0.0"}


@app.post("/strata/action", response_model=StrataActionResponse)
async def strata_action(req: StrataActionRequest) -> StrataActionResponse:
    action = req.action.strip().lower()

    try:
        user_id = _require_user(req)
        symbol = _resolve_symbol(req)

        if action in {"dashboard", "load_dashboard"}:
            statements = await fetch_income_statements(symbol)
            dashboard = summarize_dashboard(symbol, statements)
            trends = summarize_trends(symbol, statements, periods=req.months or 6)
            categories = summarize_categories(symbol, statements, month=req.month)
            insight = await generate_insight_block(dashboard)
            workspace = {
                "symbol": symbol,
                "dashboard": dashboard,
                "trends": trends,
                "categories": categories,
                "insights": insight,
            }
            save_snapshot(
                user_id,
                symbol,
                req.month or 1,
                {
                    "dashboard": dashboard,
                    "trends": trends,
                    "categories": categories,
                    "insights": insight,
                },
            )
            return StrataActionResponse(
                status="success",
                type="strata_dashboard",
                message=f"Dashboard loaded for {symbol}.",
                summary=f"Revenue {dashboard['summary']['revenue']:,.0f} | Margin {dashboard['summary']['margin']}%",
                result=workspace,
                displayName="Dashboard",
            )

        if action in {"trends", "load_trends"}:
            statements = await fetch_income_statements(symbol)
            periods = req.months or 6
            trends = summarize_trends(symbol, statements, periods=periods)
            save_snapshot(user_id, symbol, req.month or 1, {"trends": trends})
            return StrataActionResponse(
                status="success",
                type="strata_trends",
                message=f"Trend analysis loaded for {symbol}.",
                summary="Trend and forecast generated.",
                result=trends,
                displayName="Trends",
            )

        if action in {"categories", "load_categories"}:
            statements = await fetch_income_statements(symbol)
            categories = summarize_categories(symbol, statements, month=req.month)
            save_snapshot(user_id, symbol, req.month or 1, {"categories": categories})
            return StrataActionResponse(
                status="success",
                type="strata_categories",
                message=f"Category breakdown loaded for {symbol}.",
                summary="Expense categories generated from latest statement.",
                result=categories,
                displayName="Categories",
            )

        if action in {"ai_insights", "generate_insights", "insights"}:
            statements = await fetch_income_statements(symbol)
            dashboard = summarize_dashboard(symbol, statements)
            insight = await generate_insight_block(dashboard)
            payload = {"symbol": symbol, "dashboard": dashboard, "insight": insight}
            save_snapshot(user_id, symbol, req.month or 1, {"insights": payload})
            return StrataActionResponse(
                status="success",
                type="strata_insights",
                message=f"AI insights generated for {symbol}.",
                summary=insight["insight"],
                result=payload,
                displayName="AI Insights",
            )

        if action in {"query", "ask", "ask_strata"}:
            question = (req.question or "").strip()
            if not question:
                return StrataActionResponse(status="failed", error="question is required for query action.")
            statements = await fetch_income_statements(symbol)
            dashboard = summarize_dashboard(symbol, statements)
            context: dict[str, Any] = {"symbol": symbol, "summary": dashboard["summary"], "comparison": dashboard["comparison"]}
            if req.context:
                context.update(req.context)
            answer = await answer_query(question, context)
            save_query(user_id, symbol, question, answer, context)
            return StrataActionResponse(
                status="success",
                type="strata_query",
                message="Query answered.",
                summary=answer,
                result={"question": question, "answer": answer, "context": context},
                displayName="Ask Strata",
            )

        if action in {"upload_report", "analyze_report"}:
            raw_attachments = [attachment.model_dump() for attachment in (req.attachments or [])]
            processed, failed = normalize_attachments(raw_attachments)
            report_name = (req.reportName or "Uploaded Report").strip()

            if len(processed) == 0:
                message = "No readable files were found in this upload batch."
                save_report(user_id, report_name, "failed", message, processed, failed)
                return StrataActionResponse(
                    status="failed",
                    type="strata_upload",
                    error=message,
                    result={"processedFiles": processed, "failedFiles": failed},
                    displayName="Upload Report",
                )

            summary = await summarize_attachments(report_name, processed)
            status = "success" if len(failed) == 0 else "partial_success"
            save_report(user_id, report_name, status, summary, processed, failed)
            return StrataActionResponse(
                status="success",
                type="strata_upload",
                message="Report processed." if len(failed) == 0 else "Report processed with partial file failures.",
                summary=summary,
                result={
                    "reportName": report_name,
                    "processedFiles": processed,
                    "failedFiles": failed,
                    "partialFailures": len(failed) > 0,
                },
                displayName="Upload Report",
            )

        if action in {"workspace", "open_workspace"}:
            statements = await fetch_income_statements(symbol)
            dashboard = summarize_dashboard(symbol, statements)
            trends = summarize_trends(symbol, statements, periods=req.months or 6)
            categories = summarize_categories(symbol, statements, month=req.month)
            insight = await generate_insight_block(dashboard)
            workspace = {
                "symbol": symbol,
                "dashboard": dashboard,
                "trends": trends,
                "categories": categories,
                "insights": insight,
            }
            save_snapshot(user_id, symbol, req.month or 1, {"workspace": workspace})
            return StrataActionResponse(
                status="success",
                type="strata_workspace",
                message=f"Strata workspace loaded for {symbol}.",
                summary=insight["insight"],
                result=workspace,
                displayName="Strata",
            )

        return StrataActionResponse(status="failed", error=f"Unknown action: {req.action}")
    except ValueError as exc:
        return StrataActionResponse(status="failed", error=str(exc))
    except Exception as exc:
        return StrataActionResponse(
            status="failed",
            error=f"strata-agent failed: {exc}",
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8012")))
