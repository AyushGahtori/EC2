from __future__ import annotations

import os
import sys
import uuid

from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agents.new_coordinator_agent import NewCoordinatorAgent
from models.shared_memory import SharedMemory
from utils.console import ConsoleUI


class RestaurantAIAgent:
    def __init__(
        self,
        *,
        session_id: str | None = None,
        shared_memory: SharedMemory | None = None,
        enable_console: bool = True,
        debug_mode: bool | None = None,
    ):
        self.enable_console = enable_console
        self.ui = ConsoleUI() if enable_console else None
        self.coordinator = NewCoordinatorAgent(
            shared_memory=shared_memory,
            session_id=session_id,
        )
        self.session_id = self.coordinator.session_id
        self.debug_mode = (
            os.getenv("DEBUG_MODE", "false").lower() == "true"
            if debug_mode is None
            else bool(debug_mode)
        )

        if self.ui:
            self.ui.header("AI Bistro", "Router-powered assistant")
            self.ui.info("Commands: /help, /menu, /reset, /debug, /state, quit")

    def start_conversation(self):
        if not self.ui:
            raise RuntimeError("Interactive conversation requires console mode.")

        response, conversation_state = self.coordinator.process_user_input("hello")
        self.ui.ai_response(response)

        while True:
            try:
                user_input = input("\nYou: ").strip()
                if user_input.lower() in ["quit", "exit"]:
                    self.ui.ai_response("Thanks for visiting AI Bistro. Have a great day!")
                    break
                if not user_input:
                    self.ui.warn("I didn't catch that. Please say something.")
                    continue
                if user_input.startswith("/") and self._handle_command(user_input):
                    continue

                response, conversation_state = self.coordinator.process_user_input(user_input)
                self.ui.ai_response(response)

                if self.debug_mode:
                    self._show_debug_info(conversation_state)

                if conversation_state.get("customer_intent") == "COMPLETED":
                    details = self.get_order_details()
                    items = details.get("items", [])
                    totals = details.get("totals", {})
                    if items:
                        self.ui.rule("Order Summary")
                        self.ui.order_table(items, totals)

                    new_order = input("\nStart a new order? (y/n): ").strip().lower()
                    if new_order in ["yes", "y"]:
                        self.reset_conversation()
                        self.ui.info("Starting a new order...")
                        response, _ = self.coordinator.process_user_input("hello")
                        self.ui.ai_response(response)
                    else:
                        self.ui.ai_response("Thank you for choosing AI Bistro!")
                        break

                if conversation_state.get("needs_intervention"):
                    self.ui.warn("A human operator will assist shortly.")
            except KeyboardInterrupt:
                self.ui.ai_response("Thanks for visiting AI Bistro. Goodbye!")
                break
            except Exception:
                self.ui.error("The assistant hit a temporary issue.")
                self.ui.info("Please try again or contact support.")

    def _handle_command(self, cmd: str) -> bool:
        if not self.ui:
            return False
        name = cmd.lower().strip()
        if name in ("/help", "/h"):
            self.ui.ai_response(
                "\n".join(
                    [
                        "Available commands:",
                        "- /menu   Show the menu",
                        "- /state  Show current state",
                        "- /reset  Reset the conversation",
                        "- /debug  Toggle debug info",
                        "- quit    Exit the assistant",
                    ]
                ),
                title="Help",
            )
            return True
        if name == "/menu":
            self.ui.ai_response(self.coordinator.menu_agent.display_menu(), title="Menu")
            return True
        if name == "/reset":
            self.reset_conversation()
            self.ui.success("Conversation reset.")
            response, _ = self.coordinator.process_user_input("hello")
            self.ui.ai_response(response)
            return True
        if name == "/debug":
            self.debug_mode = not self.debug_mode
            self.ui.info(f"Debug mode: {'ON' if self.debug_mode else 'OFF'}")
            return True
        if name == "/state":
            self._show_debug_info(self.coordinator.shared_memory.to_dict())
            return True
        self.ui.warn("Unknown command. Type /help for options.")
        return True

    def _show_debug_info(self, conversation_state):
        if not self.ui:
            return
        data = {
            "Intent": conversation_state.get("customer_intent", "Unknown"),
            "Stage": conversation_state.get("conversation_stage", "Unknown"),
            "Order Items": len(conversation_state.get("current_order", [])),
            "Total": f"INR {conversation_state.get('order_total', 0):.2f}",
            "Last Agent": conversation_state.get("last_agent", "None"),
        }
        self.ui.debug_table(data, title="Debug Info")

    def process_single_request(self, user_input: str) -> str:
        response, _ = self.coordinator.process_user_input(user_input)
        return response

    def get_menu(self, category: str | None = None, dietary_filter: str | None = None) -> str:
        return self.coordinator.menu_agent.display_menu(category=category, dietary_filter=dietary_filter)

    def search_menu(self, query: str) -> str:
        return self.coordinator.menu_agent.search_menu(query)

    def get_menu_item_details(self, item_name: str) -> dict | None:
        return self.coordinator.menu_agent.get_menu_item(item_name)

    def get_recommendations(self) -> str:
        return self.coordinator.menu_agent.get_recommendations()

    def get_order_details(self) -> dict:
        conversation_state = self.coordinator.get_conversation_state()
        current_order = self.coordinator.shared_memory.current_order
        subtotal = sum(item.get("price", 0) * item.get("quantity", 1) for item in current_order)
        tax = subtotal * 0.08
        return {
            "session_id": self.session_id,
            "order_id": conversation_state.get("session_id"),
            "items": current_order,
            "totals": {
                "subtotal": subtotal,
                "tax": tax,
                "total": self.coordinator.shared_memory.order_total,
            },
            "status": conversation_state.get("customer_intent", "UNKNOWN"),
            "needs_intervention": conversation_state.get("needs_intervention", False),
            "routing_info": {
                "last_agent": conversation_state.get("last_agent"),
                "upsell_attempts": conversation_state.get("upsell_attempts", 0),
                "error_count": conversation_state.get("error_count", 0),
            },
        }

    def get_intelligent_suggestions(self, partial_input: str) -> str:
        return self.coordinator.handle_intelligent_suggestions(partial_input)

    def simulate_human_intervention(self, reason: str = "Testing intervention"):
        self.coordinator.shared_memory.trigger_human_intervention(reason)
        if self.ui:
            self.ui.warn(f"Human intervention triggered: {reason}")

    def reset_conversation(self, *, new_session_id: str | None = None):
        self.coordinator.reset_conversation()
        if new_session_id:
            self.coordinator.session_id = new_session_id
        self.session_id = self.coordinator.session_id or str(uuid.uuid4())

    def get_conversation_analytics(self) -> dict:
        state = self.coordinator.get_conversation_state()
        memory = self.coordinator.shared_memory
        duration = max(0.0, (memory.last_activity - memory.session_start).total_seconds())
        return {
            "session_info": {
                "session_id": self.session_id,
                "duration_seconds": duration,
                "total_interactions": len(memory.conversation_history),
            },
            "order_analytics": {
                "items_count": len(memory.current_order),
                "order_value": memory.order_total,
                "avg_item_price": memory.order_total / max(len(memory.current_order), 1),
            },
            "agent_usage": {
                "last_agent": state.get("last_agent"),
                "upsell_attempts": state.get("upsell_attempts", 0),
                "human_interventions": 1 if state.get("needs_intervention") else 0,
            },
            "conversation_flow": {
                "current_intent": state.get("customer_intent"),
                "current_stage": state.get("conversation_stage"),
                "errors_encountered": state.get("error_count", 0),
            },
        }


def main():
    load_dotenv()
    agent = RestaurantAIAgent()
    agent.start_conversation()


if __name__ == "__main__":
    main()
