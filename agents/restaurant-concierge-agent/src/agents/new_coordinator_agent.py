from __future__ import annotations

import os
import uuid
from typing import Any, Dict, Tuple

from agents.menu_agent import MenuAgent
from agents.order_agent import OrderAgent
from agents.router_agent import RouteDecision, RouterAgent
from agents.upselling_agent import UpsellingAgent
from llm import create_restaurant_llm
from models.shared_memory import SharedMemory
from tools.validation_tools import sanitize_input


class NewCoordinatorAgent:
    """
    Router-first restaurant coordinator.
    All user inputs go through the Router Agent before execution.
    """

    def __init__(self, *, shared_memory: SharedMemory | None = None, session_id: str | None = None):
        self.llm = create_restaurant_llm()
        self.shared_memory = shared_memory or SharedMemory()
        self.router_agent = RouterAgent(llm=self.llm)
        self.menu_agent = MenuAgent(llm=self.llm)
        self.order_agent = OrderAgent(llm=self.llm, shared_memory=self.shared_memory)
        self.upselling_agent = UpsellingAgent(llm=self.llm)
        self.session_id = session_id or str(uuid.uuid4())

        if os.getenv("DEBUG_MODE", "false").lower() == "true":
            print("Router-based Restaurant AI Agent initialized.")

    def process_user_input(self, user_input: str) -> Tuple[str, Dict[str, Any]]:
        try:
            if self._is_cancel_intent(user_input, None):
                response = self._handle_cancel_request(user_input, None)
                self.shared_memory.add_to_history(user_input, response, "coordinator")
                return response, self.shared_memory.to_dict()

            conversation_context = self.shared_memory.get_context_summary()
            route_decision = self.router_agent.route_conversation(user_input, conversation_context)

            if self._is_cancel_intent(user_input, route_decision):
                response = self._handle_cancel_request(user_input, route_decision)
                self.shared_memory.add_to_history(user_input, response, "coordinator")
                return response, self.shared_memory.to_dict()

            if route_decision.agent == "human" or self.shared_memory.needs_human_intervention:
                response = self._handle_human_intervention(user_input, route_decision)
                self.shared_memory.add_to_history(user_input, response, "human")
                return response, self.shared_memory.to_dict()

            response = self._execute_agent_action(user_input, route_decision)
            self.shared_memory.add_to_history(user_input, response, route_decision.agent)
            response = self._post_process_response(response, route_decision)
            return response, self.shared_memory.to_dict()
        except Exception as exc:
            self.shared_memory.increment_error(str(exc))
            return (
                "I hit a temporary issue while processing that request. Please try again or rephrase it.",
                self.shared_memory.to_dict(),
            )

    def _execute_agent_action(self, user_input: str, route_decision: RouteDecision) -> str:
        if route_decision.needs_clarification:
            self.shared_memory.pending_clarifications.append(route_decision.clarification_question or "")
            return route_decision.clarification_question or "Could you clarify what you are looking for?"

        if route_decision.agent == "menu":
            return self._handle_menu_request(user_input)
        if route_decision.agent == "order":
            return self._handle_order_request(user_input, route_decision)
        if route_decision.agent == "upselling":
            return self._handle_upselling_request()
        if route_decision.agent == "finalization":
            return self._handle_finalization_request(route_decision)
        if route_decision.agent == "delivery":
            return self._handle_delivery_request(user_input, route_decision)
        return self._handle_menu_request(user_input)

    def _handle_menu_request(self, user_input: str) -> str:
        self.shared_memory.set_customer_intent("BROWSING", "User browsing menu")
        lowered = user_input.lower()
        if "menu" in lowered or "show" in lowered:
            self.shared_memory.menu_displayed = True
            return self.menu_agent.display_menu()
        if "recommend" in lowered:
            return self.menu_agent.get_recommendations()
        return self.menu_agent.handle_menu_query(user_input)

    def _handle_order_request(self, user_input: str, route_decision: RouteDecision) -> str:
        self.shared_memory.set_customer_intent("ORDERING", "User placing or modifying order")

        if route_decision.user_intent == "MODIFY_ORDER":
            modification_result = self.order_agent.handle_order_modification(user_input)
            if self.shared_memory.conversation_stage == "awaiting_delivery":
                return f"{modification_result}\n\nShall we proceed with delivery or pickup?"
            return modification_result

        if route_decision.extracted_items:
            result = self.order_agent.process_order_with_extracted_items(
                user_input,
                route_decision.extracted_items,
            )
            if result.success:
                response_parts = [result.message]
                if result.added_items:
                    order_summary = self.order_agent.get_order_summary()
                    response_parts.append(f"\nCurrent order total: INR {order_summary['totals']['total']:.2f}")
                if result.failed_items:
                    failed_names = [item.get("requested_name", "") for item in result.failed_items]
                    response_parts.append(f"\nWarning: I could not find {', '.join(failed_names)}.")
                    response_parts.append("Would you like to see the menu for available items?")
                return "\n".join(response_parts)
            return result.message

        return "I would be happy to take your order. What would you like from the menu?"

    def _handle_upselling_request(self) -> str:
        if not self.shared_memory.current_order:
            return "Would you like to add something to your order?"
        if self.shared_memory.upsell_attempts >= self.shared_memory.max_upsell_attempts:
            return "Would you like anything else, or should we proceed with your order?"

        order_items = []
        for item in self.shared_memory.current_order:
            mock_item = type(
                "MockItem",
                (),
                {
                    "name": item.get("name", ""),
                    "price": item.get("price", 0),
                    "quantity": item.get("quantity", 1),
                },
            )()
            order_items.append(mock_item)

        mock_order = type("MockOrder", (), {"items": order_items})()
        try:
            upsell_response = self.upselling_agent.suggest_upsell(mock_order)
            self.shared_memory.upsell_attempts += 1
            return upsell_response
        except Exception:
            return "Would you like to add any drinks or sides to complete your order?"

    def _handle_finalization_request(self, route_decision: RouteDecision) -> str:
        if route_decision.wants_order_change and route_decision.user_intent == "CANCEL_ORDER":
            self.shared_memory.clear_order()
            self.shared_memory.set_customer_intent("GREETING", "Order cancelled by user")
            self.shared_memory.conversation_stage = "greeting"
            return "Your order has been cancelled. Would you like to start a new order?"

        self.shared_memory.set_customer_intent("FINALIZING", "User finalizing order")
        validation = self.order_agent.validate_order_completion()
        if not validation["ready"]:
            return validation["message"]

        order_summary = self.order_agent.get_order_summary()
        response_parts = [
            "FINAL ORDER CONFIRMATION",
            "",
            "Here is your complete order:",
            "",
        ]

        for item in order_summary["items"]:
            if item["quantity"] == 1:
                response_parts.append(f"- {item['name']} - INR {item['unit_price']:.2f}")
            else:
                response_parts.append(
                    f"- {item['quantity']}x {item['name']} - INR {item['unit_price']:.2f} each"
                )
            if item["customizations"]:
                response_parts.append(f"  Customizations: {', '.join(item['customizations'])}")

        response_parts.extend(
            [
                "",
                f"Total: INR {order_summary['totals']['total']:.2f} (includes tax)",
                "",
                "Would you like this delivered or would you prefer pickup?",
            ]
        )
        self.shared_memory.conversation_stage = "awaiting_delivery"
        self.shared_memory.set_customer_intent("DELIVERY_METHOD", "Waiting for delivery method choice")
        return "\n".join(response_parts)

    def _handle_delivery_request(self, user_input: str, route_decision: RouteDecision) -> str:
        if route_decision.wants_order_change:
            if route_decision.user_intent == "MODIFY_ORDER":
                modification_result = self.order_agent.handle_order_modification(user_input)
                self.shared_memory.conversation_stage = "awaiting_delivery"
                self.shared_memory.set_customer_intent("DELIVERY_METHOD", "Waiting for delivery method choice")
                return f"{modification_result}\n\nShall we proceed with delivery or pickup?"
            if route_decision.user_intent == "CANCEL_ORDER":
                self.shared_memory.clear_order()
                self.shared_memory.set_customer_intent("GREETING", "Order cancelled by user")
                self.shared_memory.conversation_stage = "greeting"
                return "Your order has been cancelled. Would you like to start a new order?"

        if route_decision.delivery_method:
            self.shared_memory.delivery_method = route_decision.delivery_method
            self.shared_memory.conversation_stage = "completed"
            self.shared_memory.set_customer_intent(
                "COMPLETED",
                f"Order completed with {route_decision.delivery_method}",
            )
            if route_decision.delivery_method == "delivery":
                return "\n".join(
                    [
                        "Perfect. Your order will be delivered.",
                        "",
                        "ORDER SUMMARY",
                        f"- Order Total: INR {self.shared_memory.order_total:.2f}",
                        "- Delivery Method: Delivery",
                        "- Estimated Delivery Time: 30-45 minutes",
                        "",
                        "Thank you for your order. We will start preparing it right away.",
                    ]
                )
            return "\n".join(
                [
                    "Great. Your order will be ready for pickup.",
                    "",
                    "ORDER SUMMARY",
                    f"- Order Total: INR {self.shared_memory.order_total:.2f}",
                    "- Pickup Method: Pickup",
                    "- Estimated Pickup Time: 15-20 minutes",
                    "",
                    "Thank you for your order. We will start preparing it right away.",
                ]
            )

        return (
            "I did not quite catch that. Please say delivery or pickup, "
            "or tell me if you want to add or change something first."
        )

    def _handle_human_intervention(self, user_input: str, route_decision: RouteDecision) -> str:
        return "\n".join(
            [
                "This request needs a human teammate.",
                f"Request: {user_input}",
                f"Reason: {self.shared_memory.intervention_reason or 'Complex request requiring human assistance'}",
                "",
                "I can still help with the menu or simple ordering while that gets reviewed.",
            ]
        )

    def _post_process_response(self, response: str, route_decision: RouteDecision) -> str:
        if (
            route_decision.agent == "order"
            and self.shared_memory.current_order
            and self.shared_memory.upsell_attempts < self.shared_memory.max_upsell_attempts
        ):
            import random

            response += random.choice(
                [
                    "\n\nWould you like to add any drinks or sides?",
                    "\n\nHow about an appetizer to go with that?",
                    "\n\nAny beverages to pair with your order?",
                ]
            )
            self.shared_memory.upsell_attempts += 1
        return response

    def _is_cancel_intent(self, user_input: str, route_decision: RouteDecision | None) -> bool:
        if getattr(route_decision, "user_intent", None) == "CANCEL_ORDER":
            return True

        text = sanitize_input(user_input).lower().strip()
        simple_cancel_words = ["cancel", "stop", "quit", "exit", "nevermind", "never mind", "forget it"]
        if text in simple_cancel_words:
            return True

        cancel_phrases = [
            "cancel order",
            "cancel my order",
            "cancel the order",
            "i don't want to order anymore",
            "i don't want to order any more",
            "i don't want this order",
            "nevermind the order",
            "never mind the order",
            "forget the order",
            "void the order",
            "stop the order",
            "end the order",
            "no thanks",
            "not interested",
        ]
        return any(phrase in text for phrase in cancel_phrases)

    def _handle_cancel_request(self, user_input: str, route_decision: RouteDecision | None) -> str:
        if not self.shared_memory.current_order:
            self.shared_memory.set_customer_intent("GREETING", "User requested cancel with no active order")
            self.shared_memory.conversation_stage = "greeting"
            return "There is no active order to cancel. Would you like to start a new order or see the menu?"

        self.shared_memory.clear_order()
        self.shared_memory.set_customer_intent("GREETING", "Order cancelled by user")
        self.shared_memory.conversation_stage = "greeting"
        return "Your order has been cancelled. Would you like to start a new order or see the menu?"

    def reset_conversation(self):
        self.shared_memory = SharedMemory()
        self.order_agent.shared_memory = self.shared_memory
        self.session_id = str(uuid.uuid4())
        if os.getenv("DEBUG_MODE", "false").lower() == "true":
            print("Conversation reset.")

    def get_conversation_state(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "customer_intent": self.shared_memory.customer_intent,
            "conversation_stage": self.shared_memory.conversation_stage,
            "order_items": len(self.shared_memory.current_order),
            "order_total": self.shared_memory.order_total,
            "last_agent": self.shared_memory.last_agent,
            "needs_intervention": self.shared_memory.needs_human_intervention,
            "upsell_attempts": self.shared_memory.upsell_attempts,
            "error_count": self.shared_memory.error_count,
        }

    def handle_intelligent_suggestions(self, partial_input: str) -> str:
        suggestions = self.router_agent.get_intelligent_suggestions(partial_input)
        if not suggestions:
            return "I am not sure what you are looking for. Would you like to see the menu?"

        response_parts = ["You might be looking for:", ""]
        for index, suggestion in enumerate(suggestions, 1):
            response_parts.append(f"{index}. {suggestion}")
        response_parts.append("\nWhich one interests you?")
        return "\n".join(response_parts)
