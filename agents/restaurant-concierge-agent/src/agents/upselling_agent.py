from __future__ import annotations

import json
import os

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate

from llm import create_restaurant_llm
from models.order_models import Order
from prompts.upselling_prompts import UPSELLING_AGENT_PROMPT, UPSELLING_RESPONSES
from tools.validation_tools import sanitize_input


class UpsellingAgent:
    def __init__(self, llm=None):
        self.llm = llm or create_restaurant_llm(temperature=0.8)
        self.upselling_rules = self.load_upselling_rules()
        self.prompt_template = PromptTemplate(
            input_variables=["current_order", "available_upsells", "customer_input"],
            template=UPSELLING_AGENT_PROMPT,
        )
        self.upselling_chain = self.prompt_template | self.llm | StrOutputParser()

    def load_upselling_rules(self):
        try:
            rules_file_path = os.path.join(
                os.path.dirname(__file__),
                "..",
                "data",
                "upselling_rules.json",
            )
            with open(rules_file_path, "r", encoding="utf-8") as file:
                raw = json.load(file)
        except FileNotFoundError:
            return self.get_default_upselling_rules()

        if isinstance(raw, dict) and isinstance(raw.get("upselling_rules"), list):
            normalized: dict[str, list[str]] = {}
            for rule in raw["upselling_rules"]:
                if not isinstance(rule, dict):
                    continue
                item_name = str(rule.get("item", "")).strip().lower()
                suggestions = [
                    str(value).strip()
                    for value in (rule.get("upsell_items") or rule.get("suggestions") or [])
                    if str(value).strip()
                ]
                if item_name and suggestions:
                    normalized[item_name] = suggestions
            normalized["any_order"] = [
                "Masala Chai",
                "Mango Lassi",
                "Gulab Jamun",
            ]
            return normalized or self.get_default_upselling_rules()

        if isinstance(raw, dict):
            return raw
        return self.get_default_upselling_rules()

    def get_default_upselling_rules(self):
        return {
            "biryani": ["Raita", "Masala Chai", "Gulab Jamun"],
            "butter chicken": ["Butter Naan", "Jeera Rice", "Mango Lassi"],
            "paneer butter masala": ["Butter Naan", "Lachha Paratha", "Masala Chai"],
            "masala dosa": ["Medu Vada", "Filter Coffee", "Mango Lassi"],
            "salad": ["Paneer Tikka", "Masala Chai", "Roasted Papad"],
            "mains": ["Appetizer", "Dessert", "Beverage"],
            "any_order": ["Gulab Jamun", "Masala Chai", "Extra Sides"],
        }

    def suggest_upsell(self, current_order: Order):
        order_items = list(getattr(current_order, "items", []) or [])
        if not order_items:
            return "Would you like to start with one of our appetizers?"

        suggestions: list[str] = []
        order_item_names = [str(item.name).lower() for item in order_items if getattr(item, "name", "")]

        for item_name in order_item_names:
            for rule_key, rule_suggestions in self.upselling_rules.items():
                if rule_key == "any_order":
                    continue
                if rule_key.lower() in item_name:
                    suggestions.extend(rule_suggestions)

        suggestions.extend(self.upselling_rules.get("any_order", []))
        unique_suggestions = list(dict.fromkeys(suggestions))[:3]

        if unique_suggestions:
            suggestion_text = (
                f"Based on your order, I would recommend adding {', '.join(unique_suggestions)}. "
                "These pair well with what you already picked."
            )
            return suggestion_text

        return "Would you like to add a beverage or dessert to complete your meal?"

    def process_upsell_response(self, customer_response: str, suggested_items: list, current_order: Order):
        sanitized_response = sanitize_input(customer_response).lower()

        if any(word in sanitized_response for word in ["yes", "sure", "okay", "add", "include"]):
            for item in suggested_items:
                if item.lower() in sanitized_response:
                    return f"Great! I have noted that you would like to add {item}. Anything else?"
            return "Excellent. Which of the suggested items would you like to add?"

        if any(word in sanitized_response for word in ["no", "not", "don't", "skip"]):
            return UPSELLING_RESPONSES["declined_politely"].format(current_order=str(current_order))

        return (
            "I can help with that. Let me know which suggested item you want, "
            "or say no if you want to skip extras."
        )

    def generate_smart_upsell(self, current_order: Order, customer_input: str):
        sanitized_input = sanitize_input(customer_input)
        available_upsells = []

        for item in getattr(current_order, "items", []) or []:
            item_key = str(getattr(item, "name", "")).lower()
            for rule_key, suggestions in self.upselling_rules.items():
                if rule_key != "any_order" and rule_key in item_key:
                    available_upsells.extend(suggestions)

        return self.upselling_chain.invoke(
            {
                "current_order": str(current_order),
                "available_upsells": ", ".join(dict.fromkeys(available_upsells)),
                "customer_input": sanitized_input,
            }
        )

    def calculate_upsell_value(self, base_order_total: float, upsell_items: list):
        estimated_upsell_value = len(upsell_items) * 99.0
        return base_order_total + estimated_upsell_value
