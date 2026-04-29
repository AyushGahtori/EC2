from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from config import Config


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return _utc_now()


@dataclass
class SharedMemory:
    """Central shared memory for all agents to maintain conversation state."""

    customer_intent: str = "GREETING"
    conversation_stage: str = "greeting"
    needs_human_intervention: bool = False
    intervention_reason: str = ""

    current_order: list[dict[str, Any]] = field(default_factory=list)
    order_status: str = "IN_PROGRESS"
    order_total: float = 0.0
    order_id: str = ""

    customer_name: str = ""
    delivery_details: dict[str, str] = field(default_factory=dict)
    customer_preferences: dict[str, Any] = field(default_factory=dict)
    delivery_method: str = ""

    last_agent: str = ""
    last_action: str = ""
    conversation_history: list[dict[str, Any]] = field(default_factory=list)
    pending_clarifications: list[str] = field(default_factory=list)

    upsell_attempts: int = 0
    max_upsell_attempts: int = 2
    suggested_items: list[str] = field(default_factory=list)
    declined_suggestions: list[str] = field(default_factory=list)

    menu_displayed: bool = False
    current_category: str = ""
    browsing_preferences: dict[str, Any] = field(default_factory=dict)

    error_count: int = 0
    last_error: str = ""
    fallback_mode: bool = False

    session_start: datetime = field(default_factory=_utc_now)
    last_activity: datetime = field(default_factory=_utc_now)

    def add_to_history(self, user_input: str, agent_response: str, agent_name: str) -> None:
        self.conversation_history.append(
            {
                "timestamp": _utc_now().isoformat(),
                "user_input": user_input,
                "agent_response": agent_response,
                "agent": agent_name,
            }
        )
        self.conversation_history = self.conversation_history[-Config.MAX_HISTORY_ITEMS :]
        self.last_activity = _utc_now()
        self.last_agent = agent_name

    def add_order_item(self, item: dict[str, Any]) -> None:
        normalized = {
            "name": str(item.get("name", "")).strip(),
            "quantity": max(1, int(item.get("quantity", 1) or 1)),
            "price": float(item.get("price", 0.0) or 0.0),
            "customizations": list(item.get("customizations", []) or []),
        }
        for existing_item in self.current_order:
            if (
                existing_item.get("name") == normalized["name"]
                and existing_item.get("customizations", []) == normalized["customizations"]
            ):
                existing_item["quantity"] = int(existing_item.get("quantity", 1) or 1) + normalized["quantity"]
                self._update_order_total()
                return

        self.current_order.append(normalized)
        self._update_order_total()

    def remove_order_item(self, item_name: str) -> bool:
        for index, item in enumerate(self.current_order):
            if item.get("name", "").lower() == item_name.lower():
                del self.current_order[index]
                self._update_order_total()
                return True
        return False

    def clear_order(self) -> None:
        self.current_order.clear()
        self.order_total = 0.0
        self.order_status = "IN_PROGRESS"
        self.delivery_method = ""
        self.upsell_attempts = 0

    def _update_order_total(self) -> None:
        subtotal = 0.0
        for item in self.current_order:
            quantity = int(item.get("quantity", 1) or 1)
            price = float(item.get("price", 0.0) or 0.0)
            subtotal += quantity * price
        self.order_total = subtotal * 1.08
        self.last_activity = _utc_now()

    def set_customer_intent(self, intent: str, reason: str = "") -> None:
        self.customer_intent = intent
        self.last_action = f"Intent changed to {intent}: {reason}".strip()
        self.last_activity = _utc_now()

    def trigger_human_intervention(self, reason: str) -> None:
        self.needs_human_intervention = True
        self.intervention_reason = reason
        self.last_action = f"Human intervention requested: {reason}"
        self.last_activity = _utc_now()

    def resolve_human_intervention(self) -> None:
        self.needs_human_intervention = False
        self.intervention_reason = ""
        self.last_action = "Human intervention resolved"
        self.last_activity = _utc_now()

    def increment_error(self, error_message: str) -> None:
        self.error_count += 1
        self.last_error = error_message
        self.last_activity = _utc_now()
        if self.error_count >= 3:
            self.trigger_human_intervention(f"Multiple errors occurred: {error_message}")

    def is_order_ready_for_completion(self) -> bool:
        return (
            len(self.current_order) > 0
            and self.customer_intent in {"FINALIZING", "COMPLETED"}
            and not self.needs_human_intervention
        )

    def get_context_summary(self) -> dict[str, Any]:
        return {
            "customer_intent": self.customer_intent,
            "conversation_stage": self.conversation_stage,
            "current_order": self.current_order,
            "order_total": self.order_total,
            "order_status": self.order_status,
            "customer_name": self.customer_name,
            "last_agent": self.last_agent,
            "upsell_attempts": self.upsell_attempts,
            "needs_clarification": len(self.pending_clarifications) > 0,
            "menu_displayed": self.menu_displayed,
            "error_count": self.error_count,
            "needs_human_intervention": self.needs_human_intervention,
            "delivery_method": self.delivery_method,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "customer_intent": self.customer_intent,
            "conversation_stage": self.conversation_stage,
            "needs_human_intervention": self.needs_human_intervention,
            "intervention_reason": self.intervention_reason,
            "current_order": self.current_order,
            "order_total": self.order_total,
            "order_status": self.order_status,
            "order_id": self.order_id,
            "customer_name": self.customer_name,
            "delivery_details": self.delivery_details,
            "customer_preferences": self.customer_preferences,
            "delivery_method": self.delivery_method,
            "last_agent": self.last_agent,
            "last_action": self.last_action,
            "conversation_history": self.conversation_history[-Config.MAX_HISTORY_ITEMS :],
            "pending_clarifications": self.pending_clarifications[-20:],
            "upsell_attempts": self.upsell_attempts,
            "max_upsell_attempts": self.max_upsell_attempts,
            "suggested_items": self.suggested_items[-20:],
            "declined_suggestions": self.declined_suggestions[-20:],
            "menu_displayed": self.menu_displayed,
            "current_category": self.current_category,
            "browsing_preferences": self.browsing_preferences,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "fallback_mode": self.fallback_mode,
            "session_start": self.session_start.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "session_duration": (self.last_activity - self.session_start).total_seconds(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "SharedMemory":
        data = payload or {}
        memory = cls(
            customer_intent=str(data.get("customer_intent", "GREETING")),
            conversation_stage=str(data.get("conversation_stage", "greeting")),
            needs_human_intervention=bool(data.get("needs_human_intervention", False)),
            intervention_reason=str(data.get("intervention_reason", "")),
            current_order=list(data.get("current_order", []) or []),
            order_status=str(data.get("order_status", "IN_PROGRESS")),
            order_total=float(data.get("order_total", 0.0) or 0.0),
            order_id=str(data.get("order_id", "")),
            customer_name=str(data.get("customer_name", "")),
            delivery_details=dict(data.get("delivery_details", {}) or {}),
            customer_preferences=dict(data.get("customer_preferences", {}) or {}),
            delivery_method=str(data.get("delivery_method", "")),
            last_agent=str(data.get("last_agent", "")),
            last_action=str(data.get("last_action", "")),
            conversation_history=list(data.get("conversation_history", []) or []),
            pending_clarifications=list(data.get("pending_clarifications", []) or []),
            upsell_attempts=int(data.get("upsell_attempts", 0) or 0),
            max_upsell_attempts=int(data.get("max_upsell_attempts", 2) or 2),
            suggested_items=list(data.get("suggested_items", []) or []),
            declined_suggestions=list(data.get("declined_suggestions", []) or []),
            menu_displayed=bool(data.get("menu_displayed", False)),
            current_category=str(data.get("current_category", "")),
            browsing_preferences=dict(data.get("browsing_preferences", {}) or {}),
            error_count=int(data.get("error_count", 0) or 0),
            last_error=str(data.get("last_error", "")),
            fallback_mode=bool(data.get("fallback_mode", False)),
            session_start=_coerce_datetime(data.get("session_start")),
            last_activity=_coerce_datetime(data.get("last_activity")),
        )
        memory._update_order_total()
        return memory

    def __str__(self) -> str:
        return (
            "\nSharedMemory Status:\n"
            f"- Intent: {self.customer_intent}\n"
            f"- Stage: {self.conversation_stage}\n"
            f"- Order Items: {len(self.current_order)}\n"
            f"- Order Total: INR {self.order_total:.2f}\n"
            f"- Last Agent: {self.last_agent}\n"
            f"- Needs Intervention: {self.needs_human_intervention}\n"
            f"- Error Count: {self.error_count}\n"
        )
