from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from schemas import TravelHalperActionRequest, TravelHalperActionResponse
from travel_graph_agent import TravelGraphAgent

_TRAVEL_AGENT: TravelGraphAgent | None = None


def get_travel_agent() -> TravelGraphAgent:
    global _TRAVEL_AGENT
    if _TRAVEL_AGENT is None:
        _TRAVEL_AGENT = TravelGraphAgent()
    return _TRAVEL_AGENT


def _clean(value: str | None) -> str:
    return (value or "").strip()


async def run_travel_halper_action(req: TravelHalperActionRequest) -> TravelHalperActionResponse:
    action = _clean(req.action).lower()
    plan_actions = {"plan_trip", "run_travel_halper", "run", "query", "travel_plan", "plan"}
    email_actions = {"send_email", "send_plan_email", "email", "email_plan"}

    try:
        agent = get_travel_agent()

        if action in plan_actions:
            prompt = _clean(req.prompt)
            if not prompt:
                return TravelHalperActionResponse(
                    status="needs_input",
                    type="travel_plan_result",
                    message="I need your travel request to plan flights and hotels.",
                    summary="Share destination, dates, and preferences so I can build the travel plan.",
                    result={"suggestedInputs": ["prompt"]},
                )

            thread_id = _clean(req.threadId) or str(uuid.uuid4())
            plan = agent.plan_trip(prompt, thread_id)
            return TravelHalperActionResponse(
                status="success",
                type="travel_plan_result",
                message="Travel options are ready.",
                summary="I found flight and hotel options for your trip.",
                result={
                    "threadId": thread_id,
                    "planMarkdown": plan,
                },
            )

        if action in email_actions:
            thread_id = _clean(req.threadId) or str(uuid.uuid4())
            prompt = _clean(req.prompt)
            from_email = _clean(req.senderEmail) or _clean(os.getenv("FROM_EMAIL")) or _clean(os.getenv("SMTP_USER"))
            to_email = _clean(req.receiverEmail) or _clean(os.getenv("TO_EMAIL"))
            subject = _clean(req.subject) or _clean(os.getenv("EMAIL_SUBJECT")) or "Travel Plan"

            if not to_email:
                return TravelHalperActionResponse(
                    status="needs_input",
                    type="travel_email_result",
                    message="I need the recipient email address to send the travel plan.",
                    summary="Provide receiverEmail so I can send the itinerary.",
                    result={"suggestedInputs": ["receiverEmail"]},
                )

            if not from_email:
                return TravelHalperActionResponse(
                    status="needs_input",
                    type="travel_email_result",
                    message="I need the sender email address before sending the travel plan.",
                    summary="Provide senderEmail or set FROM_EMAIL in environment.",
                    result={"suggestedInputs": ["senderEmail"]},
                )

            if not prompt and not req.threadId:
                return TravelHalperActionResponse(
                    status="needs_input",
                    type="travel_email_result",
                    message="Send action needs either threadId from a previous travel plan or a fresh prompt.",
                    summary="Provide threadId or prompt to send the travel itinerary email.",
                    result={"suggestedInputs": ["threadId", "prompt"]},
                )

            agent.send_plan_email(
                thread_id,
                from_email=from_email,
                to_email=to_email,
                subject=subject,
                prompt=prompt or None,
            )
            return TravelHalperActionResponse(
                status="success",
                type="travel_email_result",
                message="Travel plan email sent successfully.",
                summary=f"Sent travel plan to {to_email}.",
                result={
                    "threadId": thread_id,
                    "toEmail": to_email,
                    "subject": subject,
                    "sentAt": datetime.now(timezone.utc).isoformat(),
                },
            )

        return TravelHalperActionResponse(
            status="failed",
            type="travel_plan_result",
            message=f"Unsupported action: {req.action}",
            summary="Travel Halper supports plan_trip and send_plan_email actions.",
            error=f"Unknown action: {req.action}",
        )
    except ValueError as exc:
        return TravelHalperActionResponse(
            status="needs_input",
            type="travel_plan_result",
            message=str(exc),
            summary="I need one more detail to continue.",
            error=str(exc),
        )
    except Exception as exc:
        return TravelHalperActionResponse(
            status="failed",
            type="travel_plan_result",
            message="Travel Halper failed to process this request.",
            summary="Please retry with a specific travel prompt.",
            error=f"travel_halper_failed: {exc}",
        )
