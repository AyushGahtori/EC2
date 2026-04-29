from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from langchain_core.output_parsers import StrOutputParser
from langchain_core.output_parsers.pydantic import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field

from llm import create_restaurant_llm
from tools.validation_tools import sanitize_input


class RouteDecision(BaseModel):
    agent: str = Field(description="Target agent: menu, order, upselling, finalization, delivery, human")
    confidence: float = Field(description="Confidence score between 0 and 1")
    extracted_items: List[Dict[str, Any]] = Field(default_factory=list)
    user_intent: str = Field(description="Clear description of what the user wants")
    needs_clarification: bool = Field(default=False)
    clarification_question: Optional[str] = Field(default=None)
    delivery_method: Optional[str] = Field(default=None)
    wants_order_change: bool = Field(default=False)


class MultipleItemsExtraction(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    success: bool = Field(description="Whether extraction was successful")
    message: str = Field(description="Summary message about what was extracted")


class RouterAgent:
    def __init__(self, llm=None, menu_data=None):
        self.llm = llm or create_restaurant_llm(temperature=0.25)
        self.menu_data = menu_data or self._load_menu()

        self.route_parser = PydanticOutputParser(pydantic_object=RouteDecision)
        self.multi_item_parser = PydanticOutputParser(pydantic_object=MultipleItemsExtraction)

        self.routing_prompt = PromptTemplate(
            input_variables=["user_input", "conversation_context", "menu_items"],
            partial_variables={"format_instructions": self.route_parser.get_format_instructions()},
            template="""
You are an expert Router Agent for an AI restaurant assistant. Your job is to analyze user input and make intelligent routing decisions.

Available Menu Items:
{menu_items}

Current Conversation Context:
{conversation_context}

User Input: "{user_input}"

Analyze the user input and determine:

1. Intent Classification:
   - BROWSE_MENU
   - PLACE_ORDER
   - MODIFY_ORDER
   - FINALIZE_ORDER
   - DELIVERY_METHOD
   - ASK_QUESTION
   - UNCLEAR

2. Agent Routing:
   - menu
   - order
   - upselling
   - finalization
   - delivery
   - human

3. Delivery Method Detection:
   - delivery
   - pickup
   - wants_order_change if the user is editing instead of answering

4. Intelligent Matching:
   - biryani -> Chicken Biryani
   - butter chicken -> Butter Chicken
   - paneer masala -> Paneer Butter Masala
   - chai -> Masala Chai

5. Context Awareness:
   - If awaiting delivery and the user adds or changes items, route to order
   - If finalizing and they provide delivery preference, route to delivery

IMPORTANT:
- Do not include extracted_items in your response. Set extracted_items to [].
- Item extraction will be handled separately by the Order Agent.

{format_instructions}
""",
        )

        self.item_extraction_prompt = PromptTemplate(
            input_variables=["user_input", "menu_items"],
            partial_variables={"format_instructions": self.multi_item_parser.get_format_instructions()},
            template="""
You are an expert at extracting menu items from natural language input.

Available Menu Items:
{menu_items}

User Input: "{user_input}"

Extract ALL items mentioned in the user input. Use intelligent matching:
- biryani -> Chicken Biryani
- paneer masala -> Paneer Butter Masala
- chai -> Masala Chai
- lassi -> Mango Lassi
- 2 samosa -> Veg Samosa quantity 2
- dosa no onion -> Masala Dosa with customization "no onion"

For each item found, create a dictionary with:
- item_name
- quantity
- customizations
- confidence
- alternatives

{format_instructions}
""",
        )

        self.routing_chain = self.routing_prompt | self.llm | self.route_parser
        self.extraction_chain = self.item_extraction_prompt | self.llm | self.multi_item_parser

    def _load_menu(self):
        try:
            menu_file = os.path.join(os.path.dirname(__file__), "..", "data", "menu.json")
            with open(menu_file, "r", encoding="utf-8") as file_handle:
                return json.load(file_handle)
        except Exception:
            return {}

    def _format_menu_for_prompt(self):
        items = []
        if isinstance(self.menu_data, list):
            for item in self.menu_data:
                items.append(f"- {item['name']}: {item['description']} (INR {item['price']})")
        elif isinstance(self.menu_data, dict):
            for category_items in self.menu_data.values():
                if isinstance(category_items, list):
                    for item in category_items:
                        items.append(f"- {item['name']}: {item['description']} (INR {item['price']})")
        return "\n".join(items)

    def route_conversation(self, user_input: str, conversation_context: dict = None) -> RouteDecision:
        sanitized_input = sanitize_input(user_input)
        if conversation_context is None:
            conversation_context = {
                "current_order": [],
                "conversation_stage": "greeting",
                "order_total": 0.0,
            }

        try:
            route_decision: RouteDecision = self.routing_chain.invoke(
                {
                    "user_input": sanitized_input,
                    "conversation_context": json.dumps(conversation_context, indent=2),
                    "menu_items": self._format_menu_for_prompt(),
                }
            )
            if route_decision.agent == "order":
                route_decision.extracted_items = self.extract_multiple_items(sanitized_input)
            return route_decision
        except Exception as exc:
            if os.getenv("DEBUG_MODE", "false").lower() == "true":
                print(f"Router Agent error: {exc}")
            return self._fallback_routing(sanitized_input, conversation_context)

    def extract_multiple_items(self, user_input: str) -> List[Dict[str, Any]]:
        try:
            extraction_result: MultipleItemsExtraction = self.extraction_chain.invoke(
                {
                    "user_input": user_input,
                    "menu_items": self._format_menu_for_prompt(),
                }
            )
            return extraction_result.items
        except Exception as exc:
            if os.getenv("DEBUG_MODE", "false").lower() == "true":
                print(f"Item extraction error: {exc}")
            return self._manual_item_extraction(user_input)

    def _manual_item_extraction(self, user_input: str) -> List[Dict[str, Any]]:
        import re

        input_lower = user_input.lower()
        intelligent_matches = {
            "paneer tikka": {"name": "Paneer Tikka", "price": 249.0},
            "samosa": {"name": "Veg Samosa", "price": 129.0},
            "butter chicken": {"name": "Butter Chicken", "price": 399.0},
            "paneer butter masala": {"name": "Paneer Butter Masala", "price": 349.0},
            "paneer masala": {"name": "Paneer Butter Masala", "price": 349.0},
            "biryani": {"name": "Chicken Biryani", "price": 329.0},
            "dosa": {"name": "Masala Dosa", "price": 199.0},
            "kachumber": {"name": "Kachumber Salad", "price": 149.0},
            "sprout chaat": {"name": "Sprout Chaat Salad", "price": 179.0},
            "chai": {"name": "Masala Chai", "price": 79.0},
            "tea": {"name": "Masala Chai", "price": 79.0},
            "lassi": {"name": "Mango Lassi", "price": 119.0},
            "gulab jamun": {"name": "Gulab Jamun", "price": 99.0},
            "rasmalai": {"name": "Rasmalai", "price": 149.0},
        }

        quantity_patterns = [
            r"(\d+)\s+([a-zA-Z\s]+)",
            r"(one|two|three|four|five)\s+([a-zA-Z\s]+)",
        ]

        found_items: dict[str, Dict[str, Any]] = {}
        for pattern in quantity_patterns:
            matches = re.findall(pattern, input_lower)
            for qty_str, item_name in matches:
                try:
                    quantity = int(qty_str)
                except Exception:
                    quantity = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}.get(qty_str, 1)
                for key, menu_item in intelligent_matches.items():
                    if key in item_name:
                        found_items[menu_item["name"]] = {
                            "item_name": menu_item["name"],
                            "quantity": quantity,
                            "customizations": [],
                            "confidence": 0.8,
                            "alternatives": [],
                            "price": menu_item["price"],
                        }

        for key, menu_item in intelligent_matches.items():
            if key in input_lower and menu_item["name"] not in found_items:
                found_items[menu_item["name"]] = {
                    "item_name": menu_item["name"],
                    "quantity": 1,
                    "customizations": [],
                    "confidence": 0.8,
                    "alternatives": [],
                    "price": menu_item["price"],
                }

        return list(found_items.values())

    def _fallback_routing(self, user_input: str, conversation_context: dict) -> RouteDecision:
        input_lower = user_input.lower()

        if conversation_context.get("conversation_stage") == "awaiting_delivery":
            if any(word in input_lower for word in ["delivery", "deliver", "delivered"]):
                return RouteDecision(
                    agent="delivery",
                    confidence=0.8,
                    user_intent="DELIVERY_METHOD",
                    delivery_method="delivery",
                    extracted_items=[],
                    needs_clarification=False,
                )
            if any(word in input_lower for word in ["pickup", "pick up", "takeaway", "take away"]):
                return RouteDecision(
                    agent="delivery",
                    confidence=0.8,
                    user_intent="DELIVERY_METHOD",
                    delivery_method="pickup",
                    extracted_items=[],
                    needs_clarification=False,
                )
            if any(word in input_lower for word in ["add", "more", "another", "also", "want", "order"]):
                return RouteDecision(
                    agent="order",
                    confidence=0.7,
                    user_intent="MODIFY_ORDER",
                    wants_order_change=True,
                    extracted_items=self._manual_item_extraction(user_input),
                    needs_clarification=False,
                )
            if any(word in input_lower for word in ["remove", "delete", "take off", "drop", "change", "reduce"]):
                return RouteDecision(
                    agent="order",
                    confidence=0.75,
                    user_intent="MODIFY_ORDER",
                    wants_order_change=True,
                    extracted_items=self._manual_item_extraction(user_input),
                    needs_clarification=False,
                )
            if any(word in input_lower for word in ["cancel", "stop", "nevermind", "forget"]):
                return RouteDecision(
                    agent="finalization",
                    confidence=0.8,
                    user_intent="CANCEL_ORDER",
                    wants_order_change=True,
                    extracted_items=[],
                    needs_clarification=False,
                )

        if any(word in input_lower for word in ["menu", "see", "show", "what", "have"]):
            agent = "menu"
            intent = "BROWSE_MENU"
        elif any(word in input_lower for word in ["order", "want", "get", "buy", "take"]):
            agent = "order"
            intent = "PLACE_ORDER"
        elif any(word in input_lower for word in ["remove", "delete", "take off", "drop", "change", "reduce"]):
            agent = "order"
            intent = "MODIFY_ORDER"
        elif any(word in input_lower for word in ["done", "finish", "complete", "pay", "checkout"]):
            agent = "finalization"
            intent = "FINALIZE_ORDER"
        else:
            agent = "menu"
            intent = "ASK_QUESTION"

        extracted_items = []
        if agent == "order" and intent != "MODIFY_ORDER":
            extracted_items = self._manual_item_extraction(user_input)

        return RouteDecision(
            agent=agent,
            confidence=0.6,
            user_intent=intent,
            extracted_items=extracted_items,
            needs_clarification=False,
        )

    def analyze_ambiguous_input(self, user_input: str) -> Dict[str, Any]:
        ambiguity_prompt = PromptTemplate(
            input_variables=["user_input", "menu_items"],
            template="""
User said: "{user_input}"

Available menu items:
{menu_items}

This input seems ambiguous. Analyze what the user might mean and suggest clarifying questions.

Return a JSON with:
- possible_meanings
- clarifying_question
- suggested_items
""",
        )
        try:
            chain = ambiguity_prompt | self.llm | StrOutputParser()
            response_text = chain.invoke(
                {
                    "user_input": user_input,
                    "menu_items": self._format_menu_for_prompt(),
                }
            )
            return json.loads(response_text)
        except Exception:
            return {
                "possible_meanings": ["Could be menu browsing", "Could be placing an order"],
                "clarifying_question": "What would you like to do today?",
                "suggested_items": [],
            }

    def get_intelligent_suggestions(self, partial_input: str) -> List[str]:
        suggestion_prompt = PromptTemplate(
            input_variables=["partial_input", "menu_items"],
            template="""
User typed: "{partial_input}"

Menu items available:
{menu_items}

Provide up to 5 intelligent suggestions for what the user might be looking for.
""",
        )
        try:
            chain = suggestion_prompt | self.llm | StrOutputParser()
            response_text = chain.invoke(
                {
                    "partial_input": partial_input,
                    "menu_items": self._format_menu_for_prompt(),
                }
            )
            suggestions = [line.strip("- ").strip() for line in response_text.split("\n") if line.strip()]
            return suggestions[:5]
        except Exception:
            return ["Browse the menu", "See chef recommendations", "Start an order"]
